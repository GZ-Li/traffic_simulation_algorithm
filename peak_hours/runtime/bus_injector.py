"""
公交注入运行时封装。

支持四种 AddPerson 注入模式：
1. template: 直接复制 Mongo 中的一条 person 模板
2. direct_od: 只指定起终点 lane/road，写入 routes=[]，交给仿真自动规划
2. route_between: 给定起终点 lane/road，经 routing 服务生成 driving route
3. road_path: 直接写入指定的 road_ids 作为 driving route

实现取舍：
- 当前仍使用 dict 构造 person/trip/route，并依赖 ParseDict 做最终解析
- 这样可以最大限度复用 Mongo 中已有的模板结构，减少与既有数据格式的偏差
- 如果后续在字段解析上遇到兼容性问题，再迁移到 pycityproto 原生 protobuf 对象构造
"""
import asyncio
import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import pymongo

from trafficlight_rl.engine import SimulatorConfig

from .sim_runner import SimulationRunner

DRIVING_LANE_TYPE = 1
SUPPORTED_BUS_INJECT_MODES = {"template", "direct_od", "route_between", "road_path"}
DEFAULT_DRIVE_TRIP_MODE = 2
DEFAULT_DRIVING_ROUTE_TYPE = 1
DEFAULT_LOOP_COUNT = 1

if TYPE_CHECKING:
    from pycitysim.routing import RoutingClient


@dataclass(frozen=True)
class BusInjectionConfig:
    """公交注入配置。"""

    mode: str = "template"
    routing_server_address: str = ""
    start_lane_id: Optional[int] = None
    end_lane_id: Optional[int] = None
    start_road_id: Optional[int] = None
    end_road_id: Optional[int] = None
    path_road_ids: Tuple[int, ...] = ()
    departure_time: Optional[float] = None
    departure_headway_seconds: float = 0.0
    spawn_s: float = 1.0
    end_s: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "routing_server_address": self.routing_server_address,
            "start_lane_id": self.start_lane_id,
            "end_lane_id": self.end_lane_id,
            "start_road_id": self.start_road_id,
            "end_road_id": self.end_road_id,
            "path_road_ids": list(self.path_road_ids),
            "departure_time": self.departure_time,
            "departure_headway_seconds": self.departure_headway_seconds,
            "spawn_s": self.spawn_s,
            "end_s": self.end_s,
        }

    def is_default(self) -> bool:
        return (
            self.mode == "template"
            and not self.routing_server_address
            and self.start_lane_id is None
            and self.end_lane_id is None
            and self.start_road_id is None
            and self.end_road_id is None
            and not self.path_road_ids
            and self.departure_time is None
            and self.departure_headway_seconds == 0.0
            and self.spawn_s == 1.0
            and self.end_s == 1.0
        )


class _MapLookup:
    """只做轻量 map 索引，避免在注入环节重复扫描道路和车道。"""

    def __init__(self, map_data: Any):
        self.roads_by_id = {road.id: road for road in map_data.roads}
        self.lanes_by_id = {lane.id: lane for lane in map_data.lanes}

    def get_lane(self, lane_id: int) -> Any:
        lane = self.lanes_by_id.get(lane_id)
        if lane is None:
            raise ValueError(f"lane_id={lane_id} 不存在于地图中")
        return lane

    def get_lane_length(self, lane_id: int) -> float:
        lane = self.get_lane(lane_id)
        return float(getattr(lane, "length", 0.0))

    def choose_driving_lane(self, road_id: int) -> int:
        road = self.roads_by_id.get(road_id)
        if road is None:
            raise ValueError(f"road_id={road_id} 不存在于地图中")

        candidate_lanes: List[Any] = []
        for lane_id in getattr(road, "lane_ids", []):
            lane = self.lanes_by_id.get(lane_id)
            if lane is None:
                continue
            if getattr(lane, "type", None) == DRIVING_LANE_TYPE:
                candidate_lanes.append(lane)

        if not candidate_lanes:
            raise ValueError(f"road_id={road_id} 没有可用的 driving lane")

        candidate_lanes.sort(
            key=lambda lane: (float(getattr(lane, "length", 0.0)), -int(lane.id)),
            reverse=True,
        )
        return int(candidate_lanes[0].id)


def _parse_id_csv(raw_value: Optional[str]) -> Tuple[int, ...]:
    if raw_value is None or raw_value == "":
        return ()
    return tuple(int(item.strip()) for item in raw_value.split(",") if item.strip())


def validate_bus_injection_args(args: Any) -> None:
    """校验公交注入参数。仅在 add_bus_count > 0 时启用严格校验。"""

    mode = getattr(args, "bus_inject_mode", "template")
    if mode not in SUPPORTED_BUS_INJECT_MODES:
        raise ValueError(
            f"--bus_inject_mode 仅支持 {sorted(SUPPORTED_BUS_INJECT_MODES)}，当前为 {mode}"
        )

    if getattr(args, "bus_departure_headway_seconds", 0.0) < 0:
        raise ValueError("--bus_departure_headway_seconds 不能为负数")
    if getattr(args, "bus_spawn_s", 1.0) < 0:
        raise ValueError("--bus_spawn_s 不能为负数")
    if getattr(args, "bus_end_s", 1.0) < 0:
        raise ValueError("--bus_end_s 不能为负数")

    if getattr(args, "add_bus_count", 0) <= 0:
        return

    _validate_endpoint_args("start", args.bus_start_lane_id, args.bus_start_road_id)
    _validate_endpoint_args("end", args.bus_end_lane_id, args.bus_end_road_id)

    if mode in {"direct_od", "route_between"}:
        if not _has_endpoint(args.bus_start_lane_id, args.bus_start_road_id):
            raise ValueError(f"bus_inject_mode={mode} 时必须指定起点 lane 或 road")
        if not _has_endpoint(args.bus_end_lane_id, args.bus_end_road_id):
            raise ValueError(f"bus_inject_mode={mode} 时必须指定终点 lane 或 road")

    if mode == "route_between":
        if not getattr(args, "routing_server_address", "").strip():
            raise ValueError("bus_inject_mode=route_between 时必须指定 --routing_server_address")

    if mode == "road_path":
        path_road_ids = _parse_id_csv(getattr(args, "bus_path_road_ids", None))
        if not path_road_ids:
            raise ValueError("bus_inject_mode=road_path 时必须指定 --bus_path_road_ids")


def build_bus_injection_config_from_args(args: Any) -> BusInjectionConfig:
    """从 argparse Namespace 构造共享注入配置。"""

    return BusInjectionConfig(
        mode=getattr(args, "bus_inject_mode", "template"),
        routing_server_address=getattr(args, "routing_server_address", ""),
        start_lane_id=getattr(args, "bus_start_lane_id", None),
        end_lane_id=getattr(args, "bus_end_lane_id", None),
        start_road_id=getattr(args, "bus_start_road_id", None),
        end_road_id=getattr(args, "bus_end_road_id", None),
        path_road_ids=_parse_id_csv(getattr(args, "bus_path_road_ids", None)),
        departure_time=getattr(args, "bus_departure_time", None),
        departure_headway_seconds=getattr(args, "bus_departure_headway_seconds", 0.0),
        spawn_s=getattr(args, "bus_spawn_s", 1.0),
        end_s=getattr(args, "bus_end_s", 1.0),
    )


def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    """清洗模板，保留既有 person 结构，只移除 AddPerson 不接受的字段。"""

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

    for key in ("_id", "id", "base", "class", "data"):
        normalized.pop(key, None)
    return normalized


def load_person_template_from_mongo(
    mongo_uri: str, agent_db: str, agent_coll: str
) -> Optional[Dict[str, Any]]:
    """从 Mongo 读取一条 person.data 作为 AddPerson 模板。"""

    logger = logging.getLogger(__name__)
    client = pymongo.MongoClient(mongo_uri)
    try:
        doc = client[agent_db][agent_coll].find_one({}, {"_id": 0}, sort=[("id", 1)])
        if not doc:
            logger.warning("Mongo 中未找到 person 模板")
            return None
        data = doc.get("data")
        if not isinstance(data, dict):
            logger.warning("Mongo 模板缺少 data 字段")
            return None
        return data
    finally:
        client.close()


async def add_bus_persons_with_plan(
    runner: SimulationRunner,
    config: SimulatorConfig,
    count: int,
    log_dir: str,
    injection_config: Optional[BusInjectionConfig] = None,
    post_wait_steps: int = 10,
) -> List[int]:
    """
    通过 AddPerson 新增车辆，并按注入模式写入 route/home/schedules。
    """

    logger = logging.getLogger(__name__)
    if count <= 0:
        return []
    if not runner.engine:
        raise RuntimeError("仿真未启动")

    effective_config = injection_config or BusInjectionConfig()
    template = load_person_template_from_mongo(
        config.mongo_uri, config.agent_db, config.agent_coll
    )
    if not template:
        logger.warning("无法从 Mongo 读取 person 模板，AddPerson 跳过")
        return []

    normalized_template = normalize_person_for_add(copy.deepcopy(template))
    map_lookup = _MapLookup(runner.get_map_data())
    routing_client: Optional["RoutingClient"] = None
    if effective_config.mode == "route_between":
        from pycitysim.routing import RoutingClient

        routing_client = RoutingClient(effective_config.routing_server_address)

    os.makedirs(log_dir, exist_ok=True)
    _write_json(
        os.path.join(log_dir, "bus_injection_config.json"),
        {
            "add_bus_count": count,
            "post_wait_steps": post_wait_steps,
            "config": effective_config.to_dict(),
        },
    )

    added_ids: List[int] = []
    added_logs: List[Dict[str, Any]] = []
    planned_routes: List[Dict[str, Any]] = []

    for index in range(count):
        person, plan_record = await _build_person_for_index(
            template=normalized_template,
            runner=runner,
            map_lookup=map_lookup,
            injection_config=effective_config,
            routing_client=routing_client,
            index=index,
        )
        resp = await runner.engine.client.person_service.AddPerson({"person": copy.deepcopy(person)})
        person_id = resp.get("person_id") if isinstance(resp, dict) else None
        if isinstance(person_id, int):
            added_ids.append(person_id)
        plan_record["person_id"] = person_id

        added_logs.append(
            {
                "index": index,
                "person_id": person_id,
                "departure_time": plan_record["departure_time"],
                "start_lane_position": plan_record["start_lane_position"],
                "end_lane_position": plan_record["end_lane_position"],
                "resolved_route_road_ids": plan_record["resolved_route_road_ids"],
            }
        )
        planned_routes.append(plan_record)

    logger.info("AddPerson 返回 person_id 数量: %s", len(added_ids))

    if post_wait_steps > 0:
        for _ in range(post_wait_steps):
            runner.step(1)
            await asyncio.sleep(0)

    if effective_config.mode == "direct_od" and added_ids:
        resolved_road_ids_by_person = await _backfill_direct_od_routes(
            runner=runner,
            person_ids=added_ids,
        )
        for item in added_logs:
            person_id = item.get("person_id")
            if isinstance(person_id, int):
                item["resolved_route_road_ids"] = resolved_road_ids_by_person.get(person_id, [])
        for item in planned_routes:
            person_id = item.get("person_id")
            if isinstance(person_id, int):
                item["resolved_route_road_ids"] = resolved_road_ids_by_person.get(person_id, [])

    _write_json(os.path.join(log_dir, "added_bus_persons.json"), added_logs)
    _write_json(os.path.join(log_dir, "planned_routes.json"), planned_routes)

    return added_ids


async def _build_person_for_index(
    template: Dict[str, Any],
    runner: SimulationRunner,
    map_lookup: _MapLookup,
    injection_config: BusInjectionConfig,
    routing_client: Optional["RoutingClient"],
    index: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    person = copy.deepcopy(template)
    departure_time = _resolve_departure_time(runner, injection_config, index)

    if injection_config.mode == "template":
        _override_departure_time(person, departure_time, injection_config)
        return person, _build_template_plan_record(person, departure_time, index, injection_config)

    start_lane_position = _resolve_lane_position(
        map_lookup=map_lookup,
        explicit_lane_id=injection_config.start_lane_id,
        explicit_road_id=injection_config.start_road_id,
        fallback_road_id=injection_config.path_road_ids[0] if injection_config.path_road_ids else None,
        offset_s=injection_config.spawn_s,
        endpoint_name="start",
    )
    end_lane_position = _resolve_lane_position(
        map_lookup=map_lookup,
        explicit_lane_id=injection_config.end_lane_id,
        explicit_road_id=injection_config.end_road_id,
        fallback_road_id=injection_config.path_road_ids[-1] if injection_config.path_road_ids else None,
        offset_s=injection_config.end_s,
        endpoint_name="end",
    )

    if injection_config.mode == "direct_od":
        routes: List[Dict[str, Any]] = []
    elif injection_config.mode == "route_between":
        if routing_client is None:
            raise RuntimeError("route_between 模式缺少 routing client")
        routes = await _fetch_routing_journeys(routing_client, start_lane_position, end_lane_position)
    elif injection_config.mode == "road_path":
        routes = [{"type": _extract_default_route_type(person), "driving": {"road_ids": list(injection_config.path_road_ids)}}]
    else:
        raise ValueError(f"不支持的公交注入模式: {injection_config.mode}")

    trip_mode = _extract_default_trip_mode(person)
    loop_count = _extract_default_loop_count(person)

    person["home"] = {"lane_position": start_lane_position}
    person["schedules"] = [
        {
            "departure_time": departure_time,
            "loop_count": loop_count,
            "trips": [
                {
                    "mode": trip_mode,
                    "end": {"lane_position": end_lane_position},
                    "routes": routes,
                    "trip_stops": [],
                }
            ],
        }
    ]

    plan_record = {
        "index": index,
        "mode": injection_config.mode,
        "user_intent": {
            "start_lane_id": injection_config.start_lane_id,
            "end_lane_id": injection_config.end_lane_id,
            "start_road_id": injection_config.start_road_id,
            "end_road_id": injection_config.end_road_id,
            "path_road_ids": list(injection_config.path_road_ids),
            "spawn_s": injection_config.spawn_s,
            "end_s": injection_config.end_s,
            "auto_route_from_od": injection_config.mode == "direct_od",
        },
        "departure_time": departure_time,
        "start_lane_position": start_lane_position,
        "end_lane_position": end_lane_position,
        "resolved_route_road_ids": _extract_road_ids_from_routes(routes),
    }
    return person, plan_record


def _validate_endpoint_args(name: str, lane_id: Optional[int], road_id: Optional[int]) -> None:
    if lane_id is not None and road_id is not None:
        raise ValueError(f"{name} 端点的 lane_id 和 road_id 不能同时指定")


def _has_endpoint(lane_id: Optional[int], road_id: Optional[int]) -> bool:
    return lane_id is not None or road_id is not None


def _resolve_departure_time(
    runner: SimulationRunner, injection_config: BusInjectionConfig, index: int
) -> float:
    if not runner.engine:
        raise RuntimeError("仿真未启动")

    base_departure_time = (
        injection_config.departure_time
        if injection_config.departure_time is not None
        else runner.engine.get_simulation_time()
    )
    return float(base_departure_time + index * injection_config.departure_headway_seconds)


def _override_departure_time(
    person: Dict[str, Any], departure_time: float, injection_config: BusInjectionConfig
) -> None:
    if injection_config.departure_time is None and injection_config.departure_headway_seconds == 0:
        return
    schedules = person.get("schedules")
    if isinstance(schedules, list) and schedules:
        schedules[0]["departure_time"] = departure_time


def _build_template_plan_record(
    person: Dict[str, Any],
    departure_time: float,
    index: int,
    injection_config: BusInjectionConfig,
) -> Dict[str, Any]:
    schedules = person.get("schedules") if isinstance(person.get("schedules"), list) else []
    first_trip = schedules[0].get("trips", [{}])[0] if schedules else {}
    return {
        "index": index,
        "mode": injection_config.mode,
        "user_intent": {"mode": "template"},
        "departure_time": departure_time,
        "start_lane_position": person.get("home", {}).get("lane_position"),
        "end_lane_position": first_trip.get("end", {}).get("lane_position"),
        "resolved_route_road_ids": _extract_road_ids_from_routes(first_trip.get("routes", [])),
    }


def _resolve_lane_position(
    map_lookup: _MapLookup,
    explicit_lane_id: Optional[int],
    explicit_road_id: Optional[int],
    fallback_road_id: Optional[int],
    offset_s: float,
    endpoint_name: str,
) -> Dict[str, float]:
    lane_id: Optional[int] = explicit_lane_id
    if lane_id is None:
        road_id = explicit_road_id if explicit_road_id is not None else fallback_road_id
        if road_id is None:
            raise ValueError(f"{endpoint_name} 端点缺少 lane_id/road_id，无法解析注入位置")
        lane_id = map_lookup.choose_driving_lane(road_id)

    lane_length = map_lookup.get_lane_length(lane_id)
    return {"lane_id": int(lane_id), "s": _clamp_lane_s(offset_s, lane_length)}


def _clamp_lane_s(offset_s: float, lane_length: float) -> float:
    if lane_length <= 0:
        return 0.0
    return max(0.0, min(float(offset_s), max(lane_length - 0.1, 0.0)))


async def _fetch_routing_journeys(
    routing_client: "RoutingClient",
    start_lane_position: Dict[str, float],
    end_lane_position: Dict[str, float],
) -> List[Dict[str, Any]]:
    response = await routing_client.GetRoute(
        {
            "type": 1,
            "start": {"lane_position": start_lane_position},
            "end": {"lane_position": end_lane_position},
        }
    )
    journeys = response.get("journeys") if isinstance(response, dict) else None
    if not isinstance(journeys, list) or not journeys:
        raise ValueError("RoutingClient.GetRoute 未返回 journeys")

    driving_journeys = [
        journey for journey in journeys if isinstance(journey, dict) and "driving" in journey
    ]
    return driving_journeys or journeys


def _extract_default_trip_mode(person: Dict[str, Any]) -> int:
    try:
        return int(person["schedules"][0]["trips"][0]["mode"])
    except (KeyError, IndexError, TypeError, ValueError):
        # 历史 person 模板中 driving-only trip 通常使用 mode=2。
        return DEFAULT_DRIVE_TRIP_MODE


def _extract_default_route_type(person: Dict[str, Any]) -> int:
    try:
        return int(person["schedules"][0]["trips"][0]["routes"][0]["type"])
    except (KeyError, IndexError, TypeError, ValueError):
        # driving route 在历史模板和样例中通常使用 type=1。
        return DEFAULT_DRIVING_ROUTE_TYPE


def _extract_default_loop_count(person: Dict[str, Any]) -> int:
    try:
        return int(person["schedules"][0]["loop_count"])
    except (KeyError, IndexError, TypeError, ValueError):
        return DEFAULT_LOOP_COUNT


def _extract_road_ids_from_routes(routes: Sequence[Any]) -> List[int]:
    road_ids: List[int] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        driving = route.get("driving", {})
        if isinstance(driving, dict):
            route_road_ids = driving.get("road_ids", [])
            if isinstance(route_road_ids, list):
                road_ids.extend(int(road_id) for road_id in route_road_ids)
    return road_ids


async def _backfill_direct_od_routes(
    runner: SimulationRunner,
    person_ids: Sequence[int],
) -> Dict[int, List[int]]:
    logger = logging.getLogger(__name__)
    if not runner.engine or not person_ids:
        return {}

    try:
        response = await runner.engine.client.person_service.GetPersons(
            {"person_ids": list(person_ids)}
        )
    except Exception as exc:
        logger.warning("direct_od 路径回填失败，无法查询 person runtime: %s", exc)
        return {}

    result: Dict[int, List[int]] = {}
    unresolved_ids = set(int(person_id) for person_id in person_ids)
    for person in response.get("persons", []):
        person_id = _extract_person_id_from_person_state(person)
        if person_id is None:
            continue
        resolved_road_ids = _extract_routes_from_person_state(person)
        result[person_id] = resolved_road_ids
        unresolved_ids.discard(person_id)

    if unresolved_ids:
        logger.warning(
            "direct_od 路径回填时有 %s 个 person 未取到 runtime person 详情: %s",
            len(unresolved_ids),
            sorted(unresolved_ids),
        )

    unresolved_routes = [person_id for person_id, road_ids in result.items() if not road_ids]
    if unresolved_routes:
        logger.warning(
            "direct_od 路径回填完成，但有 %s 个 person 的 routes 仍为空，可能尚未完成自动规划: %s",
            len(unresolved_routes),
            unresolved_routes[:10],
        )
    return result


def _extract_person_id_from_person_state(person: Dict[str, Any]) -> Optional[int]:
    motion = person.get("motion", {})
    if isinstance(motion, dict) and isinstance(motion.get("id"), int):
        return int(motion["id"])

    base = person.get("base", {})
    if isinstance(base, dict) and isinstance(base.get("id"), int):
        return int(base["id"])

    if isinstance(person.get("id"), int):
        return int(person["id"])
    return None


def _extract_routes_from_person_state(person: Dict[str, Any]) -> List[int]:
    schedules = person.get("schedules", [])
    if not isinstance(schedules, list) or not schedules:
        return []

    first_schedule = schedules[0]
    if not isinstance(first_schedule, dict):
        return []

    trips = first_schedule.get("trips", [])
    if not isinstance(trips, list) or not trips:
        return []

    first_trip = trips[0]
    if not isinstance(first_trip, dict):
        return []

    routes = first_trip.get("routes", [])
    if not isinstance(routes, list):
        return []
    return _extract_road_ids_from_routes(routes)


def _write_json(path: str, payload: Any) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
