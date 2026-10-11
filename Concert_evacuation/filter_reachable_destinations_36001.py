#!/usr/bin/env python3
"""Filter destination roads reachable within a fixed time limit.

What this script does:
1. Connect simulator engine once.
2. Read candidate destination roads from a CSV.
3. Use one fixed origin road, inject 50 vehicles every 300 simulator seconds.
4. Stop tracking a vehicle once it exceeds 3600 seconds from planned departure.
5. Output how many destinations are reachable within the threshold.
"""

from __future__ import annotations
import argparse
import asyncio
import copy
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 版本标识：服务器上可以用 grep 检查，确认传的是 3600 秒目的地筛选版。
SCRIPT_VERSION = "filter_reachable_destinations_3600_20260423"

# 脚本所在目录。默认报告 JSON 会保存到这里，避免写死到别的工程目录。
SCRIPT_DIR = Path(__file__).resolve().parent

# 服务器上的候选目的地 CSV。你把约 3000 个 D 放到这个文件后，脚本可不传参数直接跑。
# CSV 中只要包含 D_Road_ID / destination_road / end_road / d / home_road / road_id 之一即可。
DEFAULT_DESTINATION_CSV = "/home/nkk/work/src/python/concert_evacuation/result/smart_od_trips.csv"

# =========================
# 常用实验配置区
# =========================
# 本轮筛选使用的固定起点 O。换接驳点时直接改这里即可。
FILTER_ORIGIN_ROAD = "200002061"

# 最多读取多少个目的地。None 表示读取全部。
MAX_DESTINATIONS = 30

# 需要跳过的异常 OD 索引，避免已知导致 started_time 为空的任务影响测时。
SKIP_OD_INDEXES = {15, 19}

# 每一批最多发出的车辆数。
BATCH_SIZE = 10

# 接驳车发车间隔，单位是模拟器秒。300 秒就是 5 分钟。
HEADWAY_SECONDS = 30

# 单车从 planned_departure_time 起，超过该阈值还没完成就放弃追踪。
VEHICLE_TIMEOUT_SECONDS = 3600

# 每次模拟步查询车辆位置时的并发数。服务器压力大时可通过 --query_concurrency 调小。
DEFAULT_QUERY_CONCURRENCY = 50

# 每推进多少个模拟步查询一次车辆位置。V3 默认 10 秒精度，明显减少位置查询量。
DEFAULT_TRACK_EVERY_STEPS = 10

# 终端只展示前 N 行车辆明细；完整 rows 仍写入 report_json。
DEFAULT_ROWS_PRINT_LIMIT = 20

# 服务器上 trafficlight_rl 通常在当前脚本的上级目录里。
# 把上级目录加入 import 搜索路径后，才能导入 trafficlight_rl.engine。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

print("RUNNING_SCRIPT =", __file__)
print("BATCH_SIZE =", BATCH_SIZE)
print("MAX_DESTINATIONS =", MAX_DESTINATIONS)
print("HEADWAY_SECONDS =", HEADWAY_SECONDS)
print("VEHICLE_TIMEOUT_SECONDS =", VEHICLE_TIMEOUT_SECONDS)

@dataclass
class DispatchTask:
    """一辆待疏散车辆的计划任务。"""

    od_index: int
    source_person_id: Optional[str]
    start_road: str
    end_road: str
    hub_id: str
    hub_trip_index: Optional[int]
    seat_index_in_trip: Optional[int]
    planned_departure_time: Optional[int]


@dataclass
class ProbeState:
    """单个 OD 探针车的运行状态，最后会转换成结果 JSON 的一行。"""

    od_index: int
    source_person_id: Optional[str]
    start_road: str
    end_road: str
    hub_id: Optional[str]
    hub_trip_index: Optional[int]
    seat_index_in_trip: Optional[int]
    planned_departure_time: Optional[int]
    start_lane_id: Optional[int]
    end_lane_id: Optional[int]
    person_id: Optional[int]
    departure_time: Optional[int]
    started_time: Optional[int]
    arrived_time: Optional[int]
    timeout_time: Optional[int]
    eta_seconds: Optional[int]
    arrival_reason: str
    last_road: Optional[str]
    tracking_error_count: int
    last_tracking_error: Optional[str]


class Env:
    """对模拟器 engine 做一层简单封装。"""

    def __init__(
        self,
        mongo_uri: str,
        map_db: str,
        map_coll: str,
        agent_db: str,
        agent_coll: str,
        start_step: int,
        step_size: int,
        step_count: int,
    ) -> None:
        # 只有真实连接模拟器时才导入，避免 --help 阶段依赖服务器运行环境。
        from trafficlight_rl.engine import get_engine

        # get_engine 会连接/启动模拟器。map_* 是地图集合，agent_* 是 person/vehicle 集合。
        self.eng = get_engine(
            mongo_uri=mongo_uri,
            map_db=map_db,
            map_coll=map_coll,
            agent_db=agent_db,
            agent_coll=agent_coll,
            start_step=start_step,
            total_step=(step_count + 1) * step_size,
            use_max_pressure=True,
            output_sql_dsn="",
            output_job_prefix="addperson_multi_od_smoke_",
            output_bbox=None,
        )
        self.step_size = step_size

    async def step_once(self) -> None:
        # 推进一次模拟器时间。默认 interval=30，即每次推进 30 秒。
        self.eng.next_step(self.step_size)


def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    """把 Mongo 里的 person 模板整理成 AddPerson 接口可接受的字段。"""

    # 有些模板字段是 snake_case，AddPerson 接口需要 camelCase。
    field_map = {
        "vehicle_attribute": "vehicleAttribute",
        "bus_attribute": "busAttribute",
        "pedestrian_attribute": "pedestrianAttribute",
        "bike_attribute": "bikeAttribute",
        "output_when_sleep": "outputWhenSleep",
    }
    normalized: Dict[str, Any] = {}
    for key, value in person.items():
        normalized[field_map.get(key, key)] = value

    # 新增 person 时不能复用模板 id/base，否则可能和已有车辆冲突。
    normalized.pop("id", None)
    normalized.pop("base", None)

    # 只保留 AddPerson 相关字段，避免把 Mongo 文档的无关字段传给模拟器。
    allowed = {
        "attribute",
        "home",
        "schedules",
        "vehicleAttribute",
        "busAttribute",
        "pedestrianAttribute",
        "bikeAttribute",
        "labels",
        "profile",
        "work",
        "outputWhenSleep",
        "type",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def load_person_template_from_mongo(mongo_uri: str, agent_db: str, agent_coll: str) -> Dict[str, Any]:
    """从 MongoDB 读取一个已有 person，作为 AddPerson 的模板。"""

    # 只有真实读取 Mongo 时才导入，避免本地查看 --help 时缺 pymongo 报错。
    import pymongo

    client = pymongo.MongoClient(mongo_uri)
    try:
        # 原脚本逻辑：取集合里的第一条文档，并使用其中 data 字段。
        doc = client[agent_db][agent_coll].find_one({}, {"_id": 0})
        if not doc or not isinstance(doc.get("data"), dict):
            raise RuntimeError("Cannot find valid person template in Mongo collection")
        template = normalize_person_for_add(copy.deepcopy(doc["data"]))
        if not template:
            raise RuntimeError("Template becomes empty after normalization")
        return template
    finally:
        client.close()


def first_existing_value(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    """按多个候选字段名读取 OD 数据，兼容不同 JSON 命名。"""

    for key in keys:
        value = row.get(key)
        if value is not None and str(value) != "":
            return value
    return None


def load_destination_rows_from_csv(
    destination_csv: str,
    max_destinations: Optional[int],
) -> List[Dict[str, Any]]:
    """读取候选目的地 CSV。

    兼容以下常见字段名：
    - D_Road_ID
    - destination_road
    - end_road
    - d
    - home_road
    - road_id
    """

    csv_path = Path(destination_csv).expanduser().resolve()
    if not csv_path.exists():
        raise RuntimeError(f"Destination CSV not found: {csv_path}")
    if max_destinations is not None and max_destinations <= 0:
        raise RuntimeError("MAX_DESTINATIONS must be greater than 0 when provided")

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"Destination CSV has no header: {csv_path}")

        rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            if max_destinations is not None and len(rows) >= max_destinations:
                break

            end_road = first_existing_value(
                row,
                ["D_Road_ID", "destination_road", "end_road", "d", "home_road", "road_id"],
            )
            if end_road is None:
                raise RuntimeError(
                    f"Destination CSV row #{idx} must contain D_Road_ID/destination_road/end_road/d/home_road/road_id"
                )

            rows.append(
                {
                    "od_index": idx,
                    "source_person_id": first_existing_value(
                        row,
                        ["Person_ID", "person_id", "personId", "user_id", "id"],
                    ),
                    "end_road": str(end_road),
                    "raw": row,
                }
            )

    if not rows:
        raise RuntimeError(f"Destination CSV contains no rows: {csv_path}")
    return rows


def build_filter_tasks_from_destination_csv(
    destination_csv: str,
    origin_road: str,
    batch_size: int,
    max_destinations: Optional[int],
) -> Tuple[List[DispatchTask], Dict[str, Any]]:
    """固定一个 O，把 CSV 里的每个 D 转成一辆候选筛选车。"""

    if batch_size <= 0:
        raise RuntimeError("--batch_size must be greater than 0")

    rows = load_destination_rows_from_csv(
        destination_csv=destination_csv,
        max_destinations=max_destinations,
    )
    origin_road = str(origin_road)
    tasks: List[DispatchTask] = []
    for row in rows:
        od_index = int(row["od_index"])
        if od_index in SKIP_OD_INDEXES:
            print(f"Skipping known bad OD index: {od_index}, destination={row['end_road']}")
            continue
        batch_index = len(tasks) // batch_size
        seat_index = len(tasks) % batch_size + 1
        tasks.append(
            DispatchTask(
                od_index=od_index,
                source_person_id=(
                    str(row["source_person_id"])
                    if row.get("source_person_id") is not None
                    else None
                ),
                start_road=origin_road,
                end_road=row["end_road"],
                hub_id=origin_road,
                hub_trip_index=batch_index,
                seat_index_in_trip=seat_index,
                planned_departure_time=None,
            )
        )

    metadata = {
        "mode": "filter_destinations_from_csv",
        "destination_csv": str(Path(destination_csv).expanduser().resolve()),
        "origin_road": origin_road,
        "max_destinations": max_destinations,
        "total_destination_count": len(tasks),
        "batch_size": batch_size,
        "batch_count": (len(tasks) + batch_size - 1) // batch_size,
        "max_batch_index": max((task.hub_trip_index or 0 for task in tasks), default=0),
    }
    return tasks, metadata


def assign_planned_departure_times(
    tasks: List[DispatchTask],
    first_departure_time: int,
    headway_seconds: int,
) -> None:
    """给正式疏散任务写入每辆车的计划发车时间。"""

    if headway_seconds <= 0:
        raise RuntimeError("--headway_seconds must be greater than 0")

    for task in tasks:
        trip_index = task.hub_trip_index or 0
        task.planned_departure_time = int(first_departure_time + trip_index * headway_seconds)


async def resolve_lane_with_cache(env: Env, road_id: str, lane_cache: Dict[str, int]) -> int:
    """带缓存地把 road_id 转成 lane_id，避免同一个起点被重复查询很多次。"""

    road_id = str(road_id)
    if road_id in lane_cache:
        return lane_cache[road_id]

    lanes = await env.eng.get_lane_id_of_road(road_id)
    if not lanes:
        raise RuntimeError(f"Cannot resolve lane ids for road: {road_id}")

    lane_id = int(lanes[0])
    lane_cache[road_id] = lane_id
    return lane_id


def build_probe_person_auto_route(
    template: Dict[str, Any],
    start_lane_id: int,
    end_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    """构造 AddPerson 的 person payload。

    关键字段：
    - home.lane_position：车辆出发位置。
    - schedules[0].departure_time：车辆计划出发的模拟器绝对时间。
    - trips[0].end.lane_position：车辆目的地位置。
    - routes=[]：不手写路线，让模拟器自动寻路。
    """

    person = copy.deepcopy(template)
    person["home"] = {
        "lane_position": {
            "lane_id": int(start_lane_id),
            "s": 1.0,
        }
    }
    person["schedules"] = [
        {
            "departure_time": int(departure_time),
            "loop_count": 1,
            "trips": [
                {
                    "mode": "TRIP_MODE_DRIVE_ONLY",
                    "end": {
                        "lane_position": {
                            "lane_id": int(end_lane_id),
                            "s": 1.0,
                        }
                    },
                    "routes": [],
                }
            ],
        }
    ]
    return person


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter destination roads reachable from one fixed origin within 3600 seconds."
    )
    parser.add_argument("--mongo_uri", default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017")
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")

    parser.add_argument(
        "--destination_csv",
        default=DEFAULT_DESTINATION_CSV,
        help="CSV containing candidate destination road ids. Default: DEFAULT_DESTINATION_CSV in code.",
    )
    parser.add_argument(
        "--origin_road",
        default=FILTER_ORIGIN_ROAD,
        help="Fixed origin road id. If omitted, FILTER_ORIGIN_ROAD in code is used.",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=BATCH_SIZE,
        help="Vehicles injected per batch.",
    )
    parser.add_argument(
        "--headway_seconds",
        type=int,
        default=HEADWAY_SECONDS,
        help="Simulator seconds between two batches.",
    )
    parser.add_argument(
        "--vehicle_timeout_seconds",
        type=int,
        default=VEHICLE_TIMEOUT_SECONDS,
        help="Give up tracking one vehicle after this many seconds from planned departure.",
    )

    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument(
        "--max_probe_steps",
        type=int,
        default=None,
        help="Max simulator steps. Default covers all batches plus vehicle_timeout_seconds and 10 minutes buffer.",
    )
    parser.add_argument(
        "--query_concurrency",
        type=int,
        default=DEFAULT_QUERY_CONCURRENCY,
        help="Concurrent get_road_id_with_vehicle queries per tracking tick.",
    )
    parser.add_argument(
        "--track_every_steps",
        type=int,
        default=DEFAULT_TRACK_EVERY_STEPS,
        help="Query vehicle locations every N simulator steps. Default is 10 seconds.",
    )
    parser.add_argument(
        "--report_json",
        default=str(SCRIPT_DIR / "reachable_destinations_3600_report.json"),
        help="Save full filtering report JSON.",
    )
    parser.add_argument(
        "--rows_print_limit",
        type=int,
        default=DEFAULT_ROWS_PRINT_LIMIT,
        help="Print only the first N result rows to terminal. Use 0 to suppress row printing.",
    )
    parser.add_argument(
        "--progress_print_every",
        type=int,
        default=200,
        help="Print one progress line every N injected vehicles. Use 0 to disable progress logs.",
    )
    return parser.parse_args()


async def inject_one_probe(
    env: Env,
    person_template: Dict[str, Any],
    task: DispatchTask,
    lane_cache: Dict[str, int],
    departure_time: int,
) -> ProbeState:
    """动态批次模式下，为单个 D 注入一辆车。"""

    state = ProbeState(
        od_index=task.od_index,
        source_person_id=task.source_person_id,
        start_road=str(task.start_road),
        end_road=str(task.end_road),
        hub_id=task.hub_id,
        hub_trip_index=task.hub_trip_index,
        seat_index_in_trip=task.seat_index_in_trip,
        planned_departure_time=departure_time,
        start_lane_id=None,
        end_lane_id=None,
        person_id=None,
        departure_time=None,
        started_time=None,
        arrived_time=None,
        timeout_time=None,
        eta_seconds=None,
        arrival_reason="not_injected",
        last_road=None,
        tracking_error_count=0,
        last_tracking_error=None,
    )

    try:
        start_lane_id = await resolve_lane_with_cache(env, state.start_road, lane_cache)
        end_lane_id = await resolve_lane_with_cache(env, state.end_road, lane_cache)
        state.start_lane_id = start_lane_id
        state.end_lane_id = end_lane_id
        payload = build_probe_person_auto_route(
            template=person_template,
            start_lane_id=start_lane_id,
            end_lane_id=end_lane_id,
            departure_time=departure_time,
        )
        add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
        person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
        if not isinstance(person_id, int):
            raise RuntimeError(f"AddPerson failed: {add_resp}")

        state.person_id = person_id
        state.departure_time = departure_time
        state.arrival_reason = "not_arrived"
    except Exception as exc:
        state.arrival_reason = f"inject_exception: {exc}"

    return state


async def run_dynamic_destination_filter(
    env: Env,
    person_template: Dict[str, Any],
    tasks: List[DispatchTask],
    first_departure_time: int,
    headway_seconds: int,
    vehicle_timeout_seconds: int,
    max_probe_steps: int,
    query_concurrency: int,
    track_every_steps: int,
    progress_print_every: int,
) -> Tuple[List[ProbeState], Dict[str, Any]]:
    """动态注入候选 D，并筛出 3600 秒内可达的目的地。"""

    if query_concurrency <= 0:
        raise RuntimeError("--query_concurrency must be greater than 0")
    if track_every_steps <= 0:
        raise RuntimeError("--track_every_steps must be greater than 0")
    if vehicle_timeout_seconds <= 0:
        raise RuntimeError("--vehicle_timeout_seconds must be greater than 0")

    assign_planned_departure_times(tasks, first_departure_time, headway_seconds)
    tasks = sorted(
        tasks,
        key=lambda task: (
            task.planned_departure_time if task.planned_departure_time is not None else 0,
            task.seat_index_in_trip if task.seat_index_in_trip is not None else 0,
        ),
    )

    diagnostics: Dict[str, Any] = {
        "sim_steps_executed": 0,
        "tracking_ticks_executed": 0,
        "vehicle_query_count": 0,
        "injected_count": 0,
        "timeout_count": 0,
        "early_stop_reason": None,
        "track_every_steps": track_every_steps,
        "max_probe_steps": max_probe_steps,
        "vehicle_timeout_seconds": vehicle_timeout_seconds,
        "progress_print_every": progress_print_every,
        "first_departure_time": first_departure_time,
        "last_sim_time": None,
    }

    states: List[ProbeState] = []
    active_by_person_id: Dict[int, ProbeState] = {}
    lane_cache: Dict[str, int] = {}
    next_task_index = 0
    semaphore = asyncio.Semaphore(query_concurrency)

    async def query_current_road(state: ProbeState) -> Tuple[ProbeState, Optional[str], Optional[str]]:
        try:
            async with semaphore:
                current_road = await env.eng.get_road_id_with_vehicle(state.person_id)
            current_road_str = str(current_road) if current_road is not None else None
            return state, current_road_str, None
        except Exception as exc:
            return state, None, str(exc)

    async def inject_due_batches(now_t: int) -> None:
        nonlocal next_task_index
        while next_task_index < len(tasks):
            task = tasks[next_task_index]
            if task.planned_departure_time is None or task.planned_departure_time > now_t:
                break
            state = await inject_one_probe(
                env=env,
                person_template=person_template,
                task=task,
                lane_cache=lane_cache,
                departure_time=int(task.planned_departure_time),
            )
            states.append(state)
            diagnostics["injected_count"] += 1
            if isinstance(state.person_id, int):
                active_by_person_id[state.person_id] = state
            next_task_index += 1
            if progress_print_every > 0 and diagnostics["injected_count"] % progress_print_every == 0:
                reachable_count = sum(1 for item in states if item.eta_seconds is not None)
                resolved_count = sum(1 for item in states if item.arrival_reason != "not_arrived")
                print(
                    "[progress] "
                    f"sim_t={now_t} "
                    f"injected={diagnostics['injected_count']}/{len(tasks)} "
                    f"resolved={resolved_count} "
                    f"reachable={reachable_count} "
                    f"timeout={diagnostics['timeout_count']} "
                    f"active={len(active_by_person_id)}"
                )

    try:
        for step_index in range(max_probe_steps):
            sim_clock = await env.eng.get_current_time()
            now_t = int(sim_clock["t"])
            diagnostics["last_sim_time"] = now_t

            await inject_due_batches(now_t)

            # 超过阈值的车辆直接放弃追踪，不计入可达结果。
            for person_id, state in list(active_by_person_id.items()):
                if state.departure_time is not None and now_t - state.departure_time > vehicle_timeout_seconds:
                    state.timeout_time = now_t
                    state.arrival_reason = "vehicle_timeout"
                    active_by_person_id.pop(person_id, None)
                    diagnostics["timeout_count"] += 1

            should_query = (step_index % track_every_steps == 0) or (step_index == max_probe_steps - 1)
            if should_query and active_by_person_id:
                diagnostics["tracking_ticks_executed"] += 1
                active_states = list(active_by_person_id.values())
                diagnostics["vehicle_query_count"] += len(active_states)
                query_results = await asyncio.gather(*(query_current_road(state) for state in active_states))

                for state, current_road_str, query_error in query_results:
                    if query_error is not None:
                        state.tracking_error_count += 1
                        state.last_tracking_error = query_error
                        continue

                    if state.started_time is None and current_road_str is not None:
                        state.started_time = now_t

                    if state.started_time is not None and current_road_str == state.end_road:
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "reach_destination_road"
                        active_by_person_id.pop(state.person_id, None)
                        continue

                    if state.started_time is not None and current_road_str is None and state.last_road is not None:
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "disappeared_after_departure"
                        active_by_person_id.pop(state.person_id, None)
                        continue

                    state.last_road = current_road_str

            if next_task_index >= len(tasks) and not active_by_person_id:
                diagnostics["early_stop_reason"] = "all_destinations_resolved"
                break

            await env.step_once()
            diagnostics["sim_steps_executed"] += 1
        else:
            diagnostics["early_stop_reason"] = "max_probe_steps_reached"
    finally:
        for state in states:
            if isinstance(state.person_id, int) and state.arrival_reason == "not_arrived":
                state.arrival_reason = "max_probe_steps_reached_no_eta"
        finalize_eta_fields(states)

    return states, diagnostics


def finalize_eta_fields(states: List[ProbeState]) -> None:
    """输出前兜底补算 ETA。

    如果跟踪过程中某个接口异常，动态筛选流程可能还没执行到最后的统一补算。
    只要 started_time 和 arrived_time 已经存在，这里就能把 eta_seconds 补回来。
    """

    for state in states:
        if state.started_time is not None and state.arrived_time is not None:
            state.eta_seconds = max(0, state.arrived_time - state.started_time)


def build_eta_totals(states: List[ProbeState]) -> Dict[str, Any]:
    """统计全部 OD 以及每个起点下的 ETA 总和。"""

    totals_by_origin: Dict[str, Dict[str, Any]] = {}
    total_eta_seconds = 0
    eta_count = 0

    for state in states:
        origin_bucket = totals_by_origin.setdefault(
            state.start_road,
            {
                "eta_sum_seconds": 0,
                "eta_available": 0,
                "od_count": 0,
                "missing_eta": 0,
            },
        )
        origin_bucket["od_count"] += 1

        if state.eta_seconds is None:
            origin_bucket["missing_eta"] += 1
            continue

        total_eta_seconds += state.eta_seconds
        eta_count += 1
        origin_bucket["eta_sum_seconds"] += state.eta_seconds
        origin_bucket["eta_available"] += 1

    return {
        "total_eta_seconds": total_eta_seconds,
        "eta_available_count": eta_count,
        "missing_eta_count": len(states) - eta_count,
        "eta_sum_by_origin": totals_by_origin,
    }


def build_summary(
    states: List[ProbeState],
    fatal_error: Optional[str] = None,
    first_departure_time: Optional[int] = None,
) -> Dict[str, Any]:
    """生成和终端 Summary 板块一致的统计信息。"""

    finalize_eta_fields(states)

    total = len(states)
    injected = sum(1 for s in states if isinstance(s.person_id, int))
    eta_ok = sum(1 for s in states if s.eta_seconds is not None)
    timeout_no_eta = sum(1 for s in states if s.arrival_reason == "max_probe_steps_reached_no_eta")
    vehicle_timeout_count = sum(1 for s in states if s.arrival_reason == "vehicle_timeout")
    reachable_within_threshold_count = sum(1 for s in states if s.eta_seconds is not None)
    inject_fail = sum(1 for s in states if s.arrival_reason.startswith("inject_exception:"))
    tracking_error_ods = sum(1 for s in states if s.tracking_error_count > 0)
    eta_totals = build_eta_totals(states)
    eta_values = [s.eta_seconds for s in states if s.eta_seconds is not None]
    arrived_times = [s.arrived_time for s in states if s.arrived_time is not None]
    last_arrived_time = max(arrived_times) if arrived_times else None
    max_vehicle_eta_seconds = max(eta_values) if eta_values else None
    evacuation_duration_seconds = (
        max(0, last_arrived_time - first_departure_time)
        if last_arrived_time is not None and first_departure_time is not None
        else None
    )

    summary: Dict[str, Any] = {
        "total_ods": total,
        "total_vehicles": total,
        "hub_count": len({s.hub_id for s in states if s.hub_id}),
        "injected_ok": injected,
        "all_arrived_count": eta_ok,
        "reachable_within_threshold_count": reachable_within_threshold_count,
        "vehicle_timeout_count": vehicle_timeout_count,
        "eta_available": eta_ok,
        "timeout_no_eta": timeout_no_eta,
        "inject_failures": inject_fail,
        "tracking_error_ods": tracking_error_ods,
        "max_vehicle_eta_seconds": max_vehicle_eta_seconds,
        "total_eta_seconds": eta_totals["total_eta_seconds"],
        "last_arrived_time": last_arrived_time,
        "evacuation_duration_seconds": evacuation_duration_seconds,
        "eta_available_count": eta_totals["eta_available_count"],
        "missing_eta_count": eta_totals["missing_eta_count"],
        "eta_sum_by_origin": eta_totals["eta_sum_by_origin"],
    }
    if fatal_error is not None:
        summary["fatal_error"] = fatal_error
    return summary


def state_to_row(state: ProbeState) -> Dict[str, Any]:
    """把一个 ProbeState 转成 RESULT_ROWS_JSON 中的一行。"""

    if state.eta_seconds is None and state.started_time is not None and state.arrived_time is not None:
        state.eta_seconds = max(0, state.arrived_time - state.started_time)

    return {
        "od_index": state.od_index,
        "source_person_id": state.source_person_id,
        "start_road": state.start_road,
        "end_road": state.end_road,
        "origin_road": state.start_road,
        "destination_road": state.end_road,
        "hub_id": state.hub_id,
        "hub_trip_index": state.hub_trip_index,
        "seat_index_in_trip": state.seat_index_in_trip,
        "planned_departure_time": state.planned_departure_time,
        "start_lane_id": state.start_lane_id,
        "end_lane_id": state.end_lane_id,
        "person_id": state.person_id,
        "departure_time": state.departure_time,
        "started_time": state.started_time,
        "arrived_time": state.arrived_time,
        "timeout_time": state.timeout_time,
        "eta_seconds": state.eta_seconds,
        "arrival_reason": state.arrival_reason,
        "tracking_error_count": state.tracking_error_count,
        "last_tracking_error": state.last_tracking_error,
    }


def write_report_json(
    report_json: Optional[str],
    summary: Dict[str, Any],
    runtime_diagnostics: Dict[str, Any],
    rows: List[Dict[str, Any]],
    od_generation: Dict[str, Any],
) -> None:
    """把 Summary 和 RESULT_ROWS_JSON 两块内容合并保存到一个 JSON 文件。"""

    if not report_json:
        return

    output_path = Path(report_json).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "script_version": SCRIPT_VERSION,
        "od_generation": od_generation,
        "summary": summary,
        "runtime_diagnostics": runtime_diagnostics,
        "rows": rows,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved to: {output_path}")


def summarize_and_print(
    states: List[ProbeState],
    fatal_error: Optional[str] = None,
    report_json: Optional[str] = None,
    od_generation: Optional[Dict[str, Any]] = None,
    runtime_diagnostics: Optional[Dict[str, Any]] = None,
    first_departure_time: Optional[int] = None,
    rows_print_limit: int = DEFAULT_ROWS_PRINT_LIMIT,
) -> None:
    finalize_eta_fields(states)
    summary = build_summary(
        states,
        fatal_error=fatal_error,
        first_departure_time=first_departure_time,
    )
    rows = [state_to_row(s) for s in states]
    od_generation = od_generation or {}
    runtime_diagnostics = runtime_diagnostics or {}

    print("=== Destination Reachability Filter Summary ===")
    print(f"total_ods:        {summary['total_ods']}")
    print(f"injected_ok:      {summary['injected_ok']}")
    print(f"eta_available:    {summary['eta_available']}")
    print(f"reachable_3600:   {summary['reachable_within_threshold_count']}")
    print(f"vehicle_timeout:  {summary['vehicle_timeout_count']}")
    print(f"max_eta_sec:      {summary['max_vehicle_eta_seconds']}")
    print(f"total_eta_sec:    {summary['total_eta_seconds']}")
    print(f"last_arrived_t:   {summary['last_arrived_time']}")
    print(f"evacuation_sec:   {summary['evacuation_duration_seconds']}")
    print(f"timeout_no_eta:   {summary['timeout_no_eta']}")
    print(f"inject_failures:  {summary['inject_failures']}")
    print(f"tracking_errors:  {summary['tracking_error_ods']}")
    print(f"sim_steps:        {runtime_diagnostics.get('sim_steps_executed')}")
    print(f"tracking_ticks:   {runtime_diagnostics.get('tracking_ticks_executed')}")
    print(f"vehicle_queries:  {runtime_diagnostics.get('vehicle_query_count')}")
    print(f"early_stop:       {runtime_diagnostics.get('early_stop_reason')}")
    if "fatal_error" in summary:
        print(f"fatal_error:      {summary['fatal_error']}")

    print("===RUNTIME_DIAGNOSTICS_JSON===")
    print(json.dumps(runtime_diagnostics, ensure_ascii=False, indent=2))
    if rows_print_limit > 0:
        rows_sample = rows[:rows_print_limit]
        print(f"===RESULT_ROWS_JSON_SAMPLE_FIRST_{rows_print_limit}===")
        print(json.dumps(rows_sample, ensure_ascii=False, indent=2))
        if len(rows) > rows_print_limit:
            print(f"... {len(rows) - rows_print_limit} more rows saved only in report_json")
    else:
        print("===RESULT_ROWS_JSON_SAMPLE_SKIPPED===")
        print(f"{len(rows)} rows saved only in report_json")
    write_report_json(
        report_json=report_json,
        summary=summary,
        runtime_diagnostics=runtime_diagnostics,
        rows=rows,
        od_generation=od_generation,
    )


async def amain(args: argparse.Namespace) -> int:
    """真实运行入口：动态注入候选 D，筛选 3600 秒内可达目的地。"""

    dispatch_tasks, od_generation = build_filter_tasks_from_destination_csv(
        destination_csv=args.destination_csv,
        origin_road=args.origin_road,
        batch_size=args.batch_size,
        max_destinations=MAX_DESTINATIONS,
    )
    dispatch_span_seconds = int(od_generation.get("max_batch_index", 0)) * args.headway_seconds
    if args.max_probe_steps is None:
        # 覆盖最后一批发车 + 3600 秒阈值 + 10 分钟冗余。
        effective_max_probe_steps = max(
            1,
            (
                dispatch_span_seconds
                + args.vehicle_timeout_seconds
                + 600
                + args.interval
                - 1
            )
            // args.interval,
        )
    else:
        effective_max_probe_steps = args.max_probe_steps

    tracking_horizon_seconds = effective_max_probe_steps * args.interval
    od_generation["tracking_config"] = {
        "interval": args.interval,
        "max_probe_steps": effective_max_probe_steps,
        "tracking_horizon_seconds": tracking_horizon_seconds,
        "query_concurrency": args.query_concurrency,
        "track_every_steps": args.track_every_steps,
        "vehicle_timeout_seconds": args.vehicle_timeout_seconds,
    }
    od_generation["dispatch_config"] = {
        "batch_size": args.batch_size,
        "headway_seconds": args.headway_seconds,
        "departure_delay_steps": args.departure_delay_steps,
    }

    # 给模拟器总步数留足空间：warmup + 出发延迟 + 跟踪步数 + 冗余。
    step_count = args.warmup_steps + args.departure_delay_steps + effective_max_probe_steps + 60
    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=step_count,
    )

    states: List[ProbeState] = []
    fatal_error: Optional[str] = None
    runtime_diagnostics: Dict[str, Any] = {
        "sim_steps_executed": 0,
        "tracking_ticks_executed": 0,
        "vehicle_query_count": 0,
        "early_stop_reason": None,
        "track_every_steps": args.track_every_steps,
        "max_probe_steps": effective_max_probe_steps,
        "vehicle_timeout_seconds": args.vehicle_timeout_seconds,
        "last_sim_time": None,
    }
    exit_code = 0
    first_departure_time: Optional[int] = None

    try:
        # 从 Mongo 取一个已有 person 作为模板，然后每个 OD 复制并改写起终点。
        person_template = load_person_template_from_mongo(
            args.mongo_uri, args.agent_db, args.agent_coll
        )

        # 注入探针车前，先让背景交通流/信控策略跑一段。
        for _ in range(args.warmup_steps):
            await env.step_once()

        now = await env.eng.get_current_time()
        first_departure_time = int(now["t"]) + args.departure_delay_steps * env.step_size
        assign_planned_departure_times(
            tasks=dispatch_tasks,
            first_departure_time=first_departure_time,
            headway_seconds=args.headway_seconds,
        )
        planned_departures = [
            task.planned_departure_time
            for task in dispatch_tasks
            if task.planned_departure_time is not None
        ]
        od_generation["dispatch_config"]["first_departure_time"] = first_departure_time
        od_generation["dispatch_config"]["last_planned_departure_time"] = (
            max(planned_departures) if planned_departures else None
        )

        states, runtime_diagnostics = await run_dynamic_destination_filter(
            env=env,
            person_template=person_template,
            tasks=dispatch_tasks,
            first_departure_time=first_departure_time,
            headway_seconds=args.headway_seconds,
            vehicle_timeout_seconds=args.vehicle_timeout_seconds,
            max_probe_steps=effective_max_probe_steps,
            query_concurrency=args.query_concurrency,
            track_every_steps=args.track_every_steps,
            progress_print_every=args.progress_print_every,
        )
    except Exception as exc:
        fatal_error = str(exc)
        exit_code = 1
    finally:
        summarize_and_print(
            states,
            fatal_error=fatal_error,
            report_json=args.report_json,
            od_generation=od_generation,
            runtime_diagnostics=runtime_diagnostics,
            first_departure_time=first_departure_time,
            rows_print_limit=args.rows_print_limit,
        )
        try:
            env.eng._stop_simulator()
        except Exception as stop_exc:
            print(f"WARNING: simulator stop failed and was ignored: {stop_exc}")

    return exit_code


def main() -> int:
    """命令行入口。"""

    start_time = time.time()
    args = parse_args()
    exit_code = asyncio.run(amain(args))
    end_time = time.time()
    print(f"Total execution time: {end_time - start_time:.2f} seconds")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
