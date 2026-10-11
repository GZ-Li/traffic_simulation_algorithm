"""
相位比例估算器 - 根据统计结果计算固化配时方案
"""
import logging
from typing import Dict, List
import numpy as np


class PhaseRatioEstimator:
    """
    相位比例估算器 - 将MP统计结果转化为固定配时方案
    """

    def __init__(
        self,
        cycle_seconds: float,
        min_phase_seconds: float = 3.0,
        max_phase_seconds: float = None
    ):
        """
        Args:
            cycle_seconds: 信号灯周期（秒）
            min_phase_seconds: 相位最小时长（秒）
            max_phase_seconds: 相位最大时长（秒），None表示不限制
        """
        self.cycle_seconds = cycle_seconds
        self.min_phase_seconds = min_phase_seconds
        self.max_phase_seconds = max_phase_seconds if max_phase_seconds else cycle_seconds
        self.logger = logging.getLogger(__name__)

    def estimate_fixed_timing(self, phase_ratios: List[float]) -> List[float]:
        """
        根据相位比例估算固定配时时长
        
        Args:
            phase_ratios: 相位比例列表（和为1）
            
        Returns:
            各相位固定时长（秒）列表
        """
        if not phase_ratios:
            return []
        
        num_phases = len(phase_ratios)
        max_duration = self.cycle_seconds * 0.3  # 30% 上限
        
        # 步骤1: 按原始比例分配周期
        durations = [ratio * self.cycle_seconds for ratio in phase_ratios]
        self.logger.debug(f"初始分配: {[f'{d:.2f}' for d in durations]}")
        
        # 步骤2: 应用上下限约束（不保证总和）
        for i in range(num_phases):
            if durations[i] < self.min_phase_seconds:
                durations[i] = self.min_phase_seconds
            elif durations[i] > max_duration:
                durations[i] = max_duration
        
        self.logger.debug(f"应用约束后: {[f'{d:.2f}' for d in durations]}, 总和={sum(durations):.2f}")
        
        # 步骤3: 智能调整总和到周期（迭代算法）
        max_iterations = 100
        tolerance = 0.01
        
        for iteration in range(max_iterations):
            total = sum(durations)
            diff = self.cycle_seconds - total
            
            if abs(diff) < tolerance:
                break  # 已经满足
            
            if diff > 0:
                # 需要增加时间，优先给最小的相位（但不超过上限）
                adjustable = [(i, max_duration - durations[i]) for i in range(num_phases) 
                             if durations[i] < max_duration]
                adjustable.sort(key=lambda x: durations[x[0]])  # 按当前时长排序
                
                if not adjustable:
                    self.logger.warning(f"所有相位都已达到上限 {max_duration:.2f}秒，无法增加时间")
                    break
                
                # 计算可增加的总空间
                total_space = sum(space for _, space in adjustable)
                
                if total_space < diff:
                    # 空间不足，全部用完
                    for i, space in adjustable:
                        durations[i] += space
                else:
                    # 按比例分配
                    for i, space in adjustable:
                        addition = diff * (space / total_space)
                        durations[i] += addition
                        
            else:  # diff < 0
                # 需要削减时间，优先从最大的相位扣（但不低于下限）
                adjustable = [(i, durations[i] - self.min_phase_seconds) for i in range(num_phases) 
                             if durations[i] > self.min_phase_seconds]
                adjustable.sort(key=lambda x: durations[x[0]], reverse=True)  # 按当前时长倒序
                
                if not adjustable:
                    self.logger.error(
                        f"所有相位都已达到下限 {self.min_phase_seconds}秒，无法削减时间！"
                        f"周期 {self.cycle_seconds}秒 < {num_phases} × {self.min_phase_seconds}秒"
                    )
                    break
                
                # 计算可削减的总空间
                total_space = sum(space for _, space in adjustable)
                
                if total_space < abs(diff):
                    # 空间不足，全部用完
                    for i, space in adjustable:
                        durations[i] -= space
                else:
                    # 按比例分配
                    for i, space in adjustable:
                        reduction = abs(diff) * (space / total_space)
                        durations[i] -= reduction
        
        self.logger.debug(f"迭代调整后: {[f'{d:.2f}' for d in durations]}, 总和={sum(durations):.2f}")
        
        # 步骤4: 四舍五入到 0.1 秒
        durations = [round(d, 1) for d in durations]
        
        # 步骤5: 修正四舍五入误差（加到最大的相位，同时检查约束）
        final_diff = self.cycle_seconds - sum(durations)
        if abs(final_diff) > 0.01:
            # 找到可以调整的相位（如果增加，找未达到上限的；如果减少，找未达到下限的）
            if final_diff > 0:
                candidates = [i for i in range(num_phases) if durations[i] < max_duration]
                if candidates:
                    max_idx = max(candidates, key=lambda i: durations[i])
                    durations[max_idx] = round(durations[max_idx] + final_diff, 1)
            else:
                candidates = [i for i in range(num_phases) if durations[i] > self.min_phase_seconds]
                if candidates:
                    max_idx = max(candidates, key=lambda i: durations[i])
                    durations[max_idx] = round(durations[max_idx] + final_diff, 1)
        
        # 验证最终结果
        final_total = sum(durations)
        violations = []
        for i in range(num_phases):
            if durations[i] < self.min_phase_seconds - 0.01:
                violations.append(f"相位{i}: {durations[i]:.1f} < {self.min_phase_seconds}")
            if durations[i] > max_duration + 0.01:
                violations.append(f"相位{i}: {durations[i]:.1f} > {max_duration:.1f}")
        
        if violations:
            self.logger.warning(f"约束违反: {', '.join(violations)}")
        
        self.logger.info(
            f"最终固定配时: {[f'{d:.1f}' for d in durations]} "
            f"(总和={final_total:.1f}, 目标={self.cycle_seconds})"
        )
        
        return durations

    def estimate_all_junctions(
        self,
        statistics: Dict[int, Dict[str, any]]
    ) -> Dict[int, List[float]]:
        """
        为所有路口估算固定配时
        
        Args:
            statistics: PhaseDurationCollector.get_statistics()的输出
            
        Returns:
            {junction_id: [duration0, duration1, ...]}
        """
        fixed_timings = {}
        
        for junction_id, stats in statistics.items():
            phase_ratios = stats["phase_ratios"]
            durations = self.estimate_fixed_timing(phase_ratios)
            fixed_timings[junction_id] = durations
            
            self.logger.info(
                f"路口 {junction_id}: 比例={[f'{r:.3f}' for r in phase_ratios]}, "
                f"时长={[f'{d:.1f}' for d in durations]}"
            )
        
        return fixed_timings
