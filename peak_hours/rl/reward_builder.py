from __future__ import annotations

from typing import Dict

import numpy as np

from .controller import RLController


class RLRewardBuilder:
    def __init__(self, controller: RLController, reward_scale: float):
        self.controller = controller
        self.reward_scale = reward_scale

    def compute_reward(
        self,
        junction_id: int,
        lane_snapshot: Dict[int, Dict[str, float]],
    ) -> float:
        spec = self.controller.junction_specs[junction_id]
        queues = [
            float(
                lane_snapshot.get(
                    lane_id, {"queue_cnt": 0.0, "vehicle_cnt": 0.0}
                ).get("queue_cnt", 0.0)
            )
            for lane_id in spec.lane_order
        ]
        if not queues:
            return 0.0
        return float(-np.mean(queues) * self.reward_scale)

    def compute_all(
        self,
        lane_snapshot: Dict[int, Dict[str, float]],
    ) -> Dict[int, float]:
        return {
            junction_id: self.compute_reward(junction_id, lane_snapshot)
            for junction_id in self.controller.junction_ids
        }
