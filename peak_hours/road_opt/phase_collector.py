"""
相位时长收集器 - 按周期统计各相位时长占比
"""
import logging
from typing import Dict, List
import numpy as np


class PhaseDurationCollector:
    """
    相位时长收集器 - 统计MaxPressure运行时各相位占用时长
    按周期为粒度进行统计
    """

    def __init__(self, junction_ids: List[int], cycle_seconds: float, num_phases_per_junction: Dict[int, int]):
        """
        Args:
            junction_ids: 路口ID列表
            cycle_seconds: 信号灯周期（秒）
            num_phases_per_junction: {junction_id: 相位数量}
        """
        self.junction_ids = junction_ids
        self.cycle_seconds = cycle_seconds
        self.num_phases_per_junction = num_phases_per_junction
        self.logger = logging.getLogger(__name__)
        
        # 当前周期内各相位累积时长: {junction_id: [phase_0_time, phase_1_time, ...]}
        self.current_cycle_durations: Dict[int, List[float]] = {}
        
        # 已完成周期的统计: {junction_id: [[cycle1], [cycle2], ...]}
        self.completed_cycles: Dict[int, List[List[float]]] = {}
        
        # 当前周期累积时间
        self.current_cycle_time = 0.0
        
        # 初始化
        for jid in junction_ids:
            num_phases = num_phases_per_junction[jid]
            self.current_cycle_durations[jid] = [0.0] * num_phases
            self.completed_cycles[jid] = []

    def update(self, junction_id: int, current_phase: int, dt: float):
        """
        更新统计
        
        Args:
            junction_id: 路口ID
            current_phase: 当前相位索引
            dt: 时间步长（秒）
        """
        if junction_id not in self.current_cycle_durations:
            return
        
        # 累积当前相位时长
        if 0 <= current_phase < len(self.current_cycle_durations[junction_id]):
            self.current_cycle_durations[junction_id][current_phase] += dt

    def advance_time(self, dt: float):
        """
        推进时间，检查是否完成一个周期
        
        Args:
            dt: 时间步长（秒）
        """
        self.current_cycle_time += dt
        
        # 检查是否完成一个周期
        if self.current_cycle_time >= self.cycle_seconds:
            self._complete_cycle()
            self.current_cycle_time = 0.0

    def _complete_cycle(self):
        """完成一个周期，记录统计并重置"""
        for jid in self.junction_ids:
            # 保存当前周期的相位时长
            cycle_data = self.current_cycle_durations[jid].copy()
            self.completed_cycles[jid].append(cycle_data)
            
            # 重置当前周期
            self.current_cycle_durations[jid] = [0.0] * len(self.current_cycle_durations[jid])
        
        num_cycles = len(self.completed_cycles[self.junction_ids[0]]) if self.junction_ids else 0
        self.logger.debug(f"完成周期 #{num_cycles}")

    def get_statistics(self, warmup_cycles: int = 0) -> Dict[int, Dict[str, any]]:
        """
        获取统计结果（跳过热身周期）
        
        Args:
            warmup_cycles: 跳过的热身周期数
            
        Returns:
            {junction_id: {
                "phase_ratios": [ratio0, ratio1, ...],  # 相位时长占比
                "phase_durations_mean": [dur0, dur1, ...],  # 平均时长（秒）
                "num_cycles": 统计的周期数
            }}
        """
        stats = {}
        
        for jid in self.junction_ids:
            cycles = self.completed_cycles[jid][warmup_cycles:]
            
            if not cycles:
                self.logger.warning(f"路口 {jid} 没有可用的周期数据")
                continue
            
            # 计算平均时长
            avg_durations = np.mean(cycles, axis=0)
            total_duration = np.sum(avg_durations)
            
            # 计算比例
            if total_duration > 0:
                ratios = avg_durations / total_duration
            else:
                ratios = np.ones(len(avg_durations)) / len(avg_durations)
            
            stats[jid] = {
                "phase_ratios": ratios.tolist(),
                "phase_durations_mean": avg_durations.tolist(),
                "num_cycles": len(cycles)
            }
        
        return stats

    def get_num_completed_cycles(self) -> int:
        """获取已完成的周期数"""
        if self.junction_ids:
            return len(self.completed_cycles[self.junction_ids[0]])
        return 0
