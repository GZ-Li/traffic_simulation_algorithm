from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def parse_junction_ids(raw: str) -> List[int]:
    ids: List[int] = []
    seen = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        junction_id = int(chunk)
        if junction_id in seen:
            continue
        seen.add(junction_id)
        ids.append(junction_id)
    return ids


@dataclass
class DQNHyperParams:
    gamma: float = 0.98
    epsilon_start: float = 0.9
    epsilon_decay: float = 0.999
    epsilon_min: float = 0.05
    learning_rate: float = 1e-3
    batch_size: int = 64
    buffer_size: int = 20_000
    grad_clip: float = 5.0
    learning_start_transitions: int = 200
    target_update_every: int = 10
    hidden_dims: List[int] = field(default_factory=lambda: [20, 20])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RLExperimentConfig:
    scenario: str
    rl_junction_ids: List[int]
    total_steps: int
    interval: int
    decision_interval_seconds: int
    min_green_seconds: float
    reward_scale: float = 1.0
    episodes: int = 10
    metrics_sample_interval: int = 100
    checkpoint_path: str = ""
    queue_cap: float = 20.0
    vehicle_cap: float = 30.0
    use_vehicle_cnt: bool = False
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.rl_junction_ids:
            raise ValueError("rl_junction_ids 不能为空")
        if self.interval <= 0:
            raise ValueError("interval 必须为正整数")
        if self.decision_interval_seconds <= 0:
            raise ValueError("decision_interval_seconds 必须为正整数")
        if self.decision_interval_seconds % self.interval != 0:
            raise ValueError(
                "decision_interval_seconds 必须是 interval 的整数倍"
            )
        if self.total_steps <= 0:
            raise ValueError("total_steps 必须为正整数")
        if self.total_steps % self.decision_steps != 0:
            raise ValueError("P0 要求 total_steps 能被 decision_steps 整除")
        if self.episodes <= 0:
            raise ValueError("episodes 必须为正整数")
        if self.reward_scale <= 0:
            raise ValueError("reward_scale 必须大于 0")
        if self.metrics_sample_interval < 0:
            raise ValueError("metrics_sample_interval 不能为负数")

    @property
    def decision_steps(self) -> int:
        return self.decision_interval_seconds // self.interval

    @property
    def num_decisions(self) -> int:
        return self.total_steps // self.decision_steps

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["decision_steps"] = self.decision_steps
        data["num_decisions"] = self.num_decisions
        return data
