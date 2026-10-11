from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

from trafficlight_rl.engine import SimulatorConfig
from ..metrics.evaluator import MetricsEvaluator
from ..runtime.sim_runner import SimulationRunner
from .agent import DQNAgent
from .config import DQNHyperParams, RLExperimentConfig
from .controller import RLController
from .reward_builder import RLRewardBuilder
from .state_builder import RLStateBuilder


def _set_seed(seed: Optional[int]) -> None:
    if seed is None:
        return
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _save_json(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def _checkpoint_payload(
    agents: Dict[int, DQNAgent],
    experiment_config: RLExperimentConfig,
    hyperparams: DQNHyperParams,
    controller_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "experiment_config": experiment_config.to_dict(),
        "hyperparams": hyperparams.to_dict(),
        "controller_metadata": controller_metadata,
        "agents": {
            str(junction_id): agent.checkpoint()
            for junction_id, agent in agents.items()
        },
    }


def _load_checkpoint(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"checkpoint 不存在: {path}")
    return torch.load(path, map_location="cpu")


def _log_lane_snapshot_sample(
    logger: logging.Logger,
    lane_snapshot: Dict[int, Dict[str, float]],
) -> None:
    nonzero = [
        {
            "lane_id": lane_id,
            "queue_cnt": state.get("queue_cnt", 0),
            "vehicle_cnt": state.get("vehicle_cnt", 0),
        }
        for lane_id, state in lane_snapshot.items()
        if state.get("queue_cnt", 0) or state.get("vehicle_cnt", 0)
    ]
    logger.info(
        "lane snapshot sample: total=%d nonzero=%d sample=%s",
        len(lane_snapshot),
        len(nonzero),
        nonzero[:5],
    )
    if lane_snapshot and not nonzero:
        logger.warning("lane snapshot 全为 0，请确认后端返回字段名是否仍为 vehicle_cnt / total_queuing_vehicle_cnt")


async def _run_single_episode(
    sim_config: SimulatorConfig,
    episode_log_dir: str,
    experiment_config: RLExperimentConfig,
    hyperparams: DQNHyperParams,
    agents: Dict[int, DQNAgent],
    training: bool,
) -> Dict[str, Any]:
    logger = logging.getLogger(__name__)
    with SimulationRunner(sim_config, episode_log_dir) as runner:
        controller = RLController(
            sim_runner=runner,
            junction_ids=experiment_config.rl_junction_ids,
            min_green_seconds=experiment_config.min_green_seconds,
        )
        await controller.initialize_known_phase()

        state_builder = RLStateBuilder(
            controller=controller,
            queue_cap=experiment_config.queue_cap,
            vehicle_cap=experiment_config.vehicle_cap,
            use_vehicle_cnt=experiment_config.use_vehicle_cnt,
        )
        reward_builder = RLRewardBuilder(
            controller=controller,
            reward_scale=experiment_config.reward_scale,
        )
        evaluator = MetricsEvaluator(runner)

        lane_snapshot = await runner.get_lane_states()
        _log_lane_snapshot_sample(logger, lane_snapshot)
        obs = state_builder.build_all(lane_snapshot)

        losses: Dict[int, List[float]] = {
            junction_id: [] for junction_id in controller.junction_ids
        }
        episode_rewards: Dict[int, float] = {
            junction_id: 0.0 for junction_id in controller.junction_ids
        }
        raw_steps_done = 0

        for decision_idx in range(experiment_config.num_decisions):
            actions = {
                junction_id: agents[junction_id].act(
                    obs[junction_id], training=training
                )
                for junction_id in controller.junction_ids
            }
            await controller.apply_actions(actions)

            for _ in range(experiment_config.decision_steps):
                runner.step(1)
                raw_steps_done += 1
                controller.advance_time(experiment_config.interval)
                if (
                    experiment_config.metrics_sample_interval > 0
                    and raw_steps_done % experiment_config.metrics_sample_interval == 0
                ):
                    await evaluator.sample_metrics()

            done = decision_idx == experiment_config.num_decisions - 1
            try:
                next_snapshot = await runner.get_lane_states()
                rewards = reward_builder.compute_all(next_snapshot)
                next_obs = state_builder.build_all(next_snapshot)
            except Exception as e:
                if done and raw_steps_done >= experiment_config.total_steps:
                    logger.warning(
                        "终止决策后的 lane snapshot 获取失败，仿真已完成，使用上一时刻状态收尾: %s",
                        e,
                    )
                    next_snapshot = lane_snapshot
                    rewards = reward_builder.compute_all(next_snapshot)
                    next_obs = obs
                else:
                    raise

            for junction_id in controller.junction_ids:
                reward = rewards[junction_id]
                episode_rewards[junction_id] += reward
                if training:
                    agents[junction_id].remember(
                        obs[junction_id],
                        actions[junction_id],
                        reward,
                        next_obs[junction_id],
                        done,
                    )
                    loss = agents[junction_id].train_step()
                    if loss is not None:
                        losses[junction_id].append(loss)

            lane_snapshot = next_snapshot
            obs = next_obs

        if (
            experiment_config.metrics_sample_interval == 0
            or not evaluator.speed_samples
            or not evaluator.queue_samples
        ):
            await evaluator.sample_metrics()

        metrics = await evaluator.compute_final_metrics()
        metrics["episode_rewards"] = episode_rewards
        metrics["avg_loss_per_junction"] = {
            str(junction_id): (
                float(np.mean(junction_losses))
                if junction_losses
                else 0.0
            )
            for junction_id, junction_losses in losses.items()
        }
        metrics["epsilon_per_junction"] = {
            str(junction_id): float(agents[junction_id].epsilon)
            for junction_id in controller.junction_ids
        }
        metrics["raw_steps_done"] = raw_steps_done
        metrics["num_decisions"] = experiment_config.num_decisions
        metrics["controller"] = controller.metadata()

        logger.info(
            "episode 完成: avg_queue=%.4f avg_speed=%.4f avg_travel_time=%.4f",
            metrics.get("avg_queue", 0.0),
            metrics.get("avg_speed", 0.0),
            metrics.get("avg_travel_time", 0.0),
        )
        return metrics


def run_rl_experiment(
    scenario: str,
    mongo_uri: str,
    map_db: str,
    map_coll: str,
    agent_db: str,
    agent_coll: str,
    start_step: int,
    total_steps: int,
    interval: int,
    output_dir: str,
    rl_junction_ids: List[int],
    decision_interval_seconds: int,
    min_green_seconds: float,
    reward_scale: float = 1.0,
    episodes: int = 10,
    metrics_sample_interval: int = 100,
    checkpoint_path: str = "",
    queue_cap: float = 20.0,
    vehicle_cap: float = 30.0,
    use_vehicle_cnt: bool = False,
    seed: Optional[int] = None,
    output_sql_dsn: str = "",
    output_job_prefix: str = "rl_",
    output_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Dict[str, Any]:
    logger = logging.getLogger(__name__)
    os.makedirs(output_dir, exist_ok=True)
    _set_seed(seed)

    experiment_config = RLExperimentConfig(
        scenario=scenario,
        rl_junction_ids=rl_junction_ids,
        total_steps=total_steps,
        interval=interval,
        decision_interval_seconds=decision_interval_seconds,
        min_green_seconds=min_green_seconds,
        reward_scale=reward_scale,
        episodes=episodes,
        metrics_sample_interval=metrics_sample_interval,
        checkpoint_path=checkpoint_path,
        queue_cap=queue_cap,
        vehicle_cap=vehicle_cap,
        use_vehicle_cnt=use_vehicle_cnt,
        seed=seed,
    )
    hyperparams = DQNHyperParams()

    exp_config = {
        "scenario": scenario,
        "mongo_uri": mongo_uri,
        "map_db": map_db,
        "map_coll": map_coll,
        "agent_db": agent_db,
        "agent_coll": agent_coll,
        "start_step": start_step,
        "output_dir": output_dir,
        "seed": seed,
        "timestamp": time.strftime("%Y%m%d-%H%M%S"),
        "rl_experiment": experiment_config.to_dict(),
        "dqn_hyperparams": hyperparams.to_dict(),
    }
    _save_json(os.path.join(output_dir, "experiment_config.json"), exp_config)
    logger.info(
        "RL 配置: scenario=%s junction_ids=%s decision_steps=%d episodes=%d",
        scenario,
        rl_junction_ids,
        experiment_config.decision_steps,
        experiment_config.episodes,
    )

    sim_config = SimulatorConfig(
        mongo_uri=mongo_uri,
        map_db=map_db,
        map_coll=map_coll,
        agent_db=agent_db,
        agent_coll=agent_coll,
        start_step=start_step,
        total_step=total_steps,
        interval=interval,
        use_max_pressure=False,
        output_sql_dsn=output_sql_dsn,
        output_job_prefix=f"{output_job_prefix}{scenario}_",
        output_bbox=output_bbox,
    )

    bootstrap_log_dir = os.path.join(output_dir, "_bootstrap")
    os.makedirs(bootstrap_log_dir, exist_ok=True)
    with SimulationRunner(sim_config, bootstrap_log_dir) as bootstrap_runner:
        bootstrap_controller = RLController(
            sim_runner=bootstrap_runner,
            junction_ids=experiment_config.rl_junction_ids,
            min_green_seconds=experiment_config.min_green_seconds,
        )
        bootstrap_state_builder = RLStateBuilder(
            controller=bootstrap_controller,
            queue_cap=experiment_config.queue_cap,
            vehicle_cap=experiment_config.vehicle_cap,
            use_vehicle_cnt=experiment_config.use_vehicle_cnt,
        )
        controller_metadata = bootstrap_controller.metadata()
        _save_json(
            os.path.join(output_dir, "rl_junctions.json"),
            controller_metadata,
        )
        agents = {
            junction_id: DQNAgent(
                state_dim=bootstrap_state_builder.state_dim(junction_id),
                action_dim=bootstrap_controller.junction_specs[junction_id].action_dim,
                hyperparams=hyperparams,
                seed=None if seed is None else seed + idx,
            )
            for idx, junction_id in enumerate(bootstrap_controller.junction_ids)
        }

    if checkpoint_path:
        payload = _load_checkpoint(checkpoint_path)
        for junction_id, agent in agents.items():
            key = str(junction_id)
            if key not in payload.get("agents", {}):
                raise ValueError(f"checkpoint 中缺少路口 {junction_id} 的参数")
            agent.load_checkpoint(payload["agents"][key])
        logger.info("已加载 checkpoint: %s", checkpoint_path)

    if scenario == "dqn_train":
        history: List[Dict[str, Any]] = []
        for episode_idx in range(experiment_config.episodes):
            episode_log_dir = os.path.join(output_dir, f"episode_{episode_idx:03d}")
            os.makedirs(episode_log_dir, exist_ok=True)
            logger.info(
                "开始训练 episode=%d/%d, log_dir=%s",
                episode_idx + 1,
                experiment_config.episodes,
                episode_log_dir,
            )
            metrics = asyncio.run(
                _run_single_episode(
                    sim_config=sim_config,
                    episode_log_dir=episode_log_dir,
                    experiment_config=experiment_config,
                    hyperparams=hyperparams,
                    agents=agents,
                    training=True,
                )
            )
            metrics["episode_index"] = episode_idx
            history.append(metrics)
            _save_json(
                os.path.join(episode_log_dir, "metrics.json"),
                metrics,
            )

            latest_checkpoint = os.path.join(output_dir, "checkpoint_latest.pt")
            torch.save(
                _checkpoint_payload(
                    agents=agents,
                    experiment_config=experiment_config,
                    hyperparams=hyperparams,
                    controller_metadata=controller_metadata,
                ),
                latest_checkpoint,
            )
            logger.info("checkpoint 已保存: %s", latest_checkpoint)

        final_metrics = {
            "scenario": scenario,
            "episodes": experiment_config.episodes,
            "history": history,
            "final_episode": history[-1] if history else {},
            "checkpoint_path": os.path.join(output_dir, "checkpoint_latest.pt"),
        }
        _save_json(os.path.join(output_dir, "training_history.json"), final_metrics)
        _save_json(os.path.join(output_dir, "metrics.json"), final_metrics)
        return final_metrics

    if scenario == "dqn_eval":
        eval_log_dir = os.path.join(output_dir, "eval")
        os.makedirs(eval_log_dir, exist_ok=True)
        logger.info("开始 DQN 评估, log_dir=%s", eval_log_dir)
        metrics = asyncio.run(
            _run_single_episode(
                sim_config=sim_config,
                episode_log_dir=eval_log_dir,
                experiment_config=experiment_config,
                hyperparams=hyperparams,
                agents=agents,
                training=False,
            )
        )
        metrics["scenario"] = scenario
        _save_json(os.path.join(output_dir, "metrics.json"), metrics)
        return metrics

    raise ValueError(f"不支持的 RL scenario: {scenario}")
