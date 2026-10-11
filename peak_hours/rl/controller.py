from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Dict, List


@dataclass
class JunctionSpec:
    junction_id: int
    green_phase_indices: List[int]
    phase_in_lane_ids: List[List[int]]
    lane_order: List[int]

    @property
    def action_dim(self) -> int:
        return len(self.green_phase_indices)


class RLController:
    def __init__(
        self,
        sim_runner,
        junction_ids: List[int],
        min_green_seconds: float,
    ):
        self.sim_runner = sim_runner
        self.requested_junction_ids = list(junction_ids)
        self.min_green_seconds = min_green_seconds
        self.logger = logging.getLogger(__name__)

        self.junction_specs: Dict[int, JunctionSpec] = {}
        self.junction_ids: List[int] = []
        self.current_green_slots: Dict[int, int] = {}
        self.current_green_raw_phases: Dict[int, int] = {}
        self.time_in_green: Dict[int, float] = {}

        self._init_specs()
        self._reset_runtime_state()

    def _init_specs(self) -> None:
        map_data = self.sim_runner.get_map_data()
        phase_lanes = self.sim_runner.get_junction_phase_lanes(self.requested_junction_ids)
        lane_index_to_id = {
            idx: lane.id for idx, lane in enumerate(map_data.lanes)
        }

        junctions_by_id = {junction.id: junction for junction in map_data.junctions}
        missing = [
            junction_id
            for junction_id in self.requested_junction_ids
            if junction_id not in junctions_by_id
        ]
        if missing:
            raise ValueError(f"地图中未找到这些路口: {missing}")

        selected_junctions = [
            junction
            for junction in map_data.junctions
            if junction.id in self.requested_junction_ids
        ]
        specs_by_id: Dict[int, JunctionSpec] = {}
        for junction, raw_phase_lanes in zip(selected_junctions, phase_lanes):
            green_phase_indices: List[int] = []
            phase_in_lane_ids: List[List[int]] = []
            lane_union = set()

            for raw_idx, (in_lane_indices, _out_lane_indices) in enumerate(raw_phase_lanes):
                lane_ids = [
                    lane_index_to_id[idx]
                    for idx in in_lane_indices
                    if idx in lane_index_to_id
                ]
                lane_ids = sorted(set(lane_ids))
                if not lane_ids:
                    continue

                green_phase_indices.append(raw_idx)
                phase_in_lane_ids.append(lane_ids)
                lane_union.update(lane_ids)

            if len(green_phase_indices) < 2:
                raise ValueError(
                    f"路口 {junction.id} 可控 green phase 少于 2 个，不能用于 DQN P0"
                )

            specs_by_id[junction.id] = JunctionSpec(
                junction_id=junction.id,
                green_phase_indices=green_phase_indices,
                phase_in_lane_ids=phase_in_lane_ids,
                lane_order=sorted(lane_union),
            )

        for junction_id in self.requested_junction_ids:
            if junction_id not in specs_by_id:
                raise ValueError(f"无法为路口 {junction_id} 构建可控相位信息")
            self.junction_specs[junction_id] = specs_by_id[junction_id]
            self.junction_ids.append(junction_id)

    def _reset_runtime_state(self) -> None:
        for junction_id in self.junction_ids:
            spec = self.junction_specs[junction_id]
            self.current_green_slots[junction_id] = 0
            self.current_green_raw_phases[junction_id] = spec.green_phase_indices[0]
            self.time_in_green[junction_id] = 0.0

    async def initialize_known_phase(self) -> None:
        self._reset_runtime_state()
        for junction_id in self.junction_ids:
            raw_phase_index = self.current_green_raw_phases[junction_id]
            await self.sim_runner.set_traffic_light_phase(
                junction_id=junction_id,
                phase_index=raw_phase_index,
                time_remaining=1e99,
            )

    async def apply_actions(self, actions: Dict[int, int]) -> None:
        pending_tasks = []
        pending_updates = []
        for junction_id in self.junction_ids:
            if junction_id not in actions:
                raise ValueError(f"缺少路口 {junction_id} 的动作")
            action = int(actions[junction_id])
            spec = self.junction_specs[junction_id]
            if action < 0 or action >= spec.action_dim:
                raise ValueError(f"路口 {junction_id} 的动作越界: {action}")

            current_slot = self.current_green_slots[junction_id]
            if action == current_slot:
                continue
            if self.time_in_green[junction_id] < self.min_green_seconds:
                continue

            raw_phase_index = spec.green_phase_indices[action]
            pending_tasks.append(
                self.sim_runner.set_traffic_light_phase(
                    junction_id=junction_id,
                    phase_index=raw_phase_index,
                    time_remaining=1e99,
                )
            )
            pending_updates.append((junction_id, action, raw_phase_index))

        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        for junction_id, action, raw_phase_index in pending_updates:
            self.current_green_slots[junction_id] = action
            self.current_green_raw_phases[junction_id] = raw_phase_index
            self.time_in_green[junction_id] = 0.0

    def advance_time(self, dt: float) -> None:
        for junction_id in self.junction_ids:
            self.time_in_green[junction_id] += dt

    def metadata(self) -> Dict[str, Any]:
        return {
            "junction_ids": self.junction_ids,
            "junctions": {
                str(junction_id): {
                    "green_phase_indices": spec.green_phase_indices,
                    "lane_order": spec.lane_order,
                    "phase_in_lane_ids": spec.phase_in_lane_ids,
                }
                for junction_id, spec in self.junction_specs.items()
            },
        }
