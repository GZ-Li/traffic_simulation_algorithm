"""
算法三运行器 - 公交车利用率提升的对比实验
"""
import asyncio
import json
import logging
import os
import time
import random
import math
from typing import Dict, Any, Optional, List, Tuple

from trafficlight_rl.engine import SimulatorConfig
from ..runtime.bus_injector import BusInjectionConfig, add_bus_persons_with_plan
from ..runtime.bus_route_recorder import BusRouteRecorder
from ..runtime.sim_runner import SimulationRunner
from ..metrics.evaluator import MetricsEvaluator, BusUtilizationEvaluator
from .bus_selector import BusSelector
from .weighted_pressure import WeightedPressureController
from ..road_opt.fixed_timing import FixedTimingDeployer, generate_avg_split_timing
from ..road_opt.phase_collector import PhaseDurationCollector
from ..road_opt.phase_ratio import PhaseRatioEstimator

BUS_INJECTION_POST_WAIT_STEPS = 10
BUS_SIMULATION_STEP_MARGIN = 5


def _estimate_required_total_steps(
    scenario: str,
    total_steps: int,
    interval: int,
    cycle_seconds: float,
    mp_warmup_cycles: int,
    mp_collect_cycles: int,
    add_bus_count: int,
) -> int:
    """
    估算单次仿真最少需要的 total_step 预算。

    bus_opt 与 road_opt 不同，当前会在 AddPerson 后额外推进若干等待步。
    如果只按评估循环的 total_steps 配置仿真总步数，末尾容易出现
    “仿真已结束但 Python 侧还在切相位/采样”的收尾 RPC warning。

    这里的预算按“单次仿真”估算；对于两阶段场景，两次 SimulationRunner
    都会复用同一份 config，因此只需要保证每一次 run 各自够用即可。
    """
    post_wait_steps = BUS_INJECTION_POST_WAIT_STEPS if add_bus_count > 0 else 0
    required_steps = total_steps + post_wait_steps

    if scenario == "bus_weighted_mp_fixed":
        total_cycles = mp_warmup_cycles + mp_collect_cycles
        mp_steps = int(math.ceil((total_cycles * cycle_seconds) / interval))
        required_steps = max(required_steps, mp_steps + post_wait_steps)

    return required_steps + BUS_SIMULATION_STEP_MARGIN


def save_bus_ids(log_dir: str, bus_ids: List[int]):
    """保存公交车ID列表"""
    bus_file = os.path.join(log_dir, "bus_ids.json")
    with open(bus_file, "w") as f:
        json.dump({"bus_ids": bus_ids, "count": len(bus_ids)}, f, indent=2)


async def run_fixed_random_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    bus_ids: List[int],
    add_bus_count: int,
    bus_speed_threshold: float,
    cycle_seconds: float,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    seed: Optional[int] = None,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: 随机固定配时（无优化baseline）
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 总步数
        interval: 间隔（秒）
        bus_ids: 公交车ID列表
        add_bus_count: 通过AddPerson新增公交数量
        bus_speed_threshold: 速度阈值（m/s）
        cycle_seconds: 周期时长（秒）
        seed: 随机种子
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info(f"开始运行 FixedRandom scenario (周期={cycle_seconds}s, 种子={seed})")
    
    if seed is not None:
        random.seed(seed)
    
    with SimulationRunner(config, log_dir) as runner:
        if add_bus_count > 0:
            added_bus_ids = await add_bus_persons_with_plan(
                runner=runner,
                config=config,
                count=add_bus_count,
                log_dir=log_dir,
                injection_config=bus_injection_config,
                post_wait_steps=BUS_INJECTION_POST_WAIT_STEPS,
            )
            if added_bus_ids:
                bus_ids = bus_ids + added_bus_ids
                logger.info(f"新增公交车合并后总数: {len(bus_ids)}")
        save_bus_ids(log_dir, bus_ids)
        route_recorder = BusRouteRecorder(runner, bus_ids, log_dir)
        await route_recorder.sample()

        evaluator = MetricsEvaluator(runner)
        bus_evaluator = BusUtilizationEvaluator(runner, bus_ids, bus_speed_threshold)
        
        # 获取路口信息
        map_data = runner.get_map_data()
        
        # 生成随机固定配时方案
        fixed_timings = {}
        for junction in map_data.junctions:
            num_phases = len(junction.phases) if junction.phases else len(junction.fixed_program.phases)
            if num_phases > 0:
                # 平均分配作为"随机"baseline（可扩展为真随机）
                fixed_timings[junction.id] = generate_avg_split_timing(cycle_seconds, num_phases)
        
        # 保存配时方案
        timing_file = os.path.join(log_dir, "fixed_timing_random.json")
        with open(timing_file, "w") as f:
            json.dump(fixed_timings, f, indent=2)
        
        # 部署固定配时
        deployer = FixedTimingDeployer(runner, fixed_timings)
        await deployer.initialize()
        
        for step in range(total_steps):
            # 更新固定配时
            await deployer.update(interval)
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
                await bus_evaluator.sample(interval * sample_interval)
            
            runner.step(1)
            await route_recorder.sample()
            
            if (step + 1) % 100 == 0:
                logger.info(f"FixedRandom进度: {step + 1}/{total_steps}")
        route_file = route_recorder.dump_json()
        logger.info(f"公交运行时路径已保存: {route_file}")
        
        # 收集指标
        metrics = await evaluator.compute_final_metrics()
        bus_metrics = bus_evaluator.get_metrics()
        metrics.update(bus_metrics)
        
        logger.info(f"FixedRandom完成: 公交利用率={bus_metrics['bus_utilization']:.3f}")
        
        return metrics


async def run_bus_weighted_mp_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    bus_ids: List[int],
    add_bus_count: int,
    bus_speed_threshold: float,
    bus_weight_alpha: float,
    cycle_seconds: float,
    min_green_seconds: float,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    sample_interval: int = 5
) -> Dict[str, Any]:
    """
    Scenario: 公交车加权MaxPressure
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 总步数
        interval: 间隔（秒）
        bus_ids: 公交车ID列表
        add_bus_count: 通过AddPerson新增公交数量
        bus_speed_threshold: 速度阈值（m/s）
        bus_weight_alpha: 公交权重系数
        cycle_seconds: 周期时长（秒）
        min_green_seconds: 最小绿灯（秒）
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info(f"开始运行 BusWeightedMP scenario (alpha={bus_weight_alpha})")
    
    with SimulationRunner(config, log_dir) as runner:
        if add_bus_count > 0:
            added_bus_ids = await add_bus_persons_with_plan(
                runner=runner,
                config=config,
                count=add_bus_count,
                log_dir=log_dir,
                injection_config=bus_injection_config,
                post_wait_steps=BUS_INJECTION_POST_WAIT_STEPS,
            )
            if added_bus_ids:
                bus_ids = bus_ids + added_bus_ids
                logger.info(f"新增公交车合并后总数: {len(bus_ids)}")
        save_bus_ids(log_dir, bus_ids)
        route_recorder = BusRouteRecorder(runner, bus_ids, log_dir)
        await route_recorder.sample()

        evaluator = MetricsEvaluator(runner)
        bus_evaluator = BusUtilizationEvaluator(runner, bus_ids, bus_speed_threshold)
        
        # 获取路口信息
        map_data = runner.get_map_data()
        junction_ids = [j.id for j in map_data.junctions]
        
        # 初始化公交加权MP控制器
        controller = WeightedPressureController(
            sim_runner=runner,
            junction_ids=junction_ids,
            bus_ids=bus_ids,
            bus_weight_alpha=bus_weight_alpha,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            decision_interval=interval
        )
        
        for step in range(total_steps):
            # 更新公交加权MP
            await controller.update(interval)
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
                await bus_evaluator.sample(interval * sample_interval)
            
            runner.step(1)
            await route_recorder.sample()
            
            if (step + 1) % 100 == 0:
                logger.info(f"BusWeightedMP进度: {step + 1}/{total_steps}")
        route_file = route_recorder.dump_json()
        logger.info(f"公交运行时路径已保存: {route_file}")
        
        # 收集指标
        metrics = await evaluator.compute_final_metrics()
        bus_metrics = bus_evaluator.get_metrics()
        metrics.update(bus_metrics)
        
        logger.info(f"BusWeightedMP完成: 公交利用率={bus_metrics['bus_utilization']:.3f}")
        
        return metrics


async def run_bus_weighted_mp_then_fixed_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    bus_ids: List[int],
    add_bus_count: int,
    bus_speed_threshold: float,
    bus_weight_alpha: float,
    cycle_seconds: float,
    min_green_seconds: float,
    mp_warmup_cycles: int,
    mp_collect_cycles: int,
    min_phase_seconds: float,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: 公交加权MP后固定配时 - 两次仿真
    第一次: 运行公交加权MP并统计相位占比
    第二次: 使用固化配时运行并评估公交利用率
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 第二次仿真的总步数
        interval: 间隔（秒）
        bus_ids: 公交车ID列表
        add_bus_count: 通过AddPerson新增公交数量
        bus_speed_threshold: 公交速度阈值（m/s）
        bus_weight_alpha: 公交权重系数
        cycle_seconds: 周期时长（秒）
        min_green_seconds: 最小绿灯（秒）
        mp_warmup_cycles: MP热身周期数
        mp_collect_cycles: MP统计周期数
        min_phase_seconds: 相位最小时长（秒）
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info("开始运行 BusWeightedMP-then-Fixed scenario")
    
    # ===== 第一次仿真: 运行公交加权MP并统计 =====
    logger.info(f"第一次仿真: 公交加权MP统计 (热身={mp_warmup_cycles}周期, 统计={mp_collect_cycles}周期)")
    
    mp_log_dir = os.path.join(log_dir, "phase1_mp_collection")
    os.makedirs(mp_log_dir, exist_ok=True)
    
    # 计算第一次仿真需要的步数
    total_cycles = mp_warmup_cycles + mp_collect_cycles
    mp_steps = int((total_cycles * cycle_seconds) / interval)
    
    with SimulationRunner(config, mp_log_dir) as runner:
        # AddPerson 新增公交（第一次仿真）
        phase1_bus_ids = bus_ids.copy()
        if add_bus_count > 0:
            added_ids = await add_bus_persons_with_plan(
                runner=runner,
                config=config,
                count=add_bus_count,
                log_dir=mp_log_dir,
                injection_config=bus_injection_config,
                post_wait_steps=BUS_INJECTION_POST_WAIT_STEPS,
            )
            if added_ids:
                phase1_bus_ids = phase1_bus_ids + added_ids
                logger.info(f"第一次仿真新增公交: {len(added_ids)} 辆")
        phase1_route_recorder = BusRouteRecorder(runner, phase1_bus_ids, mp_log_dir)
        await phase1_route_recorder.sample()
        
        # 获取路口信息并过滤有效路口
        map_data = runner.get_map_data()
        valid_junction_ids = []
        num_phases_per_junction = {}
        for junction in map_data.junctions:
            num_phases = len(junction.phases) if junction.phases else len(junction.fixed_program.phases)
            if num_phases > 0:
                valid_junction_ids.append(junction.id)
                num_phases_per_junction[junction.id] = num_phases
        
        junction_ids = valid_junction_ids
        logger.info(f"找到 {len(junction_ids)} 个有效信控路口")
        
        # 初始化公交加权MP控制器
        controller = WeightedPressureController(
            sim_runner=runner,
            junction_ids=junction_ids,
            bus_ids=phase1_bus_ids,
            bus_weight_alpha=bus_weight_alpha,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            decision_interval=interval
        )
        
        # 初始化相位收集器
        collector = PhaseDurationCollector(
            junction_ids=junction_ids,
            cycle_seconds=cycle_seconds,
            num_phases_per_junction=num_phases_per_junction
        )
        
        # 运行公交加权MP
        for step in range(mp_steps):
            # 更新控制器
            await controller.update(interval)
            
            # 收集相位统计
            for jid in junction_ids:
                current_phase = controller.get_current_phase(jid)
                collector.update(jid, current_phase, interval)
            
            # 推进时间
            collector.advance_time(interval)
            runner.step(1)
            await phase1_route_recorder.sample()
            
            if (step + 1) % 100 == 0:
                logger.info(f"公交加权MP统计进度: {step + 1}/{mp_steps}")
        route_file = phase1_route_recorder.dump_json()
        logger.info(f"第一次仿真公交运行时路径已保存: {route_file}")
        
        # 获取统计结果
        stats = collector.get_statistics(warmup_cycles=mp_warmup_cycles)
        logger.info(f"收集到 {len(stats)} 个路口的相位统计")
    
    # 生成固定配时方案
    estimator = PhaseRatioEstimator(
        cycle_seconds=cycle_seconds,
        min_phase_seconds=min_phase_seconds
    )
    fixed_timings = estimator.estimate_all_junctions(stats)
    
    # 保存固定配时方案
    timing_file = os.path.join(log_dir, "fixed_timing_bus_weighted_mp.json")
    with open(timing_file, "w") as f:
        json.dump(fixed_timings, f, indent=2)
    logger.info(f"固定配时方案保存到: {timing_file}")
    
    # ===== 第二次仿真: 使用固定配时运行并评估 =====
    logger.info(f"第二次仿真: 固定配时评估 (总步数={total_steps})")
    
    eval_log_dir = os.path.join(log_dir, "phase2_evaluation")
    os.makedirs(eval_log_dir, exist_ok=True)
    
    with SimulationRunner(config, eval_log_dir) as runner:
        # AddPerson 新增公交（第二次仿真，重新生成ID）
        phase2_bus_ids = bus_ids.copy()
        if add_bus_count > 0:
            added_ids = await add_bus_persons_with_plan(
                runner=runner,
                config=config,
                count=add_bus_count,
                log_dir=eval_log_dir,
                injection_config=bus_injection_config,
                post_wait_steps=BUS_INJECTION_POST_WAIT_STEPS,
            )
            if added_ids:
                phase2_bus_ids = phase2_bus_ids + added_ids
                logger.info(f"第二次仿真新增公交: {len(added_ids)} 辆")
        
        # 保存第二次仿真的公交ID列表
        save_bus_ids(log_dir, phase2_bus_ids)
        phase2_route_recorder = BusRouteRecorder(runner, phase2_bus_ids, eval_log_dir)
        await phase2_route_recorder.sample()
        
        evaluator = MetricsEvaluator(runner)
        bus_evaluator = BusUtilizationEvaluator(runner, phase2_bus_ids, bus_speed_threshold)
        
        # 部署固定配时
        deployer = FixedTimingDeployer(runner, fixed_timings)
        await deployer.initialize()
        
        for step in range(total_steps):
            # 更新固定配时
            await deployer.update(interval)
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
                await bus_evaluator.sample(interval * sample_interval)
            
            runner.step(1)
            await phase2_route_recorder.sample()
            
            if (step + 1) % 100 == 0:
                logger.info(f"固定配时评估进度: {step + 1}/{total_steps}")
        route_file = phase2_route_recorder.dump_json()
        logger.info(f"第二次仿真公交运行时路径已保存: {route_file}")
        
        # 收集指标
        metrics = await evaluator.compute_final_metrics()
        bus_metrics = bus_evaluator.get_metrics()
        metrics.update(bus_metrics)
        
        logger.info(f"BusWeightedMP-then-Fixed完成: 公交利用率={bus_metrics['bus_utilization']:.3f}")
        
        return metrics


def run_bus_opt_experiment(
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
    bus_ids: List[int],
    add_bus_count: int,
    bus_speed_threshold: float,
    bus_weight_alpha: float = 2.0,
    cycle_seconds: float = 90.0,
    min_green_seconds: float = 5.0,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    seed: Optional[int] = None,
    mp_warmup_cycles: int = 10,
    mp_collect_cycles: int = 50,
    min_phase_seconds: float = 3.0,
    output_sql_dsn: str = "",
    output_job_prefix: str = "bus_opt_",
    output_bbox: Optional[Tuple[float, float, float, float]] = None
) -> Dict[str, Any]:
    """
    运行公交车优化实验
    
    Args:
        scenario: 场景名 ("fixed_random" | "bus_weighted_mp" | "bus_weighted_mp_fixed")
        mongo_uri: MongoDB URI
        map_db: 地图数据库
        map_coll: 地图集合
        agent_db: Agent数据库
        agent_coll: Agent集合
        start_step: 起始步
        total_steps: 总步数
        interval: 间隔（秒）
        output_dir: 输出目录
        bus_ids: 公交车ID列表
        add_bus_count: 通过AddPerson新增公交数量
        bus_speed_threshold: 公交速度阈值（m/s）
        bus_weight_alpha: 公交权重系数
        cycle_seconds: 周期时长（秒）
        min_green_seconds: 最小绿灯（秒）
        seed: 随机种子
        output_sql_dsn: SQL输出DSN
        output_job_prefix: Job名前缀
        output_bbox: 输出边界框
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存实验配置
    exp_config = {
        "scenario": scenario,
        "total_steps": total_steps,
        "interval": interval,
        "bus_ids": bus_ids,
        "num_buses": len(bus_ids),
        "bus_speed_threshold": bus_speed_threshold,
        "add_bus_count": add_bus_count,
        "bus_weight_alpha": bus_weight_alpha,
        "cycle_seconds": cycle_seconds,
        "min_green_seconds": min_green_seconds,
        "bus_injection_config": (
            None if bus_injection_config is None else bus_injection_config.to_dict()
        ),
        "mp_warmup_cycles": mp_warmup_cycles,
        "mp_collect_cycles": mp_collect_cycles,
        "min_phase_seconds": min_phase_seconds,
        "seed": seed,
        "timestamp": time.strftime("%Y%m%d-%H%M%S")
    }
    with open(os.path.join(output_dir, "experiment_config.json"), "w") as f:
        json.dump(exp_config, f, indent=2)
    
    # 保存公交车ID列表
    bus_file = os.path.join(output_dir, "bus_ids.json")
    with open(bus_file, "w") as f:
        json.dump({"bus_ids": bus_ids, "count": len(bus_ids)}, f, indent=2)
    
    estimated_total_step = _estimate_required_total_steps(
        scenario=scenario,
        total_steps=total_steps,
        interval=interval,
        cycle_seconds=cycle_seconds,
        mp_warmup_cycles=mp_warmup_cycles,
        mp_collect_cycles=mp_collect_cycles,
        add_bus_count=add_bus_count,
    )
    logger.info(
        "bus_opt 仿真步数预算: requested_total_steps=%s, configured_total_step=%s",
        total_steps,
        estimated_total_step,
    )

    # 创建配置
    config = SimulatorConfig(
        mongo_uri=mongo_uri,
        map_db=map_db,
        map_coll=map_coll,
        agent_db=agent_db,
        agent_coll=agent_coll,
        start_step=start_step,
        total_step=estimated_total_step,
        interval=interval,
        use_max_pressure=False,  # 我们手动控制
        output_sql_dsn=output_sql_dsn,
        output_job_prefix=f"{output_job_prefix}{scenario}_",
        output_bbox=output_bbox
    )
    
    # 根据scenario选择运行函数
    if scenario == "fixed_random":
        metrics = asyncio.run(run_fixed_random_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            bus_ids=bus_ids,
            add_bus_count=add_bus_count,
            bus_speed_threshold=bus_speed_threshold,
            cycle_seconds=cycle_seconds,
            bus_injection_config=bus_injection_config,
            seed=seed
        ))
    
    elif scenario == "bus_weighted_mp":
        metrics = asyncio.run(run_bus_weighted_mp_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            bus_ids=bus_ids,
            add_bus_count=add_bus_count,
            bus_speed_threshold=bus_speed_threshold,
            bus_weight_alpha=bus_weight_alpha,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            bus_injection_config=bus_injection_config
        ))
    
    elif scenario == "bus_weighted_mp_fixed":
        metrics = asyncio.run(run_bus_weighted_mp_then_fixed_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            bus_ids=bus_ids,
            add_bus_count=add_bus_count,
            bus_speed_threshold=bus_speed_threshold,
            bus_weight_alpha=bus_weight_alpha,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            mp_warmup_cycles=mp_warmup_cycles,
            mp_collect_cycles=mp_collect_cycles,
            min_phase_seconds=min_phase_seconds,
            bus_injection_config=bus_injection_config
        ))
    
    else:
        raise ValueError(f"不支持的scenario: {scenario}")
    
    # 保存最终指标
    metrics_file = os.path.join(output_dir, "metrics.json")
    with open(metrics_file, "w") as f:
        json.dump(metrics, f, indent=2)
    
    logger.info(f"\n实验完成！结果保存在: {output_dir}")
    
    return metrics
