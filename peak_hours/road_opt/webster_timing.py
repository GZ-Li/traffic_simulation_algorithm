"""
Webster信号配时算法 - 简化实现
基于交通流量（排队数）计算各相位的绿灯时长
"""
import logging
from typing import Dict, List
import numpy as np


class WebsterTimingCalculator:
    """
    Webster配时计算器 - 简化版
    
    原理：
    1. 采样各车道的排队数
    2. 计算各相位的流量比 y_i = q_i / s_i (流量/饱和流量)
    3. 各相位绿灯时长 g_i = (y_i / ΣY) * 有效绿灯时间
    """
    
    def __init__(
        self,
        cycle_seconds: float,
        min_green_seconds: float = 5.0,
        loss_time_per_phase: float = 3.0,
        saturation_flow: float = 1800.0  # 饱和流量（辆/小时/车道）
    ):
        """
        Args:
            cycle_seconds: 信号周期（秒）
            min_green_seconds: 最小绿灯时间（秒）
            loss_time_per_phase: 每个相位的损失时间（秒），包括黄灯+全红
            saturation_flow: 饱和流量（辆/小时/车道），用于归一化
        """
        self.cycle_seconds = cycle_seconds
        self.min_green_seconds = min_green_seconds
        self.loss_time_per_phase = loss_time_per_phase
        self.saturation_flow = saturation_flow
        self.logger = logging.getLogger(__name__)
    
    def calculate_phase_flow_ratios(
        self,
        phase_lanes: List[List[int]],  # 每个相位的车道ID列表
        lane_queue_counts: Dict[int, int]  # 车道排队数
    ) -> List[float]:
        """
        计算各相位的流量比
        
        Args:
            phase_lanes: [[lane_ids for phase 0], [lane_ids for phase 1], ...]
            lane_queue_counts: {lane_id: queue_count}
        
        Returns:
            各相位的流量比列表
        """
        flow_ratios = []
        
        for phase_lane_ids in phase_lanes:
            # 该相位所有车道的总排队数
            total_queue = sum(lane_queue_counts.get(lane_id, 0) for lane_id in phase_lane_ids)
            
            # 简化：用排队数作为流量的近似指标
            # 流量比 = 需求 / 容量
            # 这里我们用排队数归一化（避免除以0）
            flow_ratios.append(total_queue)
        
        return flow_ratios
    
    def calculate_green_splits(
        self,
        flow_ratios: List[float]
    ) -> List[float]:
        """
        根据流量比计算各相位的绿灯时长
        
        Webster简化公式:
        g_i = (y_i / ΣY) * (C - n*L)
        
        Args:
            flow_ratios: 各相位的流量比
        
        Returns:
            各相位的绿灯时长（秒）
        """
        num_phases = len(flow_ratios)
        
        if num_phases == 0:
            return []
        
        # 计算有效绿灯时间（周期 - 各相位损失时间）
        total_loss_time = num_phases * self.loss_time_per_phase
        effective_green_time = max(self.cycle_seconds - total_loss_time, num_phases * self.min_green_seconds)
        
        # 总流量比
        total_flow = sum(flow_ratios)
        
        if total_flow == 0:
            # 如果没有流量，平均分配
            durations = [effective_green_time / num_phases] * num_phases
        else:
            # 按流量比分配绿灯时间
            durations = [(ratio / total_flow) * effective_green_time for ratio in flow_ratios]
        
        # 应用最小绿灯约束
        for i in range(num_phases):
            if durations[i] < self.min_green_seconds:
                durations[i] = self.min_green_seconds
        
        # 重新归一化到周期时长
        total = sum(durations)
        if total != self.cycle_seconds:
            scale = self.cycle_seconds / total
            durations = [d * scale for d in durations]
        
        # 四舍五入
        durations = [round(d, 1) for d in durations]
        
        # 修正误差
        diff = self.cycle_seconds - sum(durations)
        if diff != 0:
            max_idx = int(np.argmax(durations))
            durations[max_idx] += diff
        
        return durations
    
    def calculate_timing_from_queues(
        self,
        phase_lanes: List[List[int]],
        lane_queue_counts: Dict[int, int]
    ) -> List[float]:
        """
        从排队数直接计算配时方案
        
        Args:
            phase_lanes: 各相位的车道列表
            lane_queue_counts: 车道排队数
        
        Returns:
            各相位的时长（秒）
        """
        flow_ratios = self.calculate_phase_flow_ratios(phase_lanes, lane_queue_counts)
        durations = self.calculate_green_splits(flow_ratios)
        
        self.logger.debug(f"Webster配时: 流量比={flow_ratios}, 时长={durations}")
        
        return durations


async def calculate_webster_timing_for_all_junctions(
    sim_runner,
    junction_ids: List[int],
    cycle_seconds: float,
    min_green_seconds: float = 5.0,
    sample_steps: int = 1
) -> Dict[int, List[float]]:
    """
    为所有路口计算Webster配时
    
    Args:
        sim_runner: 仿真运行器
        junction_ids: 路口ID列表
        cycle_seconds: 周期时长
        min_green_seconds: 最小绿灯时间
        sample_steps: 采样步数（采样多次取平均）
    
    Returns:
        {junction_id: [duration0, duration1, ...]}
    """
    logger = logging.getLogger(__name__)
    calculator = WebsterTimingCalculator(cycle_seconds, min_green_seconds)
    
    # 获取相位-车道映射
    map_data = sim_runner.get_map_data()
    phase_lanes = sim_runner.get_junction_phase_lanes(junction_ids)
    
    # 构建lane_index到lane_id的映射
    lane_index_to_id = {i: lane.id for i, lane in enumerate(map_data.lanes)}
    
    # 构建每个路口的相位-车道ID映射
    junction_phase_lane_ids = {}
    selected_junctions = [j for j in map_data.junctions if j.id in junction_ids]
    
    for junction, j_phase_lanes in zip(selected_junctions, phase_lanes):
        phase_lane_ids = []
        for in_lane_indices, _out_lane_indices in j_phase_lanes:
            lane_ids = [
                lane_index_to_id[idx]
                for idx in in_lane_indices
                if idx in lane_index_to_id
            ]
            phase_lane_ids.append(lane_ids)
        junction_phase_lane_ids[junction.id] = phase_lane_ids
    
    # 采样多次，取平均排队数
    logger.info(f"开始采样 {sample_steps} 次以计算Webster配时...")
    
    accumulated_queues = {}
    for step in range(sample_steps):
        lane_queues = await sim_runner.get_lane_queue_counts(None)
        
        # 累积
        for lane_id, count in lane_queues.items():
            if lane_id not in accumulated_queues:
                accumulated_queues[lane_id] = 0
            accumulated_queues[lane_id] += count
        
        sim_runner.step(1)
    
    # 计算平均
    avg_queues = {lane_id: count / sample_steps for lane_id, count in accumulated_queues.items()}
    
    # 为每个路口计算Webster配时
    webster_timings = {}
    for junction_id in junction_ids:
        if junction_id not in junction_phase_lane_ids:
            continue
        
        phase_lanes_ids = junction_phase_lane_ids[junction_id]
        if not phase_lanes_ids or all(len(lanes) == 0 for lanes in phase_lanes_ids):
            continue
        
        durations = calculator.calculate_timing_from_queues(phase_lanes_ids, avg_queues)
        webster_timings[junction_id] = durations
        
        logger.info(f"路口 {junction_id}: Webster配时 = {[f'{d:.1f}' for d in durations]}s")
    
    logger.info(f"Webster配时计算完成，共 {len(webster_timings)} 个路口")
    
    return webster_timings
