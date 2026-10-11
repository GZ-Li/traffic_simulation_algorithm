#!/usr/bin/env python3
"""Step-based hub evacuation test: inject many AddPerson vehicles in one simulation.

What this script does:
1. Connect simulator engine once.
2. In formal evacuation mode, read OD CSV/JSON and dispatch each hub in 50-car batches.
3. Use local step counting instead of querying simulator time.
4. Dispatch one batch every 100 steps and stop globally at step 800.
5. Output reached/unreachable statistics, hub summary, and dispatch validation as JSON.
"""

from __future__ import annotations
import argparse
import asyncio
import copy
import csv
import json
import os
import random
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 版本标识：服务器上可以用 grep 检查，确认传的是 step 计时版 V3。
SCRIPT_VERSION = "post_hub_evacuation_sim_V3_step_20260424"

# 脚本所在目录。默认报告 JSON 会保存到这里，避免写死到别的工程目录。
SCRIPT_DIR = Path(__file__).resolve().parent

# 服务器上的 SUMO 路网文件。脚本会从这里读取真实存在的 road id。
DEFAULT_NET_XML = "/home/nkk/work/src/python/Multi_Accident/Shaped.net.xml"

# 服务器上的正式疏散 OD CSV。默认直接读取这个 1500 人 OD 文件。
DEFAULT_OD_CSV = "/home/nkk/work/src/python/concert_evacuation/result/smart_od_trips.csv"

# =========================
# 常用实验配置区
# =========================
# 后续如果有多个起点，直接在这里追加 road id 即可。
# 例如：
# DEFAULT_ORIGIN_ROADS = [
#     "200000480",
#     "200000154",
#     "200000033",
# ]
# 脚本会为每个起点分别随机生成 DEFAULT_DESTINATION_COUNT 个终点。
DEFAULT_ORIGIN_ROADS: List[str] = [
    "200000480",
    "200000154",
    "200000033",
]

# 正式疏散模式使用的固定接驳点 road id。
# 你拿到 7 个接驳点后，直接把它们都写到这里即可。
FIXED_HUB_ROADS: List[str] = [
    "200000776",
    "200001897",
    "200000522",
    "200001780",
    "200000824",
    "200000519",
    "200002049",
]

# 每个接驳点每一趟最多发出的车辆数。
BATCH_SIZE_PER_HUB = 50

# 接驳车发车间隔，单位是本地 step。当前模拟器中 1 step = 1s，
# 因此 100 step 可以理解为 100 秒。
STEP_INTERVAL_PER_BATCH = 100

# 校验相邻两趟实际上路间隔时允许的误差，单位是 step。
DEFAULT_DISPATCH_GAP_TOLERANCE_STEPS = 10


# 每个起点随机生成多少个终点。
DEFAULT_DESTINATION_COUNT = 50

# 旧测试入口保留一个空列表，避免运行到 --use_builtin_ods 时直接 NameError。
DEFAULT_OD_PAIRS: List[Tuple[str, str]] = []

# 每次模拟步查询车辆位置时的并发数。服务器压力大时可通过 --query_concurrency 调小。
DEFAULT_QUERY_CONCURRENCY = 50

# 每推进多少个模拟步查询一次车辆位置。V3 默认 10 秒精度，明显减少位置查询量。
DEFAULT_TRACK_EVERY_STEPS = 10

# 终端只展示前 N 行车辆明细；完整 rows 仍写入 report_json。
DEFAULT_ROWS_PRINT_LIMIT = 20

# 全局最大 step。跑到这个 step 后还没到 D 的车辆直接标记为不可达。
MAX_GLOBAL_STEP = 800

# 服务器上 trafficlight_rl 通常在当前脚本的上级目录里。
# 把上级目录加入 import 搜索路径后，才能导入 trafficlight_rl.engine。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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
    planned_departure_step: Optional[int]


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
    planned_departure_step: Optional[int]
    start_lane_id: Optional[int]
    end_lane_id: Optional[int]
    person_id: Optional[int]
    departure_step: Optional[int]
    started_step: Optional[int]
    arrived_step: Optional[int]
    travel_steps: Optional[int]
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
        # 推进一次模拟器时间。当前 step 版要求 interval=1，因此一次就是 1 step。
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


def load_candidate_road_ids_from_sumo_net(net_xml: str, excluded_roads: List[str]) -> List[str]:
    """从 SUMO net.xml 中读取可作为终点的 road id。

    过滤规则：
    - 跳过 function="internal" 的内部边。
    - 跳过 id 以 ":" 开头的内部连接边。
    - 只保留纯数字 road id，保证和当前模拟器 road id 风格一致。
    - 跳过 excluded_roads，避免 O 和 D 相同。
    """

    net_path = Path(net_xml).expanduser().resolve()
    if not net_path.exists():
        raise RuntimeError(f"SUMO net XML not found: {net_path}")

    excluded = {str(road_id) for road_id in excluded_roads}
    road_ids: List[str] = []
    seen = set()
    for _, elem in ET.iterparse(net_path, events=("start",)):
        if elem.tag != "edge":
            continue

        edge_id = elem.attrib.get("id", "")
        edge_function = elem.attrib.get("function", "")
        if (
            not edge_id
            or edge_id in excluded
            or edge_id in seen
            or edge_id.startswith(":")
            or edge_function == "internal"
            or not edge_id.isdigit()
        ):
            elem.clear()
            continue

        seen.add(edge_id)
        road_ids.append(edge_id)
        elem.clear()

    if not road_ids:
        raise RuntimeError(f"No usable road ids found in SUMO net XML: {net_path}")
    return road_ids


def build_random_od_pairs_from_net(
    net_xml: str,
    origin_roads: List[str],
    destination_count: int,
    random_seed: Optional[int],
) -> Tuple[List[Tuple[str, str]], Dict[str, Any]]:
    """固定一个或多个起点，从 SUMO 路网中为每个起点随机抽取终点，生成 OD 列表。"""

    if destination_count <= 0:
        raise RuntimeError("--destination_count must be greater than 0")
    if not origin_roads:
        raise RuntimeError("At least one origin road is required")

    origin_roads = [str(road_id) for road_id in origin_roads]
    candidate_road_ids_all = load_candidate_road_ids_from_sumo_net(
        net_xml=net_xml,
        excluded_roads=origin_roads,
    )
    if destination_count > len(candidate_road_ids_all):
        raise RuntimeError(
            f"destination_count={destination_count} exceeds available road ids={len(candidate_road_ids_all)}"
        )

    rng = random.Random(random_seed)
    od_pairs: List[Tuple[str, str]] = []
    by_origin: Dict[str, List[str]] = {}
    for origin_road in origin_roads:
        candidate_road_ids = [road_id for road_id in candidate_road_ids_all if road_id != origin_road]
        destinations = rng.sample(candidate_road_ids, destination_count)
        by_origin[origin_road] = destinations
        od_pairs.extend((origin_road, destination) for destination in destinations)

    metadata = {
        "mode": "random_destinations_from_sumo_net",
        "net_xml": str(Path(net_xml).expanduser().resolve()),
        "origin_roads": origin_roads,
        "destination_count_per_origin": destination_count,
        "total_od_count": len(od_pairs),
        "candidate_road_count": len(candidate_road_ids_all),
        "random_seed": random_seed,
        "destinations_by_origin": by_origin,
    }
    return od_pairs, metadata


def first_existing_value(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    """按多个候选字段名读取 OD 数据，兼容不同 JSON 命名。"""

    for key in keys:
        value = row.get(key)
        if value is not None and str(value) != "":
            return value
    return None


def load_od_rows_from_json(od_json: str) -> List[Dict[str, Any]]:
    """读取用户给定的 OD JSON。

    推荐格式是列表：
    [
      {"origin_road": "200000480", "destination_road": "200001001"}
    ]

    如果外部算法输出为 {"ods": [...]}, {"rows": [...]}, {"vehicles": [...]}，
    这里也会自动识别，方便对接不同版本的数据。
    """

    json_path = Path(od_json).expanduser().resolve()
    if not json_path.exists():
        raise RuntimeError(f"OD JSON not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = None
        for key in ("ods", "rows", "vehicles", "data"):
            value = data.get(key)
            if isinstance(value, list):
                rows = value
                break
        if rows is None:
            raise RuntimeError("OD JSON object must contain one of: ods, rows, vehicles, data")
    else:
        raise RuntimeError("OD JSON must be a list or an object containing an OD list")

    normalized_rows: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise RuntimeError(f"OD row #{idx} must be an object")

        start_road = first_existing_value(
            row,
            ["origin_road", "start_road", "o", "origin", "hub_road", "hub"],
        )
        end_road = first_existing_value(
            row,
            ["destination_road", "end_road", "d", "destination", "home_road", "home"],
        )
        if start_road is None or end_road is None:
            raise RuntimeError(
                f"OD row #{idx} must contain origin_road/start_road/o and destination_road/end_road/d"
            )

        normalized_rows.append(
            {
                "od_index": idx,
                "source_person_id": first_existing_value(
                    row,
                    ["Person_ID", "person_id", "personId", "user_id", "id"],
                ),
                "start_road": str(start_road),
                "end_road": str(end_road),
                "raw": row,
            }
        )

    if not normalized_rows:
        raise RuntimeError(f"OD JSON contains no rows: {json_path}")
    return normalized_rows


def load_od_rows_from_csv(od_csv: str) -> List[Dict[str, Any]]:
    """读取正式疏散 CSV。

    当前 smart_od_trips.csv 的列名是：
    - Person_ID：原始用户/车辆 ID。
    - O_Road_ID：接驳点 road id。
    - D_Road_ID：home road id。
    """

    csv_path = Path(od_csv).expanduser().resolve()
    if not csv_path.exists():
        raise RuntimeError(f"OD CSV not found: {csv_path}")

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"OD CSV has no header: {csv_path}")

        normalized_rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            start_road = first_existing_value(
                row,
                ["O_Road_ID", "origin_road", "start_road", "o", "origin", "hub_road", "hub"],
            )
            end_road = first_existing_value(
                row,
                ["D_Road_ID", "destination_road", "end_road", "d", "destination", "home_road", "home"],
            )
            if start_road is None or end_road is None:
                raise RuntimeError(
                    f"OD CSV row #{idx} must contain O_Road_ID and D_Road_ID"
                )

            normalized_rows.append(
                {
                    "od_index": idx,
                    "source_person_id": first_existing_value(
                        row,
                        ["Person_ID", "person_id", "personId", "user_id", "id"],
                    ),
                    "start_road": str(start_road),
                    "end_road": str(end_road),
                    "raw": row,
                }
            )

    if not normalized_rows:
        raise RuntimeError(f"OD CSV contains no rows: {csv_path}")
    return normalized_rows


def build_dispatch_tasks_from_od_rows(
    od_rows: List[Dict[str, Any]],
    source_path: str,
    source_format: str,
    fixed_hub_roads: List[str],
    batch_size_per_hub: int,
) -> Tuple[List[DispatchTask], Dict[str, Any]]:
    """从正式 OD 行数据生成接驳点分批发车任务。"""

    if batch_size_per_hub <= 0:
        raise RuntimeError("--batch_size_per_hub must be greater than 0")
    if not fixed_hub_roads:
        raise RuntimeError("FIXED_HUB_ROADS or --hub_roads must contain at least one hub road")

    hub_roads = [str(road_id) for road_id in fixed_hub_roads]
    hub_set = set(hub_roads)

    grouped_rows: Dict[str, List[Dict[str, Any]]] = {hub: [] for hub in hub_roads}
    unknown_origins: Dict[str, int] = {}
    for row in od_rows:
        start_road = row["start_road"]
        if start_road not in hub_set:
            unknown_origins[start_road] = unknown_origins.get(start_road, 0) + 1
            continue
        grouped_rows[start_road].append(row)

    if unknown_origins:
        raise RuntimeError(
            "OD JSON contains origin roads outside FIXED_HUB_ROADS: "
            + json.dumps(unknown_origins, ensure_ascii=False, sort_keys=True)
        )

    tasks_by_hub: Dict[str, List[DispatchTask]] = {}
    max_trip_index = 0
    for hub_road in hub_roads:
        hub_tasks: List[DispatchTask] = []
        for hub_pos, row in enumerate(grouped_rows[hub_road]):
            trip_index = hub_pos // batch_size_per_hub
            seat_index = hub_pos % batch_size_per_hub + 1
            max_trip_index = max(max_trip_index, trip_index)
            hub_tasks.append(
                DispatchTask(
                    od_index=int(row["od_index"]),
                    source_person_id=(
                        str(row["source_person_id"])
                        if row.get("source_person_id") is not None
                        else None
                    ),
                    start_road=row["start_road"],
                    end_road=row["end_road"],
                    hub_id=hub_road,
                    hub_trip_index=trip_index,
                    seat_index_in_trip=seat_index,
                    planned_departure_step=None,
                )
            )
        tasks_by_hub[hub_road] = hub_tasks

    # 按“趟次 -> 接驳点 -> 座位序号”排序，报告里能直观看出并发发车批次。
    hub_order = {hub: idx for idx, hub in enumerate(hub_roads)}
    tasks = [
        task
        for hub_tasks in tasks_by_hub.values()
        for task in hub_tasks
    ]
    tasks.sort(
        key=lambda task: (
            task.hub_trip_index if task.hub_trip_index is not None else 0,
            hub_order.get(task.start_road, 999999),
            task.seat_index_in_trip if task.seat_index_in_trip is not None else 0,
        )
    )

    hub_vehicle_counts = {hub: len(rows) for hub, rows in grouped_rows.items()}
    hub_trip_counts = {
        hub: (count + batch_size_per_hub - 1) // batch_size_per_hub
        for hub, count in hub_vehicle_counts.items()
    }
    metadata = {
        "mode": f"hub_batch_dispatch_from_od_{source_format}",
        f"od_{source_format}": str(Path(source_path).expanduser().resolve()),
        "hub_roads": hub_roads,
        "total_vehicle_count": len(tasks),
        "batch_size_per_hub": batch_size_per_hub,
        "hub_vehicle_counts": hub_vehicle_counts,
        "hub_trip_counts": hub_trip_counts,
        "max_trip_index": max_trip_index,
    }
    return tasks, metadata


def build_dispatch_tasks_from_od_json(
    od_json: str,
    fixed_hub_roads: List[str],
    batch_size_per_hub: int,
) -> Tuple[List[DispatchTask], Dict[str, Any]]:
    """从正式 OD JSON 生成接驳点分批发车任务。"""

    return build_dispatch_tasks_from_od_rows(
        od_rows=load_od_rows_from_json(od_json),
        source_path=od_json,
        source_format="json",
        fixed_hub_roads=fixed_hub_roads,
        batch_size_per_hub=batch_size_per_hub,
    )


def build_dispatch_tasks_from_od_csv(
    od_csv: str,
    fixed_hub_roads: List[str],
    batch_size_per_hub: int,
) -> Tuple[List[DispatchTask], Dict[str, Any]]:
    """从正式 OD CSV 生成接驳点分批发车任务。"""

    return build_dispatch_tasks_from_od_rows(
        od_rows=load_od_rows_from_csv(od_csv),
        source_path=od_csv,
        source_format="csv",
        fixed_hub_roads=fixed_hub_roads,
        batch_size_per_hub=batch_size_per_hub,
    )


def build_dispatch_tasks_from_od_pairs(od_pairs: List[Tuple[str, str]]) -> List[DispatchTask]:
    """把旧的随机/内置 OD 列表包装成任务，保持老测试模式可用。"""

    tasks: List[DispatchTask] = []
    for idx, (start_road, end_road) in enumerate(od_pairs, start=1):
        tasks.append(
            DispatchTask(
                od_index=idx,
                source_person_id=None,
                start_road=str(start_road),
                end_road=str(end_road),
                hub_id=str(start_road),
                hub_trip_index=0,
                seat_index_in_trip=idx,
                planned_departure_step=None,
            )
        )
    return tasks


def assign_planned_departure_steps(
    tasks: List[DispatchTask],
    first_departure_step: int,
    step_interval_per_batch: int,
) -> None:
    """给正式疏散任务写入每辆车的计划发车 step。"""

    if step_interval_per_batch <= 0:
        raise RuntimeError("--step_interval_per_batch must be greater than 0")

    for task in tasks:
        trip_index = task.hub_trip_index or 0
        task.planned_departure_step = int(first_departure_step + trip_index * step_interval_per_batch)


async def resolve_start_end_lanes(env: Env, start_road: str, end_road: str) -> Tuple[int, int]:
    """把 road_id 转换成 AddPerson 需要的 lane_id。"""

    # AddPerson 的出发点/终点使用 lane_position，所以不能直接传 road_id。
    start_lanes = await env.eng.get_lane_id_of_road(start_road)
    end_lanes = await env.eng.get_lane_id_of_road(end_road)
    if not start_lanes or not end_lanes:
        raise RuntimeError(f"Cannot resolve lane ids for OD: {start_road} -> {end_road}")
    return int(start_lanes[0]), int(end_lanes[0])


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
    - schedules[0].departure_time：车辆计划出发的模拟器绝对时间/step。
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
        description="Step-based hub evacuation simulation with batched AddPerson dispatch."
    )
    parser.add_argument("--mongo_uri", default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017")
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")

    # 正式疏散输入：每行 OD 的起点必须是固定接驳点之一，终点是对应车辆的 home。
    parser.add_argument(
        "--od_json",
        default=None,
        help="Formal evacuation OD JSON. Each row should contain origin_road and destination_road.",
    )
    parser.add_argument(
        "--od_csv",
        default=None,
        help=(
            "Formal evacuation OD CSV. If omitted, DEFAULT_OD_CSV in code is used. "
            "smart_od_trips.csv should contain Person_ID,O_Road_ID,D_Road_ID."
        ),
    )
    parser.add_argument(
        "--hub_roads",
        nargs="+",
        default=None,
        help="Fixed shuttle hub road ids. If omitted, FIXED_HUB_ROADS in code is used.",
    )
    parser.add_argument(
        "--batch_size_per_hub",
        type=int,
        default=BATCH_SIZE_PER_HUB,
        help="Vehicles dispatched per hub per trip.",
    )
    parser.add_argument(
        "--step_interval_per_batch",
        type=int,
        default=STEP_INTERVAL_PER_BATCH,
        help="Dispatch interval between trips of the same hub, in local steps.",
    )
    parser.add_argument(
        "--dispatch_gap_tolerance_steps",
        type=int,
        default=DEFAULT_DISPATCH_GAP_TOLERANCE_STEPS,
        help="Allowed error when validating observed dispatch gaps, in steps.",
    )
    # 兼容旧命令。传了旧参数时，后面会映射到 step 参数。
    parser.add_argument("--headway_seconds", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--dispatch_gap_tolerance_seconds", type=int, default=None, help=argparse.SUPPRESS)

    # OD 生成参数：默认使用 DEFAULT_ORIGIN_ROADS 中的起点，每个起点随机抽取 50 个终点。
    parser.add_argument(
        "--origin_roads",
        nargs="+",
        default=None,
        help="One or more origin road ids. If omitted, DEFAULT_ORIGIN_ROADS in code is used.",
    )
    parser.add_argument("--net_xml", default=DEFAULT_NET_XML, help="SUMO net.xml path used to sample destination roads.")
    parser.add_argument(
        "--destination_count",
        type=int,
        default=DEFAULT_DESTINATION_COUNT,
        help="Number of random destination roads per origin.",
    )
    parser.add_argument(
        "--random_seed",
        type=int,
        default=None,
        help="Optional random seed. Set it to reproduce the same destination sample.",
    )
    parser.add_argument(
        "--use_builtin_ods",
        action="store_true",
        help="Use the hard-coded DEFAULT_OD_PAIRS instead of sampling destinations from net_xml.",
    )

    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument(
        "--departure_stagger_steps",
        type=int,
        default=0,
        help="Additional delay between consecutive OD probes (0 means all depart together)",
    )
    parser.add_argument(
        "--max_global_step",
        type=int,
        default=MAX_GLOBAL_STEP,
        help="Global maximum local step. Vehicles not reaching D by this step are marked unreachable.",
    )
    parser.add_argument("--max_probe_steps", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--query_concurrency",
        type=int,
        default=DEFAULT_QUERY_CONCURRENCY,
        help="Concurrent get_road_id_with_vehicle queries per tracking tick. Use 1 to force serial queries.",
    )
    parser.add_argument(
        "--track_every_steps",
        type=int,
        default=DEFAULT_TRACK_EVERY_STEPS,
        help="Query vehicle locations every N simulator steps. Larger values are faster but coarser.",
    )
    parser.add_argument(
        "--report_json",
        default=str(SCRIPT_DIR / "muti_res_v3.json"),
        help="Save summary and result rows to one JSON report file. Default: script directory.",
    )
    parser.add_argument(
        "--rows_print_limit",
        type=int,
        default=DEFAULT_ROWS_PRINT_LIMIT,
        help="Print only the first N result rows to terminal. Use 0 to suppress row printing.",
    )
    return parser.parse_args()


async def inject_all_probes(
    env: Env,
    person_template: Dict[str, Any],
    dispatch_tasks: List[DispatchTask],
    sim_start_step: int,
    departure_delay_steps: int,
    departure_stagger_steps: int,
) -> List[ProbeState]:
    """为所有 OD 各注入一辆 AddPerson 探针车。"""

    # 这里完全不用读取模拟器当前时间，直接根据本地 step 计数换算绝对 departure_time。
    base_departure_step = int(sim_start_step + departure_delay_steps)

    states: List[ProbeState] = []
    lane_cache: Dict[str, int] = {}
    for inject_index, task in enumerate(dispatch_tasks, start=1):
        state = ProbeState(
            od_index=task.od_index,
            source_person_id=task.source_person_id,
            start_road=str(task.start_road),
            end_road=str(task.end_road),
            hub_id=task.hub_id,
            hub_trip_index=task.hub_trip_index,
            seat_index_in_trip=task.seat_index_in_trip,
            planned_departure_step=task.planned_departure_step,
            start_lane_id=None,
            end_lane_id=None,
            person_id=None,
            departure_step=None,
            started_step=None,
            arrived_step=None,
            travel_steps=None,
            arrival_reason="not_injected",
            last_road=None,
            tracking_error_count=0,
            last_tracking_error=None,
        )

        try:
            # 先把 road_id 转成 lane_id，再构造 AddPerson 请求。
            # lane_cache 可以显著减少多起点场景下的重复 lane 查询。
            start_lane_id = await resolve_lane_with_cache(env, state.start_road, lane_cache)
            end_lane_id = await resolve_lane_with_cache(env, state.end_road, lane_cache)
            state.start_lane_id = start_lane_id
            state.end_lane_id = end_lane_id
            if task.planned_departure_step is None:
                # 旧的随机 OD 测试模式：继续支持 departure_stagger_steps。
                departure_step = departure_delay_steps + (inject_index - 1) * departure_stagger_steps
            else:
                departure_step = int(task.planned_departure_step)
            state.planned_departure_step = departure_step
            absolute_departure_time = base_departure_step + (departure_step - departure_delay_steps)
            payload = build_probe_person_auto_route(
                template=person_template,
                start_lane_id=start_lane_id,
                end_lane_id=end_lane_id,
                departure_time=absolute_departure_time,
            )

            # 核心调用：把 person payload 加入模拟器。
            add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
            person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
            if not isinstance(person_id, int):
                raise RuntimeError(f"AddPerson failed: {add_resp}")

            state.person_id = person_id
            state.departure_step = departure_step
            state.arrival_reason = "not_arrived"
        except Exception as exc:
            state.arrival_reason = f"inject_exception: {exc}"

        states.append(state)

    return states


async def track_all_probes(
    env: Env,
    states: List[ProbeState],
    max_global_step: int,
    query_concurrency: int,
    track_every_steps: int,
) -> Dict[str, Any]:
    """推进模拟器并跟踪所有探针车，直到到达、消失或达到全局 step 上限。"""

    if query_concurrency <= 0:
        raise RuntimeError("--query_concurrency must be greater than 0")
    if track_every_steps <= 0:
        raise RuntimeError("--track_every_steps must be greater than 0")
    if max_global_step < 0:
        raise RuntimeError("--max_global_step must be greater than or equal to 0")

    diagnostics: Dict[str, Any] = {
        "sim_steps_executed": 0,
        "tracking_ticks_executed": 0,
        "vehicle_query_count": 0,
        "early_stop_reason": None,
        "track_every_steps": track_every_steps,
        "max_global_step": max_global_step,
        "final_step": 0,
    }

    active_ids = {s.person_id for s in states if isinstance(s.person_id, int)}
    if not active_ids:
        diagnostics["early_stop_reason"] = "no_injected_vehicles"
        return diagnostics

    semaphore = asyncio.Semaphore(query_concurrency)

    async def query_current_road(state: ProbeState) -> Tuple[ProbeState, Optional[str], Optional[str]]:
        try:
            async with semaphore:
                current_road = await env.eng.get_road_id_with_vehicle(state.person_id)
            current_road_str = str(current_road) if current_road is not None else None
            return state, current_road_str, None
        except Exception as exc:
            return state, None, str(exc)

    try:
        current_step = 0
        while True:
            diagnostics["final_step"] = current_step
            should_query = (current_step % track_every_steps == 0) or (current_step >= max_global_step)

            # 还没到计划发车时间的车辆不查询位置。
            # 1500 辆分批发车时，这能避免大量“未来车辆”的无效接口调用。
            active_states = [
                state
                for state in states
                if state.person_id in active_ids
                and (state.departure_step is None or state.departure_step <= current_step)
            ]
            if should_query and active_states:
                diagnostics["tracking_ticks_executed"] += 1
                diagnostics["vehicle_query_count"] += len(active_states)
                query_results = await asyncio.gather(*(query_current_road(state) for state in active_states))

                for state, current_road_str, query_error in query_results:
                    if query_error is not None:
                        # 单辆车查询失败不应该打断整个 50 组 OD 的跟踪。
                        # 查询失败不能当作车辆消失，否则会误判到达/完成。
                        state.tracking_error_count += 1
                        state.last_tracking_error = query_error
                        continue

                    # 第一次查到车辆出现在路网上，记为 started_step。
                    if state.started_step is None and current_road_str is not None:
                        state.started_step = current_step

                    # 明确观测到车辆位于目标 road，认为到达。
                    if state.started_step is not None and current_road_str == state.end_road:
                        state.arrived_step = current_step
                        state.travel_steps = max(0, state.arrived_step - state.started_step)
                        state.arrival_reason = "reach_destination_road"
                        active_ids.discard(state.person_id)
                        continue

                    # 车辆出发后从 active 查询中消失。原脚本把这种情况也作为可计算到达。
                    if state.started_step is not None and current_road_str is None and state.last_road is not None:
                        state.arrived_step = current_step
                        state.travel_steps = max(0, state.arrived_step - state.started_step)
                        state.arrival_reason = "disappeared_after_departure"
                        active_ids.discard(state.person_id)
                        continue

                    state.last_road = current_road_str

            if not active_ids:
                diagnostics["early_stop_reason"] = "all_vehicles_completed"
                break

            if current_step >= max_global_step:
                diagnostics["early_stop_reason"] = "max_global_step_reached"
                break

            await env.step_once()
            diagnostics["sim_steps_executed"] += 1
            current_step += 1
    finally:
        # 即使 step 或查询过程被外层异常打断，也要把已经注入但未完成的车辆状态收口。
        for state in states:
            if isinstance(state.person_id, int) and state.arrived_step is None and state.arrival_reason == "not_arrived":
                state.arrival_reason = "max_global_step_reached"

        finalize_travel_steps(states)
    return diagnostics


def finalize_travel_steps(states: List[ProbeState]) -> None:
    """输出前兜底补算每辆车的 travel_steps。"""

    for state in states:
        if state.started_step is not None and state.arrived_step is not None:
            state.travel_steps = max(0, state.arrived_step - state.started_step)


def build_summary(
    states: List[ProbeState],
    fatal_error: Optional[str] = None,
    final_step: Optional[int] = None,
    max_global_step: Optional[int] = None,
) -> Dict[str, Any]:
    """生成和终端 Summary 板块一致的统计信息。"""

    finalize_travel_steps(states)

    total = len(states)
    injected = sum(1 for s in states if isinstance(s.person_id, int))
    arrived_vehicle_count = sum(1 for s in states if s.arrived_step is not None)
    reached_destination_road_count = sum(1 for s in states if s.arrival_reason == "reach_destination_road")
    disappeared_after_departure_count = sum(1 for s in states if s.arrival_reason == "disappeared_after_departure")
    unreachable_vehicle_count = sum(1 for s in states if s.arrived_step is None and isinstance(s.person_id, int))
    inject_fail = sum(1 for s in states if s.arrival_reason.startswith("inject_exception:"))
    tracking_error_ods = sum(1 for s in states if s.tracking_error_count > 0)
    travel_steps_values = [s.travel_steps for s in states if s.travel_steps is not None]
    arrived_steps = [s.arrived_step for s in states if s.arrived_step is not None]
    last_arrived_step = max(arrived_steps) if arrived_steps else None
    max_travel_steps = max(travel_steps_values) if travel_steps_values else None
    total_travel_steps = sum(travel_steps_values)

    summary: Dict[str, Any] = {
        "total_ods": total,
        "total_vehicles": total,
        "hub_count": len({s.hub_id for s in states if s.hub_id}),
        "injected_ok": injected,
        "arrived_vehicle_count": arrived_vehicle_count,
        "reached_destination_road_count": reached_destination_road_count,
        "disappeared_after_departure_count": disappeared_after_departure_count,
        "unreachable_vehicle_count": unreachable_vehicle_count,
        "inject_failures": inject_fail,
        "tracking_error_ods": tracking_error_ods,
        "max_travel_steps": max_travel_steps,
        "total_travel_steps": total_travel_steps,
        "last_arrived_step": last_arrived_step,
        "final_step": final_step,
        "max_global_step": max_global_step,
    }
    if fatal_error is not None:
        summary["fatal_error"] = fatal_error
    return summary


def build_hub_summary(states: List[ProbeState]) -> Dict[str, Any]:
    """按接驳点汇总车辆数、趟数和到达情况。"""

    hub_summary: Dict[str, Any] = {}
    for state in states:
        hub_id = state.hub_id or state.start_road
        bucket = hub_summary.setdefault(
            hub_id,
            {
                "vehicle_count": 0,
                "trip_count": 0,
                "first_planned_departure_step": None,
                "last_planned_departure_step": None,
                "last_arrived_step": None,
                "arrived_vehicle_count": 0,
                "unreachable_vehicle_count": 0,
            },
        )
        bucket["vehicle_count"] += 1
        if state.hub_trip_index is not None:
            bucket["trip_count"] = max(bucket["trip_count"], state.hub_trip_index + 1)
        if state.planned_departure_step is not None:
            if bucket["first_planned_departure_step"] is None:
                bucket["first_planned_departure_step"] = state.planned_departure_step
            bucket["first_planned_departure_step"] = min(
                bucket["first_planned_departure_step"], state.planned_departure_step
            )
            bucket["last_planned_departure_step"] = max(
                bucket["last_planned_departure_step"] or state.planned_departure_step,
                state.planned_departure_step,
            )
        if state.arrived_step is not None:
            bucket["last_arrived_step"] = max(bucket["last_arrived_step"] or state.arrived_step, state.arrived_step)
            bucket["arrived_vehicle_count"] += 1
        else:
            bucket["unreachable_vehicle_count"] += 1

    return hub_summary


def build_dispatch_validation(
    states: List[ProbeState],
    step_interval_per_batch: int,
    tolerance_steps: int,
) -> Dict[str, Any]:
    """用 started_step 验证相邻两趟是否近似相隔 step_interval_per_batch。"""

    trips_by_hub: Dict[str, Dict[int, List[ProbeState]]] = {}
    for state in states:
        if state.hub_trip_index is None:
            continue
        hub_id = state.hub_id or state.start_road
        trips_by_hub.setdefault(hub_id, {}).setdefault(state.hub_trip_index, []).append(state)

    hub_results: Dict[str, Any] = {}
    all_gap_checks: List[bool] = []
    for hub_id, trips in trips_by_hub.items():
        trip_summaries: List[Dict[str, Any]] = []
        for trip_index in sorted(trips):
            trip_states = trips[trip_index]
            planned_steps = [
                state.planned_departure_step
                for state in trip_states
                if state.planned_departure_step is not None
            ]
            started_steps = [
                state.started_step
                for state in trip_states
                if state.started_step is not None
            ]
            trip_summaries.append(
                {
                    "hub_trip_index": trip_index,
                    "planned_departure_step": min(planned_steps) if planned_steps else None,
                    "first_started_step": min(started_steps) if started_steps else None,
                    "last_started_step": max(started_steps) if started_steps else None,
                    "planned_vehicle_count": len(trip_states),
                    "started_vehicle_count": len(started_steps),
                }
            )

        gap_checks: List[Dict[str, Any]] = []
        for prev_trip, next_trip in zip(trip_summaries, trip_summaries[1:]):
            planned_gap = (
                next_trip["planned_departure_step"] - prev_trip["planned_departure_step"]
                if next_trip["planned_departure_step"] is not None
                and prev_trip["planned_departure_step"] is not None
                else None
            )
            observed_first_gap = (
                next_trip["first_started_step"] - prev_trip["first_started_step"]
                if next_trip["first_started_step"] is not None
                and prev_trip["first_started_step"] is not None
                else None
            )
            observed_last_gap = (
                next_trip["last_started_step"] - prev_trip["last_started_step"]
                if next_trip["last_started_step"] is not None
                and prev_trip["last_started_step"] is not None
                else None
            )
            planned_gap_passed = planned_gap == step_interval_per_batch
            observed_gap_passed = (
                observed_first_gap is not None
                and abs(observed_first_gap - step_interval_per_batch) <= tolerance_steps
            )
            gap_passed = planned_gap_passed and observed_gap_passed
            all_gap_checks.append(gap_passed)
            gap_checks.append(
                {
                    "from_trip_index": prev_trip["hub_trip_index"],
                    "to_trip_index": next_trip["hub_trip_index"],
                    "planned_gap_steps": planned_gap,
                    "observed_first_start_gap_steps": observed_first_gap,
                    "observed_last_start_gap_steps": observed_last_gap,
                    "planned_gap_passed": planned_gap_passed,
                    "observed_gap_passed": observed_gap_passed,
                    "passed": gap_passed,
                }
            )

        hub_results[hub_id] = {
            "trips": trip_summaries,
            "gap_checks": gap_checks,
            "passed": all(check["passed"] for check in gap_checks) if gap_checks else True,
        }

    return {
        "step_interval_per_batch": step_interval_per_batch,
        "tolerance_steps": tolerance_steps,
        "passed": all(all_gap_checks) if all_gap_checks else True,
        "hubs": hub_results,
    }


def state_to_row(state: ProbeState) -> Dict[str, Any]:
    """把一个 ProbeState 转成 RESULT_ROWS_JSON 中的一行。"""

    if state.travel_steps is None and state.started_step is not None and state.arrived_step is not None:
        state.travel_steps = max(0, state.arrived_step - state.started_step)

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
        "planned_departure_step": state.planned_departure_step,
        "start_lane_id": state.start_lane_id,
        "end_lane_id": state.end_lane_id,
        "person_id": state.person_id,
        "departure_step": state.departure_step,
        "started_step": state.started_step,
        "arrived_step": state.arrived_step,
        "travel_steps": state.travel_steps,
        "arrival_reason": state.arrival_reason,
        "tracking_error_count": state.tracking_error_count,
        "last_tracking_error": state.last_tracking_error,
    }


def write_report_json(
    report_json: Optional[str],
    summary: Dict[str, Any],
    hub_summary: Dict[str, Any],
    dispatch_validation: Dict[str, Any],
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
        "hub_summary": hub_summary,
        "dispatch_validation": dispatch_validation,
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
    final_step: Optional[int] = None,
    max_global_step: int = MAX_GLOBAL_STEP,
    step_interval_per_batch: int = STEP_INTERVAL_PER_BATCH,
    dispatch_gap_tolerance_steps: int = DEFAULT_DISPATCH_GAP_TOLERANCE_STEPS,
    rows_print_limit: int = DEFAULT_ROWS_PRINT_LIMIT,
) -> None:
    finalize_travel_steps(states)
    summary = build_summary(
        states,
        fatal_error=fatal_error,
        final_step=final_step,
        max_global_step=max_global_step,
    )
    hub_summary = build_hub_summary(states)
    dispatch_validation = build_dispatch_validation(
        states,
        step_interval_per_batch=step_interval_per_batch,
        tolerance_steps=dispatch_gap_tolerance_steps,
    )
    rows = [state_to_row(s) for s in states]
    od_generation = od_generation or {}
    runtime_diagnostics = runtime_diagnostics or {}

    print("=== Multi-OD AddPerson Result Summary ===")
    print(f"total_ods:        {summary['total_ods']}")
    print(f"injected_ok:      {summary['injected_ok']}")
    print(f"arrived_count:    {summary['arrived_vehicle_count']}")
    print(f"reached_d_count:  {summary['reached_destination_road_count']}")
    print(f"disappeared_cnt:  {summary['disappeared_after_departure_count']}")
    print(f"unreachable_cnt:  {summary['unreachable_vehicle_count']}")
    print(f"max_travel_step:  {summary['max_travel_steps']}")
    print(f"total_travel:     {summary['total_travel_steps']}")
    print(f"last_arrived:     {summary['last_arrived_step']}")
    print(f"final_step:       {summary['final_step']}")
    print(f"max_global_step:  {summary['max_global_step']}")
    print(f"inject_failures:  {summary['inject_failures']}")
    print(f"tracking_errors:  {summary['tracking_error_ods']}")
    print(f"dispatch_passed:  {dispatch_validation['passed']}")
    print(f"sim_steps:        {runtime_diagnostics.get('sim_steps_executed')}")
    print(f"tracking_ticks:   {runtime_diagnostics.get('tracking_ticks_executed')}")
    print(f"vehicle_queries:  {runtime_diagnostics.get('vehicle_query_count')}")
    print(f"early_stop:       {runtime_diagnostics.get('early_stop_reason')}")
    if "fatal_error" in summary:
        print(f"fatal_error:      {summary['fatal_error']}")

    print("===HUB_SUMMARY_JSON===")
    print(json.dumps(hub_summary, ensure_ascii=False, indent=2))
    print("===DISPATCH_VALIDATION_JSON===")
    print(json.dumps(dispatch_validation, ensure_ascii=False, indent=2))
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
        hub_summary=hub_summary,
        dispatch_validation=dispatch_validation,
        runtime_diagnostics=runtime_diagnostics,
        rows=rows,
        od_generation=od_generation,
    )


async def amain(args: argparse.Namespace) -> int:
    """真实运行入口：连接模拟器、warmup、注入车辆、跟踪并输出报告。"""

    if args.interval != 1:
        raise RuntimeError("This step-based V3 expects --interval 1, because 1 step must equal 1 second.")

    if args.headway_seconds is not None:
        args.step_interval_per_batch = args.headway_seconds
    if args.dispatch_gap_tolerance_seconds is not None:
        args.dispatch_gap_tolerance_steps = args.dispatch_gap_tolerance_seconds
    if args.max_probe_steps is not None:
        args.max_global_step = args.max_probe_steps

    first_departure_step: Optional[int] = None
    dispatch_span_steps = 0
    effective_max_global_step = args.max_global_step
    effective_od_csv = args.od_csv
    od_generation: Dict[str, Any] = {}
    env: Optional[Env] = None
    if not args.od_json and not args.use_builtin_ods:
        effective_od_csv = effective_od_csv or DEFAULT_OD_CSV

    if args.od_json and effective_od_csv:
        raise RuntimeError("Use only one formal OD input: --od_json or --od_csv")

    if effective_od_csv:
        hub_roads = [str(road_id) for road_id in (args.hub_roads or FIXED_HUB_ROADS)]
        dispatch_tasks, od_generation = build_dispatch_tasks_from_od_csv(
            od_csv=effective_od_csv,
            fixed_hub_roads=hub_roads,
            batch_size_per_hub=args.batch_size_per_hub,
        )
        dispatch_span_steps = int(od_generation.get("max_trip_index", 0)) * args.step_interval_per_batch
    elif args.od_json:
        hub_roads = [str(road_id) for road_id in (args.hub_roads or FIXED_HUB_ROADS)]
        dispatch_tasks, od_generation = build_dispatch_tasks_from_od_json(
            od_json=args.od_json,
            fixed_hub_roads=hub_roads,
            batch_size_per_hub=args.batch_size_per_hub,
        )
        dispatch_span_steps = int(od_generation.get("max_trip_index", 0)) * args.step_interval_per_batch
    elif args.use_builtin_ods:
        od_pairs = DEFAULT_OD_PAIRS
        dispatch_tasks = build_dispatch_tasks_from_od_pairs(od_pairs)
        od_generation: Dict[str, Any] = {
            "mode": "builtin_default_od_pairs",
            "origin_roads": sorted({start_road for start_road, _ in DEFAULT_OD_PAIRS}),
            "destination_count": len(DEFAULT_OD_PAIRS),
            "destinations": [end_road for _, end_road in DEFAULT_OD_PAIRS],
        }
    else:
        origin_roads = [str(road_id) for road_id in (args.origin_roads or DEFAULT_ORIGIN_ROADS)]
        od_pairs, od_generation = build_random_od_pairs_from_net(
            net_xml=args.net_xml,
            origin_roads=origin_roads,
            destination_count=args.destination_count,
            random_seed=args.random_seed,
        )
        dispatch_tasks = build_dispatch_tasks_from_od_pairs(od_pairs)

    if (args.od_json or effective_od_csv) and effective_max_global_step < dispatch_span_steps:
        od_generation["tracking_horizon_warning"] = (
            "max_global_step is shorter than the dispatch span; "
            "some later trips may be marked unreachable before being observed."
        )

    od_generation["tracking_config"] = {
        "interval": args.interval,
        "max_global_step": effective_max_global_step,
        "tracking_horizon_steps": effective_max_global_step,
        "query_concurrency": args.query_concurrency,
        "track_every_steps": args.track_every_steps,
        "dispatch_gap_tolerance_steps": args.dispatch_gap_tolerance_steps,
    }
    od_generation["dispatch_config"] = {
        "batch_size_per_hub": args.batch_size_per_hub,
        "step_interval_per_batch": args.step_interval_per_batch,
        "departure_delay_steps": args.departure_delay_steps,
    }

    # 给模拟器总步数留足空间：warmup + 出发延迟 + 跟踪步数 + 冗余。
    step_count = args.warmup_steps + args.departure_delay_steps + effective_max_global_step + 60
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
        "max_global_step": effective_max_global_step,
        "final_step": 0,
    }
    exit_code = 0

    try:
        # 从 Mongo 取一个已有 person 作为模板，然后每个 OD 复制并改写起终点。
        person_template = load_person_template_from_mongo(
            args.mongo_uri, args.agent_db, args.agent_coll
        )

        # 注入探针车前，先让背景交通流/信控策略跑一段。
        for _ in range(args.warmup_steps):
            await env.step_once()

        first_departure_step = args.departure_delay_steps
        if args.od_json or effective_od_csv:
            assign_planned_departure_steps(
                tasks=dispatch_tasks,
                first_departure_step=first_departure_step,
                step_interval_per_batch=args.step_interval_per_batch,
            )
            planned_departures = [
                task.planned_departure_step
                for task in dispatch_tasks
                if task.planned_departure_step is not None
            ]
            od_generation["dispatch_config"]["first_departure_step"] = first_departure_step
            od_generation["dispatch_config"]["last_planned_departure_step"] = (
                max(planned_departures) if planned_departures else None
            )

        states = await inject_all_probes(
            env=env,
            person_template=person_template,
            dispatch_tasks=dispatch_tasks,
            sim_start_step=args.start + args.warmup_steps,
            departure_delay_steps=args.departure_delay_steps,
            departure_stagger_steps=args.departure_stagger_steps,
        )

        runtime_diagnostics = await track_all_probes(
            env=env,
            states=states,
            max_global_step=effective_max_global_step,
            query_concurrency=args.query_concurrency,
            track_every_steps=args.track_every_steps,
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
            final_step=runtime_diagnostics.get("final_step"),
            max_global_step=effective_max_global_step,
            step_interval_per_batch=args.step_interval_per_batch,
            dispatch_gap_tolerance_steps=args.dispatch_gap_tolerance_steps,
            rows_print_limit=args.rows_print_limit,
        )
        try:
            if env is not None:
                env.eng._stop_simulator()
        except Exception as stop_exc:
            print(f"WARNING: simulator stop failed and was ignored: {stop_exc}")

    return exit_code


def main() -> int:
    """命令行入口。"""

    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
