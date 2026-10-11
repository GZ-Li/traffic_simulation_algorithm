from __future__ import annotations

import random
from typing import Any, Dict, Optional

import numpy as np
import torch
from torch import nn
from torch.nn.utils import clip_grad_norm_

from .config import DQNHyperParams
from .replay_buffer import ReplayBuffer


class DQNNet(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden_dims: list[int]):
        super().__init__()
        dims = [input_dim] + hidden_dims + [output_dim]
        layers = []
        for idx in range(len(dims) - 1):
            layers.append(nn.Linear(dims[idx], dims[idx + 1]))
            if idx < len(dims) - 2:
                layers.append(nn.ReLU())
        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class DQNAgent:
    def __init__(
        self,
        state_dim: int,
        action_dim: int,
        hyperparams: DQNHyperParams,
        seed: Optional[int] = None,
    ):
        if state_dim <= 0:
            raise ValueError("state_dim 必须大于 0")
        if action_dim <= 1:
            raise ValueError("action_dim 必须大于 1")

        self.state_dim = state_dim
        self.action_dim = action_dim
        self.hyperparams = hyperparams
        self.epsilon = hyperparams.epsilon_start
        self.transitions_seen = 0
        self.train_steps = 0
        self._rng = random.Random(seed)

        self.model = DQNNet(
            input_dim=state_dim,
            output_dim=action_dim,
            hidden_dims=hyperparams.hidden_dims,
        )
        self.target_model = DQNNet(
            input_dim=state_dim,
            output_dim=action_dim,
            hidden_dims=hyperparams.hidden_dims,
        )
        self.target_model.load_state_dict(self.model.state_dict())
        self.target_model.eval()

        self.optimizer = torch.optim.RMSprop(
            self.model.parameters(),
            lr=hyperparams.learning_rate,
            alpha=0.9,
            centered=False,
            eps=1e-7,
        )
        self.criterion = nn.MSELoss(reduction="mean")
        self.replay_buffer = ReplayBuffer(hyperparams.buffer_size, seed=seed)

    def act(self, state: np.ndarray, training: bool = True) -> int:
        if training and self._rng.random() <= self.epsilon:
            return self._rng.randrange(self.action_dim)

        state_tensor = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            q_values = self.model(state_tensor)
        return int(torch.argmax(q_values, dim=1).item())

    def remember(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        self.replay_buffer.add(state, action, reward, next_state, done)
        self.transitions_seen += 1

    def train_step(self) -> Optional[float]:
        if len(self.replay_buffer) < self.hyperparams.batch_size:
            return None
        if self.transitions_seen < self.hyperparams.learning_start_transitions:
            return None

        samples = self.replay_buffer.sample(self.hyperparams.batch_size)
        states = torch.tensor(
            np.stack([sample[0] for sample in samples]), dtype=torch.float32
        )
        actions = torch.tensor(
            [sample[1] for sample in samples], dtype=torch.long
        )
        rewards = torch.tensor(
            [sample[2] for sample in samples], dtype=torch.float32
        )
        next_states = torch.tensor(
            np.stack([sample[3] for sample in samples]), dtype=torch.float32
        )
        dones = torch.tensor(
            [sample[4] for sample in samples], dtype=torch.float32
        )

        q_values = self.model(states).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            next_q_values = self.target_model(next_states).max(dim=1).values
            targets = rewards + (1.0 - dones) * self.hyperparams.gamma * next_q_values

        loss = self.criterion(q_values, targets)
        self.optimizer.zero_grad()
        loss.backward()
        clip_grad_norm_(self.model.parameters(), self.hyperparams.grad_clip)
        self.optimizer.step()

        self.train_steps += 1
        if self.train_steps % self.hyperparams.target_update_every == 0:
            self.update_target_network()

        if self.epsilon > self.hyperparams.epsilon_min:
            self.epsilon = max(
                self.hyperparams.epsilon_min,
                self.epsilon * self.hyperparams.epsilon_decay,
            )
        return float(loss.item())

    def update_target_network(self) -> None:
        self.target_model.load_state_dict(self.model.state_dict())

    def checkpoint(self) -> Dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
            "model_state_dict": self.model.state_dict(),
            "target_model_state_dict": self.target_model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "epsilon": self.epsilon,
            "transitions_seen": self.transitions_seen,
            "train_steps": self.train_steps,
        }

    def load_checkpoint(self, payload: Dict[str, Any]) -> None:
        if payload["state_dim"] != self.state_dim or payload["action_dim"] != self.action_dim:
            raise ValueError("checkpoint 的 state_dim 或 action_dim 与当前 agent 不匹配")
        self.model.load_state_dict(payload["model_state_dict"])
        self.target_model.load_state_dict(payload["target_model_state_dict"])
        self.optimizer.load_state_dict(payload["optimizer_state_dict"])
        self.epsilon = float(payload.get("epsilon", self.hyperparams.epsilon_start))
        self.transitions_seen = int(payload.get("transitions_seen", 0))
        self.train_steps = int(payload.get("train_steps", 0))
