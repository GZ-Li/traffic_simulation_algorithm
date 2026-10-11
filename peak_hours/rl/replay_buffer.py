from __future__ import annotations

from collections import deque
import random
from typing import Deque, List, Optional, Tuple

import numpy as np

Transition = Tuple[np.ndarray, int, float, np.ndarray, bool]


class ReplayBuffer:
    def __init__(self, capacity: int, seed: Optional[int] = None):
        if capacity <= 0:
            raise ValueError("capacity 必须大于 0")
        self._buffer: Deque[Transition] = deque(maxlen=capacity)
        self._rng = random.Random(seed)

    def add(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self._buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size: int) -> List[Transition]:
        return self._rng.sample(list(self._buffer), batch_size)

    def __len__(self) -> int:
        return len(self._buffer)
