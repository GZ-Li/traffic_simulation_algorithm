"""
算法一运行器 - 不同车辆数的扫描实验
"""
import asyncio
import json
import logging
import os
import time
from typing import List, Dict, Any, Optional, Tuple

from trafficlight_rl.engine import SimulatorConfig
from ..runtime.sim_runner import SimulationRunner
from ..metrics.evaluator import MetricsEvaluator
from .vehicle_builder import VehicleSetBuilder


async def run_single_vehicle_config(
    config: SimulatorConfig,
    log_dir: str,
    total_steps: int,
    interval: int,
    sample_interval: int = 10
) -> Dict[str, Any]:
    """
    运行单次车辆配置实验（baseline，不控灯）
    
    Args:
        config: 仿真配置
        log_dir: 日志目录
        total_steps: 总仿真步数
        interval: 仿真间隔（秒）
        sample_interval: 采样间隔（步）
        
    Returns:
        指标字典
    """
    logger = logging.getLogger(__name__)
    
    with SimulationRunner(config, log_dir) as runner:
        evaluator = MetricsEvaluator(runner)
        
        logger.info(f"开始仿真，总步数: {total_steps}")
        
        for step in range(total_steps):
            # 不控制信号灯 - 让simulet-go的fixed_program自然运行
            
            # 定期采样指标
            if step % sample_interval == 0:
                await evaluator.sample_metrics()
            
            # 推进仿真
            runner.step(1)
            
            if (step + 1) % 100 == 0:
                logger.info(f"进度: {step + 1}/{total_steps}")
        
        # 计算最终指标
        metrics = await evaluator.compute_final_metrics()
        logger.info(f"仿真完成，指标: {metrics}")
        
        return metrics


def run_vehicle_config_experiment(
    mongo_uri: str,
    map_db: str,
    map_coll: str,
    agent_db: str,
    agent_coll: str,
    vehicle_counts: List[int],
    start_step: int,
    total_steps: int,
    interval: int,
    output_dir: str,
    seed: Optional[int] = None,
    output_sql_dsn: str = "",
    output_job_prefix: str = "vehicle_config_",
    output_bbox: Optional[Tuple[float, float, float, float]] = None
) -> Dict[str, Any]:
    """
    运行车辆配置扫描实验
    
    Args:
        mongo_uri: MongoDB URI
        map_db: 地图数据库
        map_coll: 地图集合
        agent_db: Agent数据库
        agent_coll: Agent源集合
        vehicle_counts: 车辆数量列表
        start_step: 起始步
        total_steps: 总步数
        interval: 间隔（秒）
        output_dir: 输出目录
        seed: 随机种子
        output_sql_dsn: SQL输出DSN
        output_job_prefix: Job名前缀
        output_bbox: 输出边界框
        
    Returns:
        汇总结果
    """
    logger = logging.getLogger(__name__)
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存实验配置
    exp_config = {
        "vehicle_counts": vehicle_counts,
        "start_step": start_step,
        "total_steps": total_steps,
        "interval": interval,
        "seed": seed,
        "timestamp": time.strftime("%Y%m%d-%H%M%S")
    }
    with open(os.path.join(output_dir, "experiment_config.json"), "w") as f:
        json.dump(exp_config, f, indent=2)
    
    builder = VehicleSetBuilder(mongo_uri, agent_db, agent_coll)
    results = {}
    
    for vehicle_count in vehicle_counts:
        logger.info(f"\n{'='*60}")
        logger.info(f"开始实验: 车辆数 = {vehicle_count}")
        logger.info(f"{'='*60}")
        
        # 创建运行时collection
        runtime_coll = f"{agent_coll}_runtime_{vehicle_count}_{int(time.time())}"
        vehicle_ids = builder.build_downsampled_collection(
            target_vehicle_count=vehicle_count,
            agent_coll_runtime=runtime_coll,
            mode="random",
            seed=seed
        )
        
        # 保存车辆ID列表
        vehicle_log = os.path.join(output_dir, f"vehicles_{vehicle_count}.json")
        with open(vehicle_log, "w") as f:
            json.dump({"vehicle_ids": vehicle_ids, "count": len(vehicle_ids)}, f, indent=2)
        
        try:
            # 创建配置（使用运行时collection）
            config = SimulatorConfig(
                mongo_uri=mongo_uri,
                map_db=map_db,
                map_coll=map_coll,
                agent_db=agent_db,
                agent_coll=runtime_coll,  # 使用运行时collection
                start_step=start_step,
                total_step=total_steps,
                interval=interval,
                use_max_pressure=False,  # Baseline不使用max_pressure
                output_sql_dsn=output_sql_dsn,
                output_job_prefix=f"{output_job_prefix}{vehicle_count}_",
                output_bbox=output_bbox
            )
            
            # 运行仿真
            run_log_dir = os.path.join(output_dir, f"run_{vehicle_count}")
            os.makedirs(run_log_dir, exist_ok=True)
            
            metrics = asyncio.run(run_single_vehicle_config(
                config=config,
                log_dir=run_log_dir,
                total_steps=total_steps,
                interval=interval
            ))
            
            results[vehicle_count] = metrics
            
            # 保存单次结果
            result_file = os.path.join(run_log_dir, "metrics.json")
            with open(result_file, "w") as f:
                json.dump(metrics, f, indent=2)
            
            logger.info(f"车辆数 {vehicle_count} 实验完成")
            
        finally:
            # 清理运行时collection
            builder.cleanup_runtime_collection(runtime_coll)
    
    # 保存汇总结果
    summary_file = os.path.join(output_dir, "summary.json")
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\n所有实验完成！结果保存在: {output_dir}")
    
    return results
