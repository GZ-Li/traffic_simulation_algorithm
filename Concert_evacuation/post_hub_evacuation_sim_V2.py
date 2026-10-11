#!/usr/bin/env python3
"""Multi-OD / hub evacuation test: inject many AddPerson vehicles in one simulation.

What this script does:
1. Connect simulator engine once.
2. In formal evacuation mode, read OD JSON and dispatch each hub in 50-car batches.
3. Write planned departure_time into AddPerson, so 300-second headway is simulator time.
4. Step simulation once per tick and track all probes in parallel.
5. Output ETA, hub summary, and dispatch validation as JSON.

This is faster than running one-OD scripts repeatedly.
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

# 版本标识：服务器上可以用 grep 检查，确认传的是带报告输出的 V2 版本。
SCRIPT_VERSION = "post_hub_evacuation_sim_V2_report_20260423"

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

# 接驳车发车间隔，单位是模拟器秒。300 秒就是 5 分钟。
HEADWAY_SECONDS = 300

# 校验相邻两趟实际上路间隔时允许的误差，单位是秒。
DEFAULT_DISPATCH_GAP_TOLERANCE_SECONDS = 10


# 每个起点随机生成多少个终点。
DEFAULT_DESTINATION_COUNT = 50

# 每次模拟步查询车辆位置时的并发数。服务器压力大时可通过 --query_concurrency 调小。
DEFAULT_QUERY_CONCURRENCY = 50

# 每推进多少个模拟步查询一次车辆位置。1 表示每步都查，数值越大越快但 ETA 越粗。
DEFAULT_TRACK_EVERY_STEPS = 1

# 如果正式疏散模式没有显式传 --max_probe_steps，默认最多跟踪 2 小时模拟时间。
DEFAULT_EVACUATION_MAX_PROBE_SECONDS = 7200

# 服务器上 trafficlight_rl 通常在当前脚本的上级目录里。
# 把上级目录加入 import 搜索路径后，才能导入 trafficlight_rl.engine。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# 备用 OD 列表：只有在显式传 --use_builtin_ods 时才使用。
# 默认逻辑会从 Shaped.net.xml 随机抽取终点 road id。
DEFAULT_OD_PAIRS: List[Tuple[str, str]] = [
   
    ("200000480", "200000387"),
    ("200000387", "200000480"),
    ("200000480", "200007001"),
    ("200007001", "200000480"),
    ("200000480", "200000869"),
    ("200000869", "200000480"),
    
    ("200000154", "200000387"),
    ("200000387", "200000154"),
    ("200000154", "200007001"),
    ("200007001", "200000154"),
    ("200000154", "200000869"),
    ("200000869", "200000154"),
    
    ("200000033", "200000387"),
    ("200000387", "200000033"),
    ("200000033", "200007001"),
    ("200007001", "200000033"),
    ("200000033", "200000869"),
    ("200000869", "200000033"),
]

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
                    planned_departure_time=None,
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
                planned_departure_time=None,
            )
        )
    return tasks


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
        description="Inject multiple OD probes in one simulation and collect ETA together."
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
        "--headway_seconds",
        type=int,
        default=HEADWAY_SECONDS,
        help="Dispatch interval between trips of the same hub, in simulator seconds.",
    )
    parser.add_argument(
        "--dispatch_gap_tolerance_seconds",
        type=int,
        default=DEFAULT_DISPATCH_GAP_TOLERANCE_SECONDS,
        help="Allowed error when validating observed dispatch gaps.",
    )

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
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument(
        "--departure_stagger_steps",
        type=int,
        default=0,
        help="Additional delay between consecutive OD probes (0 means all depart together)",
    )
    parser.add_argument(
        "--max_probe_steps",
        type=int,
        default=None,
        help="Max tracking steps after injection. Default: 60 for random tests, auto for --od_json.",
    )
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
        default=str(SCRIPT_DIR / "muti_res.json"),
        help="Save summary and result rows to one JSON report file. Default: script directory.",
    )
    return parser.parse_args()


async def inject_all_probes(
    env: Env,
    person_template: Dict[str, Any],
    dispatch_tasks: List[DispatchTask],
    departure_delay_steps: int,
    departure_stagger_steps: int,
) -> List[ProbeState]:
    """为所有 OD 各注入一辆 AddPerson 探针车。"""

    now = await env.eng.get_current_time()
    # departure_time 是模拟器绝对时间，不是现实世界时间。
    base_departure = int(now["t"]) + departure_delay_steps * env.step_size

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
            planned_departure_time=task.planned_departure_time,
            start_lane_id=None,
            end_lane_id=None,
            person_id=None,
            departure_time=None,
            started_time=None,
            arrived_time=None,
            eta_seconds=None,
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
            if task.planned_departure_time is None:
                # 旧的随机 OD 测试模式：继续支持 departure_stagger_steps。
                departure_time = base_departure + (inject_index - 1) * departure_stagger_steps * env.step_size
            else:
                # 正式疏散模式：这里把 5 分钟一趟的计划时间真正写入 AddPerson。
                departure_time = int(task.planned_departure_time)
            state.planned_departure_time = departure_time
            payload = build_probe_person_auto_route(
                template=person_template,
                start_lane_id=start_lane_id,
                end_lane_id=end_lane_id,
                departure_time=departure_time,
            )

            # 核心调用：把 person payload 加入模拟器。
            add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
            person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
            if not isinstance(person_id, int):
                raise RuntimeError(f"AddPerson failed: {add_resp}")

            state.person_id = person_id
            state.departure_time = departure_time
            state.arrival_reason = "not_arrived"
        except Exception as exc:
            state.arrival_reason = f"inject_exception: {exc}"

        states.append(state)

    return states


async def track_all_probes(
    env: Env,
    states: List[ProbeState],
    max_probe_steps: int,
    query_concurrency: int,
    track_every_steps: int,
) -> None:
    """推进模拟器并跟踪所有探针车，直到到达、消失或超过最大步数。"""

    if query_concurrency <= 0:
        raise RuntimeError("--query_concurrency must be greater than 0")
    if track_every_steps <= 0:
        raise RuntimeError("--track_every_steps must be greater than 0")

    active_ids = {s.person_id for s in states if isinstance(s.person_id, int)}
    if not active_ids:
        return

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
        for step_index in range(max_probe_steps):
            await env.step_once()
            should_query = (step_index % track_every_steps == 0) or (step_index == max_probe_steps - 1)
            if not should_query:
                continue

            sim_clock = await env.eng.get_current_time()
            now_t = int(sim_clock["t"])

            # 还没到计划发车时间的车辆不查询位置。
            # 1500 辆分批发车时，这能避免大量“未来车辆”的无效接口调用。
            active_states = [
                state
                for state in states
                if state.person_id in active_ids
                and (state.departure_time is None or state.departure_time <= now_t)
            ]
            if not active_states:
                continue

            query_results = await asyncio.gather(*(query_current_road(state) for state in active_states))

            for state, current_road_str, query_error in query_results:
                if query_error is not None:
                    # 单辆车查询失败不应该打断整个 50 组 OD 的跟踪。
                    # 查询失败不能当作车辆消失，否则会误判到达/完成。
                    state.tracking_error_count += 1
                    state.last_tracking_error = query_error
                    continue

                # 第一次查到车辆出现在路网上，记为 started_time。
                if state.started_time is None and current_road_str is not None:
                    state.started_time = now_t

                # 明确观测到车辆位于目标 road，认为到达。
                if state.started_time is not None and current_road_str == state.end_road:
                    state.arrived_time = now_t
                    state.eta_seconds = max(0, state.arrived_time - state.started_time)
                    state.arrival_reason = "reach_destination_road"
                    active_ids.discard(state.person_id)
                    continue

                # 车辆出发后从 active 查询中消失。原脚本把这种情况也作为可计算 ETA。
                if state.started_time is not None and current_road_str is None and state.last_road is not None:
                    state.arrived_time = now_t
                    state.eta_seconds = max(0, state.arrived_time - state.started_time)
                    state.arrival_reason = "disappeared_after_departure"
                    active_ids.discard(state.person_id)
                    continue

                state.last_road = current_road_str

            if not active_ids:
                break
    finally:
        # 即使 step 或查询过程被外层异常打断，也要把已经注入但未完成的车辆状态收口，
        # 避免输出里长期停留在 not_arrived，难以判断原因。
        for state in states:
            if isinstance(state.person_id, int) and state.arrived_time is None and state.arrival_reason == "not_arrived":
                state.arrival_reason = "max_probe_steps_reached_no_eta"

        finalize_eta_fields(states)


def finalize_eta_fields(states: List[ProbeState]) -> None:
    """输出前兜底补算 ETA。

    如果跟踪过程中某个接口异常，track_all_probes 可能还没执行到最后的统一补算。
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
                "first_planned_departure_time": None,
                "last_planned_departure_time": None,
                "last_arrived_time": None,
                "eta_sum_seconds": 0,
                "eta_available": 0,
                "missing_eta": 0,
            },
        )
        bucket["vehicle_count"] += 1
        if state.hub_trip_index is not None:
            bucket["trip_count"] = max(bucket["trip_count"], state.hub_trip_index + 1)
        if state.planned_departure_time is not None:
            if bucket["first_planned_departure_time"] is None:
                bucket["first_planned_departure_time"] = state.planned_departure_time
            bucket["first_planned_departure_time"] = min(
                bucket["first_planned_departure_time"], state.planned_departure_time
            )
            bucket["last_planned_departure_time"] = max(
                bucket["last_planned_departure_time"] or state.planned_departure_time,
                state.planned_departure_time,
            )
        if state.arrived_time is not None:
            bucket["last_arrived_time"] = max(bucket["last_arrived_time"] or state.arrived_time, state.arrived_time)
        if state.eta_seconds is None:
            bucket["missing_eta"] += 1
        else:
            bucket["eta_sum_seconds"] += state.eta_seconds
            bucket["eta_available"] += 1

    return hub_summary


def build_dispatch_validation(
    states: List[ProbeState],
    headway_seconds: int,
    tolerance_seconds: int,
) -> Dict[str, Any]:
    """用 started_time 验证相邻两趟是否近似相隔 headway_seconds。"""

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
            planned_times = [
                state.planned_departure_time
                for state in trip_states
                if state.planned_departure_time is not None
            ]
            started_times = [
                state.started_time
                for state in trip_states
                if state.started_time is not None
            ]
            trip_summaries.append(
                {
                    "hub_trip_index": trip_index,
                    "planned_departure_time": min(planned_times) if planned_times else None,
                    "first_started_time": min(started_times) if started_times else None,
                    "last_started_time": max(started_times) if started_times else None,
                    "planned_vehicle_count": len(trip_states),
                    "started_vehicle_count": len(started_times),
                }
            )

        gap_checks: List[Dict[str, Any]] = []
        for prev_trip, next_trip in zip(trip_summaries, trip_summaries[1:]):
            planned_gap = (
                next_trip["planned_departure_time"] - prev_trip["planned_departure_time"]
                if next_trip["planned_departure_time"] is not None
                and prev_trip["planned_departure_time"] is not None
                else None
            )
            observed_first_gap = (
                next_trip["first_started_time"] - prev_trip["first_started_time"]
                if next_trip["first_started_time"] is not None
                and prev_trip["first_started_time"] is not None
                else None
            )
            observed_last_gap = (
                next_trip["last_started_time"] - prev_trip["last_started_time"]
                if next_trip["last_started_time"] is not None
                and prev_trip["last_started_time"] is not None
                else None
            )
            planned_gap_passed = planned_gap == headway_seconds
            observed_gap_passed = (
                observed_first_gap is not None
                and abs(observed_first_gap - headway_seconds) <= tolerance_seconds
            )
            gap_passed = planned_gap_passed and observed_gap_passed
            all_gap_checks.append(gap_passed)
            gap_checks.append(
                {
                    "from_trip_index": prev_trip["hub_trip_index"],
                    "to_trip_index": next_trip["hub_trip_index"],
                    "planned_gap_seconds": planned_gap,
                    "observed_first_start_gap": observed_first_gap,
                    "observed_last_start_gap": observed_last_gap,
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
        "headway_seconds": headway_seconds,
        "tolerance_seconds": tolerance_seconds,
        "passed": all(all_gap_checks) if all_gap_checks else True,
        "hubs": hub_results,
    }


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
        "eta_seconds": state.eta_seconds,
        "arrival_reason": state.arrival_reason,
        "tracking_error_count": state.tracking_error_count,
        "last_tracking_error": state.last_tracking_error,
    }


def write_report_json(
    report_json: Optional[str],
    summary: Dict[str, Any],
    hub_summary: Dict[str, Any],
    dispatch_validation: Dict[str, Any],
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
    first_departure_time: Optional[int] = None,
    headway_seconds: int = HEADWAY_SECONDS,
    dispatch_gap_tolerance_seconds: int = DEFAULT_DISPATCH_GAP_TOLERANCE_SECONDS,
) -> None:
    finalize_eta_fields(states)
    summary = build_summary(
        states,
        fatal_error=fatal_error,
        first_departure_time=first_departure_time,
    )
    hub_summary = build_hub_summary(states)
    dispatch_validation = build_dispatch_validation(
        states,
        headway_seconds=headway_seconds,
        tolerance_seconds=dispatch_gap_tolerance_seconds,
    )
    rows = [state_to_row(s) for s in states]
    od_generation = od_generation or {}

    print("=== Multi-OD AddPerson Result Summary ===")
    print(f"total_ods:        {summary['total_ods']}")
    print(f"injected_ok:      {summary['injected_ok']}")
    print(f"eta_available:    {summary['eta_available']}")
    print(f"max_eta_sec:      {summary['max_vehicle_eta_seconds']}")
    print(f"total_eta_sec:    {summary['total_eta_seconds']}")
    print(f"last_arrived_t:   {summary['last_arrived_time']}")
    print(f"evacuation_sec:   {summary['evacuation_duration_seconds']}")
    print(f"timeout_no_eta:   {summary['timeout_no_eta']}")
    print(f"inject_failures:  {summary['inject_failures']}")
    print(f"tracking_errors:  {summary['tracking_error_ods']}")
    print(f"dispatch_passed:  {dispatch_validation['passed']}")
    if "fatal_error" in summary:
        print(f"fatal_error:      {summary['fatal_error']}")

    print("===HUB_SUMMARY_JSON===")
    print(json.dumps(hub_summary, ensure_ascii=False, indent=2))
    print("===DISPATCH_VALIDATION_JSON===")
    print(json.dumps(dispatch_validation, ensure_ascii=False, indent=2))
    print("===RESULT_ROWS_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    write_report_json(
        report_json=report_json,
        summary=summary,
        hub_summary=hub_summary,
        dispatch_validation=dispatch_validation,
        rows=rows,
        od_generation=od_generation,
    )


async def amain(args: argparse.Namespace) -> int:
    """真实运行入口：连接模拟器、warmup、注入车辆、跟踪并输出报告。"""

    first_departure_time: Optional[int] = None
    dispatch_span_seconds = 0
    effective_od_csv = args.od_csv
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
        dispatch_span_seconds = int(od_generation.get("max_trip_index", 0)) * args.headway_seconds
    elif args.od_json:
        hub_roads = [str(road_id) for road_id in (args.hub_roads or FIXED_HUB_ROADS)]
        dispatch_tasks, od_generation = build_dispatch_tasks_from_od_json(
            od_json=args.od_json,
            fixed_hub_roads=hub_roads,
            batch_size_per_hub=args.batch_size_per_hub,
        )
        dispatch_span_seconds = int(od_generation.get("max_trip_index", 0)) * args.headway_seconds
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

    if args.max_probe_steps is None:
        if args.od_json or effective_od_csv:
            # 正式疏散默认至少覆盖全部发车计划，再额外留 2 小时追踪最后一批车辆。
            effective_max_probe_steps = max(
                1,
                (dispatch_span_seconds + DEFAULT_EVACUATION_MAX_PROBE_SECONDS + args.interval - 1)
                // args.interval,
            )
        else:
            effective_max_probe_steps = 60
    else:
        effective_max_probe_steps = args.max_probe_steps

    tracking_horizon_seconds = effective_max_probe_steps * args.interval
    if (args.od_json or effective_od_csv) and tracking_horizon_seconds < dispatch_span_seconds:
        od_generation["tracking_horizon_warning"] = (
            "max_probe_steps * interval is shorter than the dispatch span; "
            "some later trips may never be observed."
        )

    od_generation["tracking_config"] = {
        "interval": args.interval,
        "max_probe_steps": effective_max_probe_steps,
        "tracking_horizon_seconds": tracking_horizon_seconds,
        "query_concurrency": args.query_concurrency,
        "track_every_steps": args.track_every_steps,
        "dispatch_gap_tolerance_seconds": args.dispatch_gap_tolerance_seconds,
    }
    od_generation["dispatch_config"] = {
        "batch_size_per_hub": args.batch_size_per_hub,
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
    exit_code = 0

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
        if args.od_json or effective_od_csv:
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

        states = await inject_all_probes(
            env=env,
            person_template=person_template,
            dispatch_tasks=dispatch_tasks,
            departure_delay_steps=args.departure_delay_steps,
            departure_stagger_steps=args.departure_stagger_steps,
        )

        await track_all_probes(
            env=env,
            states=states,
            max_probe_steps=effective_max_probe_steps,
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
            first_departure_time=first_departure_time,
            headway_seconds=args.headway_seconds,
            dispatch_gap_tolerance_seconds=args.dispatch_gap_tolerance_seconds,
        )
        try:
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
