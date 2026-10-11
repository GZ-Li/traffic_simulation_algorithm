"""
算法二运行器 - 道路资源优化的三种对比方案
"""
import asyncio
import json
import logging
import os
import random
import time
from typing import Dict, Any, Optional, Tuple, List

import numpy as np

from trafficlight_rl.engine import SimulatorConfig
from ..runtime.bus_injector import BusInjectionConfig, add_bus_persons_with_plan
from ..runtime.sim_runner import SimulationRunner
from ..metrics.evaluator import MetricsEvaluator, BusUtilizationEvaluator
from .max_pressure import MaxPressureController
from .phase_collector import PhaseDurationCollector
from .phase_ratio import PhaseRatioEstimator
from .fixed_timing import FixedTimingDeployer, generate_avg_split_timing
from .webster_timing import calculate_webster_timing_for_all_junctions


async def run_baseline_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: Baseline - 固定配时（simulet-go内置fixed_program自然运行）
    不主动控制信号灯
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 总步数
        interval: 间隔（秒）
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info("开始运行 Baseline scenario (固定配时自然运行)")
    
    with SimulationRunner(config, log_dir) as runner:
        evaluator = MetricsEvaluator(runner)
        
        for step in range(total_steps):
            # 不控制信号灯 - 让固定配时自然运行
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
            
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                logger.info(f"Baseline进度: {step + 1}/{total_steps}")
        
        metrics = await evaluator.compute_final_metrics()
        logger.info(f"Baseline完成: {metrics}")
        
        return metrics


async def run_avg_split_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    cycle_seconds: float,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: 平均分配 - 总周期固定，所有相位均分
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 总步数
        interval: 间隔（秒）
        cycle_seconds: 周期时长（秒）
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info(f"开始运行 AvgSplit scenario (周期={cycle_seconds}s)")
    
    with SimulationRunner(config, log_dir) as runner:
        evaluator = MetricsEvaluator(runner)
        
        # 获取路口信息并过滤有效路口
        map_data = runner.get_map_data()
        
        # 生成平均分配方案（只处理有相位的路口）
        fixed_timings = {}
        valid_junction_count = 0
        for junction in map_data.junctions:
            num_phases = len(junction.phases) if junction.phases else len(junction.fixed_program.phases)
            if num_phases > 0:
                fixed_timings[junction.id] = generate_avg_split_timing(cycle_seconds, num_phases)
                valid_junction_count += 1
        
        logger.info(f"找到 {valid_junction_count} 个有效信控路口（地图总路口数: {len(map_data.junctions)}）")
        
        # 保存配时方案
        timing_file = os.path.join(log_dir, "fixed_timing_avg_split.json")
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
            
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                logger.info(f"AvgSplit进度: {step + 1}/{total_steps}")
        
        metrics = await evaluator.compute_final_metrics()
        logger.info(f"AvgSplit完成: {metrics}")
        
        return metrics


async def run_mp_then_fixed_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    cycle_seconds: float,
    min_green_seconds: float,
    mp_warmup_cycles: int,
    mp_collect_cycles: int,
    min_phase_seconds: float,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: MP优化后固定配时 - 两次仿真
    第一次: 运行MP并统计相位占比
    第二次: 使用固化配时运行
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 第二次仿真的总步数
        interval: 间隔（秒）
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
    logger.info("开始运行 MP-then-Fixed scenario")
    
    # ===== 第一次仿真: 运行MP并统计 =====
    logger.info(f"第一次仿真: MP统计 (热身={mp_warmup_cycles}周期, 统计={mp_collect_cycles}周期)")
    
    mp_log_dir = os.path.join(log_dir, "run1_mp_collection")
    os.makedirs(mp_log_dir, exist_ok=True)
    
    # 计算第一次仿真需要的步数
    total_cycles = mp_warmup_cycles + mp_collect_cycles
    mp_steps = int((total_cycles * cycle_seconds) / interval)
    
    with SimulationRunner(config, mp_log_dir) as runner:
        # 获取路口信息并过滤有效路口
        map_data = runner.get_map_data()
        
        # 只保留有相位的路口
        valid_junction_ids = []
        num_phases_per_junction = {}
        for junction in map_data.junctions:
            num_phases = len(junction.phases) if junction.phases else len(junction.fixed_program.phases)
            if num_phases > 0:  # 只保留有相位的路口
                valid_junction_ids.append(junction.id)
                num_phases_per_junction[junction.id] = num_phases
        
        junction_ids = valid_junction_ids
        logger.info(f"找到 {len(junction_ids)} 个有效信控路口（地图总路口数: {len(map_data.junctions)}）")
        
        # 初始化MP控制器
        mp_controller = MaxPressureController(
            sim_runner=runner,
            junction_ids=junction_ids,
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
        
        # 运行MP
        for step in range(mp_steps):
            # MP更新
            await mp_controller.update(interval)
            
            # 收集相位统计
            for jid in junction_ids:
                current_phase = mp_controller.get_current_phase(jid)
                collector.update(jid, current_phase, interval)
            
            collector.advance_time(interval)
            
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                cycles_done = collector.get_num_completed_cycles()
                logger.info(f"MP进度: {step + 1}/{mp_steps}, 完成周期: {cycles_done}")
        
        # 获取统计结果
        statistics = collector.get_statistics(warmup_cycles=mp_warmup_cycles)
        
        # 保存统计
        stats_file = os.path.join(log_dir, "mp_phase_statistics.json")
        with open(stats_file, "w") as f:
            json.dump(statistics, f, indent=2)
        
        logger.info(f"MP统计完成，保存到: {stats_file}")
    
    # ===== 计算固化配时方案 =====
    logger.info("计算固化配时方案...")
    
    estimator = PhaseRatioEstimator(
        cycle_seconds=cycle_seconds,
        min_phase_seconds=min_phase_seconds
    )
    
    fixed_timings = estimator.estimate_all_junctions(statistics)
    
    # 保存固化配时方案
    timing_file = os.path.join(log_dir, "fixed_timing_from_mp.json")
    with open(timing_file, "w") as f:
        json.dump(fixed_timings, f, indent=2)
    
    logger.info(f"固化配时方案保存到: {timing_file}")
    
    # ===== 第二次仿真: 使用固化配时并评估 =====
    logger.info(f"第二次仿真: 固化配时评估 (总步数={total_steps})")
    
    fixed_log_dir = os.path.join(log_dir, "run2_fixed_evaluation")
    os.makedirs(fixed_log_dir, exist_ok=True)
    
    with SimulationRunner(config, fixed_log_dir) as runner:
        evaluator = MetricsEvaluator(runner)
        
        # 部署固定配时
        deployer = FixedTimingDeployer(runner, fixed_timings)
        await deployer.initialize()
        
        for step in range(total_steps):
            # 更新固定配时
            await deployer.update(interval)
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
            
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                logger.info(f"固化配时进度: {step + 1}/{total_steps}")
        
        metrics = await evaluator.compute_final_metrics()
        logger.info(f"MP-then-Fixed完成: {metrics}")
        
        return metrics


async def run_webster_scenario(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    bus_ids: List[int],
    add_bus_count: int,
    bus_speed_threshold: float,
    cycle_seconds: float,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    min_green_seconds: float = 5.0,
    sample_steps: int = 10,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    Scenario: Webster配时 - 基于流量采样计算固定配时
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 评估阶段的总步数
        interval: 间隔（秒）
        cycle_seconds: 周期时长（秒）
        min_green_seconds: 最小绿灯（秒）
        sample_steps: 采样步数（用于计算Webster配时）
        sample_interval: 评估阶段的采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    logger.info(f"开始运行 Webster scenario (周期={cycle_seconds}s)")
    
    # ===== 第一阶段: 采样并计算Webster配时 =====
    logger.info(f"第一阶段: 采样 {sample_steps} 步以计算Webster配时")
    
    sample_log_dir = os.path.join(log_dir, "phase1_sampling")
    os.makedirs(sample_log_dir, exist_ok=True)
    
    with SimulationRunner(config, sample_log_dir) as runner:
        # 获取有效路口
        map_data = runner.get_map_data()
        valid_junction_ids = []
        for junction in map_data.junctions:
            num_phases = len(junction.phases) if junction.phases else len(junction.fixed_program.phases)
            if num_phases > 0:
                valid_junction_ids.append(junction.id)
        
        logger.info(f"找到 {len(valid_junction_ids)} 个有效信控路口")
        
        # 计算Webster配时
        webster_timings = await calculate_webster_timing_for_all_junctions(
            sim_runner=runner,
            junction_ids=valid_junction_ids,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            sample_steps=sample_steps
        )
    
    # 保存Webster配时方案
    timing_file = os.path.join(log_dir, "fixed_timing_webster.json")
    with open(timing_file, "w") as f:
        json.dump(webster_timings, f, indent=2)
    logger.info(f"Webster配时方案保存到: {timing_file}")
    
    # ===== 第二阶段: 使用Webster配时运行并评估 =====
    logger.info(f"第二阶段: Webster配时评估 (总步数={total_steps})")
    
    eval_log_dir = os.path.join(log_dir, "phase2_evaluation")
    os.makedirs(eval_log_dir, exist_ok=True)
    
    with SimulationRunner(config, eval_log_dir) as runner:
        evaluator = MetricsEvaluator(runner)

        # （可选）新增公交并统计公交利用率
        effective_bus_ids: List[int] = list(bus_ids) if bus_ids else []
        if add_bus_count > 0:
            added_bus_ids = await add_bus_persons_with_plan(
                runner=runner,
                config=config,
                count=add_bus_count,
                log_dir=eval_log_dir,
                injection_config=bus_injection_config,
                post_wait_steps=10,
            )
            if added_bus_ids:
                effective_bus_ids.extend(added_bus_ids)
                logger.info(f"新增公交车合并后总数: {len(effective_bus_ids)}")

        bus_evaluator: Optional[BusUtilizationEvaluator] = None
        if effective_bus_ids:
            bus_evaluator = BusUtilizationEvaluator(runner, effective_bus_ids, bus_speed_threshold)
        
        # 部署Webster配时
        deployer = FixedTimingDeployer(runner, webster_timings)
        await deployer.initialize()
        
        for step in range(total_steps):
            # 更新固定配时
            await deployer.update(interval)
            
            # 定期采样
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
                if bus_evaluator:
                    await bus_evaluator.sample(interval * sample_interval)
            
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                logger.info(f"Webster评估进度: {step + 1}/{total_steps}")
        
        metrics = await evaluator.compute_final_metrics()
        if bus_evaluator:
            metrics.update(bus_evaluator.get_metrics())
        logger.info(f"Webster完成: {metrics}")
        
        return metrics


def run_road_opt_experiment(
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
    cycle_seconds: Optional[float] = None,
    min_green_seconds: float = 5.0,
    mp_warmup_cycles: int = 10,
    mp_collect_cycles: int = 50,
    min_phase_seconds: float = 3.0,
    webster_sample_steps: int = 1,
    bus_ids: Optional[List[int]] = None,
    add_bus_count: int = 0,
    bus_speed_threshold: float = 5.0,
    bus_injection_config: Optional[BusInjectionConfig] = None,
    seed: Optional[int] = None,
    output_sql_dsn: str = "",
    output_job_prefix: str = "road_opt_",
    output_bbox: Optional[Tuple[float, float, float, float]] = None
) -> Dict[str, Any]:
    """
    运行道路资源优化实验
    
    Args:
        scenario: 场景名 ("fixed_program_baseline" | "fixed_avg_split" | "fixed_from_mp" | "webster")
        其他参数见各scenario函数
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    os.makedirs(output_dir, exist_ok=True)
    
    # 设置随机种子
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        logger.info(f"设置随机种子: {seed}")
    
    # 保存实验配置
    exp_config = {
        "scenario": scenario,
        "total_steps": total_steps,
        "interval": interval,
        "cycle_seconds": cycle_seconds,
        "min_green_seconds": min_green_seconds,
        "mp_warmup_cycles": mp_warmup_cycles,
        "mp_collect_cycles": mp_collect_cycles,
        "min_phase_seconds": min_phase_seconds,
        "bus_ids": bus_ids or [],
        "num_buses": len(bus_ids or []),
        "add_bus_count": add_bus_count,
        "bus_speed_threshold": bus_speed_threshold,
        "bus_injection_config": (
            None if bus_injection_config is None else bus_injection_config.to_dict()
        ),
        "seed": seed,
        "timestamp": time.strftime("%Y%m%d-%H%M%S")
    }
    with open(os.path.join(output_dir, "experiment_config.json"), "w") as f:
        json.dump(exp_config, f, indent=2)

    # 保存公交车ID列表（可选）
    with open(os.path.join(output_dir, "bus_ids.json"), "w") as f:
        json.dump({"bus_ids": bus_ids or [], "count": len(bus_ids or [])}, f, indent=2)
    
    # 创建配置
    config = SimulatorConfig(
        mongo_uri=mongo_uri,
        map_db=map_db,
        map_coll=map_coll,
        agent_db=agent_db,
        agent_coll=agent_coll,
        start_step=start_step,
        total_step=total_steps * 2,  # 预留足够空间
        interval=interval,
        use_max_pressure=False,  # 我们手动控制
        output_sql_dsn=output_sql_dsn,
        output_job_prefix=f"{output_job_prefix}{scenario}_",
        output_bbox=output_bbox
    )
    
    # 根据scenario选择运行函数
    if scenario == "fixed_program_baseline":
        metrics = asyncio.run(run_baseline_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval
        ))
    
    elif scenario == "fixed_avg_split":
        if not cycle_seconds:
            raise ValueError("fixed_avg_split需要指定cycle_seconds参数")
        
        metrics = asyncio.run(run_avg_split_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            cycle_seconds=cycle_seconds
        ))
    
    elif scenario == "fixed_from_mp":
        if not cycle_seconds:
            raise ValueError("fixed_from_mp需要指定cycle_seconds参数")
        
        metrics = asyncio.run(run_mp_then_fixed_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            mp_warmup_cycles=mp_warmup_cycles,
            mp_collect_cycles=mp_collect_cycles,
            min_phase_seconds=min_phase_seconds
        ))
    
    elif scenario == "webster":
        if not cycle_seconds:
            raise ValueError("webster需要指定cycle_seconds参数")
        
        metrics = asyncio.run(run_webster_scenario(
            config=config,
            log_dir=output_dir,
            total_steps=total_steps,
            interval=interval,
            bus_ids=bus_ids or [],
            add_bus_count=add_bus_count,
            bus_speed_threshold=bus_speed_threshold,
            bus_injection_config=bus_injection_config,
            cycle_seconds=cycle_seconds,
            min_green_seconds=min_green_seconds,
            sample_steps=webster_sample_steps
        ))
    
    else:
        raise ValueError(f"不支持的scenario: {scenario}")
    
    # 保存最终指标
    metrics_file = os.path.join(output_dir, "metrics.json")
    with open(metrics_file, "w") as f:
        json.dump(metrics, f, indent=2)
    
    logger.info(f"\n实验完成！结果保存在: {output_dir}")
    
    return metrics
