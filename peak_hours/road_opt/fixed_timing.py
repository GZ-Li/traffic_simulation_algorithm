"""
固定配时部署器 - 按固定时长循环执行相位
"""
import asyncio
import logging
from typing import Dict, List


class FixedTimingDeployer:
    """
    固定配时部署器 - 按固定方案循环执行相位
    只使用已有的SetTrafficLightPhase接口实现
    """

    def __init__(self, sim_runner, fixed_timings: Dict[int, List[float]]):
        """
        Args:
            sim_runner: SimulationRunner实例
            fixed_timings: {junction_id: [duration0, duration1, ...]}（秒）
        """
        self.sim_runner = sim_runner
        self.fixed_timings = fixed_timings
        self.logger = logging.getLogger(__name__)
        
        # 当前相位索引
        self.current_phase_indices: Dict[int, int] = {}
        # 当前相位剩余时间
        self.phase_remaining_times: Dict[int, float] = {}
        
        # 初始化
        for junction_id in fixed_timings.keys():
            self.current_phase_indices[junction_id] = 0
            self.phase_remaining_times[junction_id] = fixed_timings[junction_id][0]

    async def initialize(self):
        """初始化 - 设置所有路口的初始相位"""
        success_count = 0
        failed_count = 0
        
        for junction_id, durations in self.fixed_timings.items():
            if not durations:
                self.logger.debug(f"路口 {junction_id} 无配时数据，跳过")
                continue
            
            try:
                await self.sim_runner.set_traffic_light_phase(
                    junction_id=junction_id,
                    phase_index=0,
                    time_remaining=durations[0]
                )
                self.logger.debug(f"路口 {junction_id} 初始化为相位 0，时长 {durations[0]}s")
                success_count += 1
            except Exception as e:
                self.logger.warning(f"路口 {junction_id} 初始化失败: {e}")
                failed_count += 1
        
        self.logger.info(f"固定配时初始化完成: 成功 {success_count} 个，失败 {failed_count} 个")

    async def update(self, dt: float):
        """
        更新固定配时状态
        
        Args:
            dt: 时间步长（秒）
        """
        for junction_id, durations in self.fixed_timings.items():
            if not durations:
                continue
            
            # 减少剩余时间
            self.phase_remaining_times[junction_id] -= dt
            
            # 如果当前相位结束，切换到下一相位
            if self.phase_remaining_times[junction_id] <= 0:
                # 循环到下一相位
                num_phases = len(durations)
                next_phase = (self.current_phase_indices[junction_id] + 1) % num_phases
                
                # 设置新相位
                try:
                    await self.sim_runner.set_traffic_light_phase(
                        junction_id=junction_id,
                        phase_index=next_phase,
                        time_remaining=durations[next_phase]
                    )
                    
                    self.logger.debug(
                        f"路口 {junction_id}: 切换到相位 {next_phase}，"
                        f"时长 {durations[next_phase]}s"
                    )
                    
                    # 更新状态
                    self.current_phase_indices[junction_id] = next_phase
                    self.phase_remaining_times[junction_id] = durations[next_phase]
                except Exception as e:
                    self.logger.warning(f"路口 {junction_id} 切换相位失败: {e}")
                    # 即使失败也更新状态，避免重复尝试
                    self.current_phase_indices[junction_id] = next_phase
                    self.phase_remaining_times[junction_id] = durations[next_phase]


def generate_avg_split_timing(cycle_seconds: float, num_phases: int) -> List[float]:
    """
    生成平均分配的固定配时方案
    
    Args:
        cycle_seconds: 周期时长（秒）
        num_phases: 相位数量
        
    Returns:
        各相位时长列表
    """
    if num_phases == 0:
        return []
    
    base_duration = cycle_seconds / num_phases
    durations = [base_duration] * num_phases
    
    # 修正浮点误差
    diff = cycle_seconds - sum(durations)
    if diff != 0:
        durations[0] += diff
    
    return durations
