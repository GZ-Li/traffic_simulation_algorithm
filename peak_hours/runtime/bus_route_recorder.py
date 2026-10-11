"""
公交运行时路径记录器。

思路很直接：
- 运行仿真时定期调用 GetPersons 获取公交当前所在 lane_id
- 通过地图里的 road.lane_ids 建立 lane -> road 映射
- 每辆公交只在 road_id 发生变化时追加一次
- 仿真结束后把每辆公交经过的 road_id 序列落盘
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from .sim_runner import SimulationRunner


def _try_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _load_json_if_exists(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _build_lane_to_road_id(map_data: Any) -> Dict[int, int]:
    lane_to_road_id: Dict[int, int] = {}
    for road in getattr(map_data, "roads", []):
        road_id = _try_int(getattr(road, "id", None))
        if road_id is None:
            continue
        for lane_id in getattr(road, "lane_ids", []):
            maybe_lane_id = _try_int(lane_id)
            if maybe_lane_id is not None:
                lane_to_road_id[maybe_lane_id] = road_id
    return lane_to_road_id


def _load_route_metadata(log_dir: str) -> Dict[int, Dict[str, Any]]:
    added_bus_path = os.path.join(log_dir, "added_bus_persons.json")
    planned_routes_path = os.path.join(log_dir, "planned_routes.json")
    added_payload = _load_json_if_exists(added_bus_path)
    planned_payload = _load_json_if_exists(planned_routes_path)

    added_by_index: Dict[int, Dict[str, Any]] = {}
    if isinstance(added_payload, list):
        for item in added_payload:
            if not isinstance(item, dict):
                continue
            index = _try_int(item.get("index"))
            if index is None:
                continue
            added_by_index[index] = item

    metadata_by_person_id: Dict[int, Dict[str, Any]] = {}
    if isinstance(planned_payload, list):
        for idx, item in enumerate(planned_payload):
            if not isinstance(item, dict):
                continue
            route_index = _try_int(item.get("index"))
            if route_index is None:
                route_index = idx
            added_item = added_by_index.get(route_index, {})
            person_id = _try_int(item.get("person_id"))
            if person_id is None:
                person_id = _try_int(added_item.get("person_id"))
            if person_id is None:
                continue
            metadata_by_person_id[person_id] = {
                "index": route_index,
                "departure_time": item.get(
                    "departure_time",
                    added_item.get("departure_time"),
                ),
                "start_lane_position": item.get(
                    "start_lane_position",
                    added_item.get("start_lane_position"),
                ),
                "end_lane_position": item.get(
                    "end_lane_position",
                    added_item.get("end_lane_position"),
                ),
            }

    return metadata_by_person_id


class BusRouteRecorder:
    """运行时记录公交经过的 road_id 序列。"""

    def __init__(
        self,
        sim_runner: SimulationRunner,
        bus_ids: Sequence[int],
        log_dir: str,
    ):
        self.sim_runner = sim_runner
        self.bus_ids = [int(bus_id) for bus_id in bus_ids]
        self.log_dir = log_dir
        self.logger = logging.getLogger(__name__)
        self.lane_to_road_id = _build_lane_to_road_id(sim_runner.get_map_data())
        self.route_metadata_by_person_id = _load_route_metadata(log_dir)
        self.routes_by_person_id: Dict[int, List[int]] = {
            int(bus_id): [] for bus_id in self.bus_ids
        }
        self.sample_count = 0

    async def sample(self) -> None:
        if not self.bus_ids or not self.sim_runner.engine:
            return

        try:
            res = await self.sim_runner.engine.client.person_service.GetPersons(
                {"person_ids": self.bus_ids}
            )
        except Exception as exc:
            self.logger.warning("采样公交运行路径失败: %s", exc)
            return

        self.sample_count += 1
        for person in res.get("persons", []):
            motion = person.get("motion", {})
            if not isinstance(motion, dict):
                continue
            person_id = _try_int(motion.get("id"))
            if person_id is None:
                continue
            position = motion.get("position", {})
            if not isinstance(position, dict):
                continue
            lane_position = position.get("lane_position", {})
            if not isinstance(lane_position, dict):
                continue
            lane_id = _try_int(lane_position.get("lane_id"))
            if lane_id is None:
                continue
            road_id = self.lane_to_road_id.get(lane_id)
            if road_id is None:
                continue

            route = self.routes_by_person_id.setdefault(person_id, [])
            if not route or route[-1] != road_id:
                route.append(road_id)

    def dump_json(self, path: Optional[str] = None) -> str:
        output_path = path or os.path.join(self.log_dir, "bus_runtime_routes.json")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        payload: List[Dict[str, Any]] = []
        for default_index, person_id in enumerate(self.bus_ids):
            metadata = self.route_metadata_by_person_id.get(person_id, {})
            payload.append(
                {
                    "index": metadata.get("index", default_index),
                    "person_id": person_id,
                    "departure_time": metadata.get("departure_time"),
                    "start_lane_position": metadata.get("start_lane_position"),
                    "end_lane_position": metadata.get("end_lane_position"),
                    "resolved_route_road_ids": self.routes_by_person_id.get(person_id, []),
                    "source": "runtime_sampled",
                    "sample_count": self.sample_count,
                }
            )

        with open(output_path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return output_path
