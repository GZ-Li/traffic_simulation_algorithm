from __future__ import annotations

from typing import Dict

import numpy as np

from .controller import RLController


class RLStateBuilder:
    def __init__(
        self,
        controller: RLController,
        queue_cap: float,
        vehicle_cap: float,
        use_vehicle_cnt: bool = False,
    ):
        self.controller = controller
        self.queue_cap = queue_cap
        self.vehicle_cap = vehicle_cap
        self.use_vehicle_cnt = use_vehicle_cnt

    def build_state(
        self,
        junction_id: int,
        lane_snapshot: Dict[int, Dict[str, float]],
    ) -> np.ndarray:
        spec = self.controller.junction_specs[junction_id]
        features = []
        for lane_id in spec.lane_order:
            lane_state = lane_snapshot.get(
                lane_id, {"queue_cnt": 0.0, "vehicle_cnt": 0.0}
            )
            queue_cnt = float(lane_state.get("queue_cnt", 0.0))
            features.append(min(queue_cnt / self.queue_cap, 1.0))

        if self.use_vehicle_cnt:
            for lane_id in spec.lane_order:
                lane_state = lane_snapshot.get(
                    lane_id, {"queue_cnt": 0.0, "vehicle_cnt": 0.0}
                )
                vehicle_cnt = float(lane_state.get("vehicle_cnt", 0.0))
                features.append(min(vehicle_cnt / self.vehicle_cap, 1.0))

        phase_one_hot = np.zeros(spec.action_dim, dtype=np.float32)
        phase_one_hot[self.controller.current_green_slots[junction_id]] = 1.0
        return np.concatenate(
            [np.asarray(features, dtype=np.float32), phase_one_hot], axis=0
        )

    def build_all(
        self,
        lane_snapshot: Dict[int, Dict[str, float]],
    ) -> Dict[int, np.ndarray]:
        return {
            junction_id: self.build_state(junction_id, lane_snapshot)
            for junction_id in self.controller.junction_ids
        }

    def state_dim(self, junction_id: int) -> int:
        spec = self.controller.junction_specs[junction_id]
        lane_feature_dim = len(spec.lane_order) * (2 if self.use_vehicle_cnt else 1)
        return lane_feature_dim + spec.action_dim
