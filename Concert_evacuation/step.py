#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
脚本功能：
1. 固定一个起点 O。
2. 从 CSV 中读取前 N 个目的地 D。
3. 模拟器 1 step = 1 秒。
4. 每隔 HEADWAY_SECONDS 个 step，注入一批 BATCH_SIZE 辆车。
5. 当“所有成功注入的车辆都到达终点”时，提前结束模拟。
6. 否则，当模拟步数达到 MAX_PROBE_STEPS=1200 时结束模拟。
7. 对于达到 MAX_PROBE_STEPS 时仍未到达终点的车辆，认为其终点不可达。
8. 输出 summary + rows 两部分 JSON 报告。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# =========================================================
# 1. 固定配置区
# =========================================================

# 版本号，方便你在服务器上 grep 检查当前运行的是不是这份脚本
SCRIPT_VERSION = "step_driven_destination_filter_no_timeout_20260424"

# 当前脚本所在目录
SCRIPT_DIR = Path(__file__).resolve().parent

# 默认的目的地 CSV 文件
DEFAULT_DESTINATION_CSV = "/home/nkk/work/src/python/concert_evacuation/result/smart_od_trips.csv"

# 固定起点 O
FILTER_ORIGIN_ROAD = "200002061"

# 最多读取多少个目的地 D
# None 表示读取 CSV 全部行
MAX_DESTINATIONS = 100

# 每一批注入多少辆车
BATCH_SIZE = 50

# 每隔多少 step 注入一批车
# 因为你的模拟器 1 step = 1 秒，所以 300 就表示每 300 秒注入一批
HEADWAY_SECONDS = 300

# 最大模拟步数
# 达到这个值后，仍未到达终点的车辆视为不可达
MAX_PROBE_STEPS = 1200

# 每隔多少个 step 查询一次车辆位置
DEFAULT_TRACK_EVERY_STEPS = 10

# 位置查询的并发上限
DEFAULT_QUERY_CONCURRENCY = 50

# 服务器上 trafficlight_rl 一般在当前脚本的上级目录
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =========================================================
# 2. 数据结构
# =========================================================

@dataclass
class VehicleState:
    """
    用来记录每一辆探针车的完整状态。
    最终 rows 里的每一行，就是这个结构序列化后的结果。
    """

    # 任务基本信息
    od_index: int
    source_person_id: Optional[str]
    start_road: str
    end_road: str

    # 批次信息
    batch_index: int
    seat_in_batch: int

    # 该车所属批次的注入时刻（模拟器时间）
    departure_time: int

    # 起终点 lane
    start_lane_id: Optional[int] = None
    end_lane_id: Optional[int] = None

    # 模拟器返回的 person_id
    person_id: Optional[int] = None

    # 运行过程时间戳
    started_time: Optional[int] = None
    arrived_time: Optional[int] = None

    # ETA = arrived_time - started_time
    eta_seconds: Optional[int] = None

    # 最终结束原因
    # 常见值：
    # - not_injected
    # - not_arrived
    # - reach_destination_road
    # - disappeared_after_departure
    # - inject_exception: ...
    # - not_reached_within_max_probe_steps
    arrival_reason: str = "not_injected"

    # 最近一次查询到的 road
    last_road: Optional[str] = None

    # 跟踪错误信息
    tracking_error_count: int = 0
    last_tracking_error: Optional[str] = None


# =========================================================
# 3. 模拟器封装
# =========================================================

class Env:
    """
    对底层引擎做一个薄封装，方便统一管理。
    """

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
        # 只有真正运行时才导入，避免你本地仅查看 --help 时缺环境报错
        from trafficlight_rl.engine import get_engine

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
        """
        推进一次模拟器。
        你这里默认 interval=1，所以一次 step_once 就是推进 1 秒。
        """
        self.eng.next_step(self.step_size)


# =========================================================
# 4. 模板 person 处理
# =========================================================

def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Mongo 中已有 person 模板整理成 AddPerson 接口可接受的格式。

    主要做两件事：
    1. 把 snake_case 字段转成 camelCase。
    2. 只保留 AddPerson 真正需要的字段，避免传入无关内容。
    """

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

    # 新增 person 时，不能沿用原模板的 id/base
    normalized.pop("id", None)
    normalized.pop("base", None)

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


def load_person_template_from_mongo(
    mongo_uri: str,
    agent_db: str,
    agent_coll: str,
) -> Dict[str, Any]:
    """
    从 MongoDB 中读取一个已有 person，作为 AddPerson 的模板。
    """

    import pymongo

    client = pymongo.MongoClient(mongo_uri)
    try:
        doc = client[agent_db][agent_coll].find_one({}, {"_id": 0})
        if not doc or not isinstance(doc.get("data"), dict):
            raise RuntimeError("Cannot find valid person template in Mongo collection")

        template = normalize_person_for_add(copy.deepcopy(doc["data"]))
        if not template:
            raise RuntimeError("Template becomes empty after normalization")
        return template
    finally:
        client.close()


# =========================================================
# 5. 读取 CSV 中的目的地 D
# =========================================================

def first_existing_value(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    """
    按顺序尝试多个字段名，返回第一个存在且非空的值。
    """
    for key in keys:
        value = row.get(key)
        if value is not None and str(value) != "":
            return value
    return None


def load_destination_rows_from_csv(
    destination_csv: str,
    max_destinations: Optional[int],
) -> List[Dict[str, Any]]:
    """
    从 CSV 读取目的地 D。

    兼容以下字段名：
    - D_Road_ID
    - destination_road
    - end_road
    - d
    - home_road
    - road_id

    返回的每一行只保留：
    - od_index
    - source_person_id
    - end_road
    """

    csv_path = Path(destination_csv).expanduser().resolve()
    if not csv_path.exists():
        raise RuntimeError(f"Destination CSV not found: {csv_path}")

    if max_destinations is not None and max_destinations <= 0:
        raise RuntimeError("MAX_DESTINATIONS must be greater than 0")

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"Destination CSV has no header: {csv_path}")

        rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            # 达到上限后停止读取
            if max_destinations is not None and len(rows) >= max_destinations:
                break

            end_road = first_existing_value(
                row,
                ["D_Road_ID", "destination_road", "end_road", "d", "home_road", "road_id"],
            )
            if end_road is None:
                raise RuntimeError(
                    f"CSV row #{idx} must contain one of: "
                    "D_Road_ID / destination_road / end_road / d / home_road / road_id"
                )

            rows.append(
                {
                    "od_index": idx,
                    "source_person_id": first_existing_value(
                        row,
                        ["Person_ID", "person_id", "personId", "user_id", "id"],
                    ),
                    "end_road": str(end_road),
                }
            )

    if not rows:
        raise RuntimeError(f"Destination CSV contains no rows: {csv_path}")

    return rows


# =========================================================
# 6. road -> lane 解析
# =========================================================

async def resolve_lane_with_cache(env: Env, road_id: str, lane_cache: Dict[str, int]) -> int:
    """
    将 road_id 解析成 lane_id，并加缓存，避免重复查询。
    当前逻辑默认取该 road 的第一个 lane。
    """
    road_id = str(road_id)

    if road_id in lane_cache:
        return lane_cache[road_id]

    lanes = await env.eng.get_lane_id_of_road(road_id)
    if not lanes:
        raise RuntimeError(f"Cannot resolve lane ids for road: {road_id}")

    lane_id = int(lanes[0])
    lane_cache[road_id] = lane_id
    return lane_id


# =========================================================
# 7. 构造 AddPerson payload
# =========================================================

def build_person_payload(
    template: Dict[str, Any],
    start_lane_id: int,
    end_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    """
    使用模板 person，构造真正用于 AddPerson 的 payload。
    这里不手写 routes，让模拟器自己自动寻路。
    """
    person = copy.deepcopy(template)

    # 起点位置
    person["home"] = {
        "lane_position": {
            "lane_id": int(start_lane_id),
            "s": 1.0,
        }
    }

    # 只保留一条 trip：从起点 lane 到终点 lane
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


# =========================================================
# 8. 注入单辆车
# =========================================================

async def inject_one_vehicle(
    env: Env,
    person_template: Dict[str, Any],
    origin_road: str,
    row: Dict[str, Any],
    departure_time: int,
    batch_index: int,
    seat_in_batch: int,
    lane_cache: Dict[str, int],
) -> VehicleState:
    """
    注入一辆探针车，并返回它的初始状态。
    """
    state = VehicleState(
        od_index=int(row["od_index"]),
        source_person_id=(
            str(row["source_person_id"]) if row.get("source_person_id") is not None else None
        ),
        start_road=str(origin_road),
        end_road=str(row["end_road"]),
        batch_index=batch_index,
        seat_in_batch=seat_in_batch,
        departure_time=int(departure_time),
    )

    try:
        # 解析起终点 lane
        start_lane_id = await resolve_lane_with_cache(env, state.start_road, lane_cache)
        end_lane_id = await resolve_lane_with_cache(env, state.end_road, lane_cache)

        state.start_lane_id = start_lane_id
        state.end_lane_id = end_lane_id

        # 构造 AddPerson 请求
        payload = build_person_payload(
            template=person_template,
            start_lane_id=start_lane_id,
            end_lane_id=end_lane_id,
            departure_time=departure_time,
        )

        # 调用模拟器接口
        add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
        person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None

        if not isinstance(person_id, int):
            raise RuntimeError(f"AddPerson failed: {add_resp}")

        state.person_id = person_id
        state.arrival_reason = "not_arrived"

    except Exception as exc:
        # 注车失败
        state.arrival_reason = f"inject_exception: {exc}"

    return state


# =========================================================
# 9. 统计 summary
# =========================================================

def build_summary(
    rows: List[VehicleState],
    config: Dict[str, Any],
    runtime: Dict[str, Any],
    fatal_error: Optional[str],
) -> Dict[str, Any]:
    """
    生成最终 summary。
    """

    eta_values = [x.eta_seconds for x in rows if x.eta_seconds is not None]
    arrived_times = [x.arrived_time for x in rows if x.arrived_time is not None]

    summary = {
        # 任务总数
        "total_tasks": len(rows),

        # 成功注入的车辆数
        "injected_ok_count": sum(1 for x in rows if x.person_id is not None),

        # 到达终点的车辆数
        "arrived_destination_count": sum(
            1 for x in rows if x.arrival_reason == "reach_destination_road"
        ),

        # 在最大步数限制内仍未到达终点，视为不可达
        "not_reached_within_max_probe_steps_count": sum(
            1 for x in rows if x.arrival_reason == "not_reached_within_max_probe_steps"
        ),

        # 注车失败数量
        "inject_failures": sum(
            1 for x in rows if x.arrival_reason.startswith("inject_exception:")
        ),

        # 发生过位置跟踪错误的车辆数
        "tracking_error_count": sum(
            1 for x in rows if x.tracking_error_count > 0
        ),

        # ETA 统计
        "max_vehicle_eta_seconds": max(eta_values) if eta_values else None,
        "total_eta_seconds": sum(eta_values),

        # 最后一辆成功到达的模拟器时刻
        "last_arrived_time": max(arrived_times) if arrived_times else None,

        # 运行配置和运行诊断
        "config": config,
        "runtime": runtime,

        # 全局异常
        "fatal_error": fatal_error,
    }
    return summary


# =========================================================
# 10. 写报告 JSON
# =========================================================

def write_report(report_json: str, summary: Dict[str, Any], rows: List[VehicleState]) -> None:
    """
    将 summary 和 rows 写入 JSON 文件。
    """
    output_path = Path(report_json).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "script_version": SCRIPT_VERSION,
        "summary": summary,
        "rows": [asdict(x) for x in rows],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Report saved to: {output_path}")


# =========================================================
# 11. 命令行参数
# =========================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step-driven OD injection script without timeout. "
                    "Stop when all injected vehicles arrive destination, "
                    "or when max_probe_steps is reached."
    )

    # Mongo / 地图 / agent 集合
    parser.add_argument("--mongo_uri", default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017")
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")

    # 输入 CSV 和固定 O
    parser.add_argument("--destination_csv", default=DEFAULT_DESTINATION_CSV)
    parser.add_argument("--origin_road", default=FILTER_ORIGIN_ROAD)

    # 注车节奏参数
    parser.add_argument("--batch_size", type=int, default=BATCH_SIZE)
    parser.add_argument("--headway_seconds", type=int, default=HEADWAY_SECONDS)

    # 模拟器推进参数
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)  # 你这里建议保持 1
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument("--max_probe_steps", type=int, default=MAX_PROBE_STEPS)

    # 跟踪参数
    parser.add_argument("--query_concurrency", type=int, default=DEFAULT_QUERY_CONCURRENCY)
    parser.add_argument("--track_every_steps", type=int, default=DEFAULT_TRACK_EVERY_STEPS)

    # 输出文件
    parser.add_argument(
        "--report_json",
        default=str(SCRIPT_DIR / "reachable_destinations_step_driven_report.json"),
    )

    return parser.parse_args()


# =========================================================
# 12. 主流程
# =========================================================

async def amain(args: argparse.Namespace) -> int:
    """
    主异步入口。
    """

    print("RUNNING_SCRIPT =", __file__)
    print("SCRIPT_VERSION =", SCRIPT_VERSION)
    print("MAX_DESTINATIONS =", MAX_DESTINATIONS)
    print("BATCH_SIZE =", args.batch_size)
    print("HEADWAY_SECONDS =", args.headway_seconds)
    print("MAX_PROBE_STEPS =", args.max_probe_steps)

    env: Optional[Env] = None
    all_states: List[VehicleState] = []
    fatal_error: Optional[str] = None

    # 运行时诊断信息
    runtime = {
        "sim_steps_executed": 0,
        "tracking_ticks_executed": 0,
        "vehicle_query_count": 0,
        "injected_count": 0,
        "early_stop_reason": None,
        "last_sim_time": None,
    }

    try:
        # -------------------------------------------------
        # 1) 读取 CSV 前 N 行目的地 D
        # -------------------------------------------------
        rows = load_destination_rows_from_csv(
            destination_csv=args.destination_csv,
            max_destinations=MAX_DESTINATIONS,
        )
        print("loaded_destination_count =", len(rows))

        if args.batch_size <= 0:
            raise RuntimeError("--batch_size must be greater than 0")
        if args.headway_seconds <= 0:
            raise RuntimeError("--headway_seconds must be greater than 0")
        if args.track_every_steps <= 0:
            raise RuntimeError("--track_every_steps must be greater than 0")
        if args.query_concurrency <= 0:
            raise RuntimeError("--query_concurrency must be greater than 0")
        if args.max_probe_steps <= 0:
            raise RuntimeError("--max_probe_steps must be greater than 0")

        total_batches = (len(rows) + args.batch_size - 1) // args.batch_size

        # -------------------------------------------------
        # 2) 初始化模拟器环境
        # -------------------------------------------------
        # 这里 step_count 给足够大即可，确保底层引擎不会太早停
        step_count = args.warmup_steps + args.departure_delay_steps + args.max_probe_steps + 60

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

        # -------------------------------------------------
        # 3) 读取 person 模板
        # -------------------------------------------------
        person_template = load_person_template_from_mongo(
            args.mongo_uri,
            args.agent_db,
            args.agent_coll,
        )

        # -------------------------------------------------
        # 4) warmup
        # -------------------------------------------------
        for _ in range(args.warmup_steps):
            await env.step_once()

        # -------------------------------------------------
        # 5) 计算第一批注车时间
        # -------------------------------------------------
        now = await env.eng.get_current_time()
        first_dispatch_time = int(now["t"]) + args.departure_delay_steps * env.step_size
        next_dispatch_time = first_dispatch_time

        print("first_dispatch_time =", first_dispatch_time)

        # 已经注入到第几行
        next_row_index = 0

        # 当前是第几批
        batch_index = 0

        # 当前仍在跟踪的车辆
        active_by_person_id: Dict[int, VehicleState] = {}

        # road -> lane cache
        lane_cache: Dict[str, int] = {}

        # 用于限制并发查询位置
        semaphore = asyncio.Semaphore(args.query_concurrency)

        async def query_current_road(
            state: VehicleState,
        ) -> Tuple[VehicleState, Optional[str], Optional[str]]:
            """
            查询一辆车当前所在 road。
            返回：(state, current_road_str, error_message)
            """
            try:
                async with semaphore:
                    current_road = await env.eng.get_road_id_with_vehicle(state.person_id)
                current_road_str = str(current_road) if current_road is not None else None
                return state, current_road_str, None
            except Exception as exc:
                return state, None, str(exc)

        # -------------------------------------------------
        # 6) 主模拟循环
        # -------------------------------------------------
        for step_index in range(args.max_probe_steps):
            # 获取当前模拟器时间
            sim_clock = await env.eng.get_current_time()
            now_t = int(sim_clock["t"])
            runtime["last_sim_time"] = now_t

            # ---------------------------------------------
            # 6.1 到点就注入一整批车
            #     用 while 而不是 if，是为了防止时间跨过多个批次点时漏发
            # ---------------------------------------------
            while next_row_index < len(rows) and now_t >= next_dispatch_time:
                batch_rows = rows[next_row_index: next_row_index + args.batch_size]

                print(
                    f"[dispatch] sim_t={now_t} "
                    f"batch_index={batch_index} "
                    f"batch_size={len(batch_rows)} "
                    f"next_row_index={next_row_index}"
                )

                for seat_in_batch, row in enumerate(batch_rows, start=1):
                    state = await inject_one_vehicle(
                        env=env,
                        person_template=person_template,
                        origin_road=args.origin_road,
                        row=row,
                        departure_time=next_dispatch_time,
                        batch_index=batch_index,
                        seat_in_batch=seat_in_batch,
                        lane_cache=lane_cache,
                    )
                    all_states.append(state)
                    runtime["injected_count"] += 1

                    # 只有成功拿到 person_id，才算真正注入成功，才加入 active 跟踪表
                    if state.person_id is not None:
                        active_by_person_id[state.person_id] = state

                next_row_index += len(batch_rows)
                batch_index += 1
                next_dispatch_time += args.headway_seconds

            # ---------------------------------------------
            # 6.2 每隔一定 step 查询一次车辆位置
            # ---------------------------------------------
            should_query = (
                step_index % args.track_every_steps == 0
                or step_index == args.max_probe_steps - 1
            )

            if should_query and active_by_person_id:
                runtime["tracking_ticks_executed"] += 1

                active_states = list(active_by_person_id.values())
                runtime["vehicle_query_count"] += len(active_states)

                query_results = await asyncio.gather(
                    *(query_current_road(state) for state in active_states)
                )

                for state, current_road_str, query_error in query_results:
                    # 查询失败
                    if query_error is not None:
                        state.tracking_error_count += 1
                        state.last_tracking_error = query_error
                        continue

                    # 第一次能查询到位置，认为开始运行
                    if state.started_time is None and current_road_str is not None:
                        state.started_time = now_t

                    # 到达终点 road
                    if state.started_time is not None and current_road_str == state.end_road:
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "reach_destination_road"
                        active_by_person_id.pop(state.person_id, None)
                        continue

                    # 如果已经开始运行，且之前有 last_road，现在突然查不到了，
                    # 这里保留一个“运行后消失”的结束理由，方便后续排查
                    if (
                        state.started_time is not None
                        and current_road_str is None
                        and state.last_road is not None
                    ):
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "disappeared_after_departure"
                        active_by_person_id.pop(state.person_id, None)
                        continue

                    state.last_road = current_road_str

            # ---------------------------------------------
            # 6.3 提前结束条件
            #
            # 逻辑要求：
            # - 所有任务都已经完成注车尝试
            # - 并且所有“成功注入”的车辆都已经到达终点
            # 才允许提前结束
            # ---------------------------------------------
            injected_ok_count = sum(1 for s in all_states if s.person_id is not None)
            arrived_destination_count = sum(
                1 for s in all_states if s.arrival_reason == "reach_destination_road"
            )

            if next_row_index >= len(rows):
                # 情况 A：至少有成功注入的车，并且全部到达终点
                if injected_ok_count > 0 and arrived_destination_count == injected_ok_count:
                    runtime["early_stop_reason"] = "all_injected_vehicles_arrived_destination"
                    break

                # 情况 B：所有行都处理完了，但一辆成功注入的车都没有
                if injected_ok_count == 0 and not active_by_person_id:
                    runtime["early_stop_reason"] = "all_rows_processed_but_no_vehicle_injected"
                    break

            # 推进一步模拟器
            await env.step_once()
            runtime["sim_steps_executed"] += 1

        else:
            # for 正常跑满，说明达到 max_probe_steps 了
            runtime["early_stop_reason"] = "max_probe_steps_reached"

        # -------------------------------------------------
        # 7) 对仍未到达终点的已注入车辆，统一标记为不可达
        # -------------------------------------------------
        for state in all_states:
            if state.person_id is not None and state.arrival_reason == "not_arrived":
                state.arrival_reason = "not_reached_within_max_probe_steps"

        # -------------------------------------------------
        # 8) 生成 summary
        # -------------------------------------------------
        config = {
            "destination_csv": str(Path(args.destination_csv).expanduser().resolve()),
            "origin_road": args.origin_road,
            "max_destinations": MAX_DESTINATIONS,
            "total_destination_count": len(rows),
            "batch_size": args.batch_size,
            "headway_seconds": args.headway_seconds,
            "track_every_steps": args.track_every_steps,
            "first_dispatch_time": first_dispatch_time,
            "total_batches": total_batches,
            "max_probe_steps": args.max_probe_steps,
            "interval": args.interval,
        }

        summary = build_summary(
            rows=all_states,
            config=config,
            runtime=runtime,
            fatal_error=None,
        )

        # -------------------------------------------------
        # 9) 写报告
        # -------------------------------------------------
        write_report(args.report_json, summary, all_states)

        print("=== SUMMARY ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    except Exception as exc:
        fatal_error = str(exc)

        config = {
            "destination_csv": str(Path(args.destination_csv).expanduser().resolve()),
            "origin_road": args.origin_road,
            "max_destinations": MAX_DESTINATIONS,
            "batch_size": args.batch_size,
            "headway_seconds": args.headway_seconds,
            "track_every_steps": args.track_every_steps,
            "max_probe_steps": args.max_probe_steps,
            "interval": args.interval,
        }

        summary = build_summary(
            rows=all_states,
            config=config,
            runtime=runtime,
            fatal_error=fatal_error,
        )

        write_report(args.report_json, summary, all_states)

        print("fatal_error =", fatal_error)
        return 1

    finally:
        # 无论成功失败，都尝试停止模拟器
        if env is not None:
            try:
                env.eng._stop_simulator()
            except Exception as stop_exc:
                print(f"WARNING: simulator stop failed and was ignored: {stop_exc}")


# =========================================================
# 13. 同步入口
# =========================================================

def main() -> int:
    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())