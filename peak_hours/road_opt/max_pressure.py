"""
MaxPressure信控算法 - 基于已有接口实现
参考mp_demo.py，但适配到当前仿真器接口
"""
import logging
from typing import Dict, List, Tuple, Optional


class MaxPressureController:
    """
    MaxPressure控制器 - 只使用已有接口
    基于车道排队数计算相位压力，选择最大压力相位
    """

    def __init__(
        self,
        sim_runner,
        junction_ids: List[int],
        cycle_seconds: float,
        min_green_seconds: float = 5.0,
        decision_interval: float = 1.0
    ):
        """
        Args:
            sim_runner: SimulationRunner实例
            junction_ids: 路口ID列表
            cycle_seconds: 信号灯周期（秒）
            min_green_seconds: 最小绿灯时间（秒）
            decision_interval: 决策间隔（秒）
        """
        self.sim_runner = sim_runner
        self.junction_ids = junction_ids
        self.cycle_seconds = cycle_seconds
        self.min_green_seconds = min_green_seconds
        self.decision_interval = decision_interval
        self.logger = logging.getLogger(__name__)
        
        # 从地图获取相位-车道映射
        self._init_phase_lane_map()
        
        # 当前相位与时间跟踪
        self.current_phases: Dict[int, int] = {}  # {junction_id: phase_index}
        self.current_phase_times: Dict[int, float] = {}  # {junction_id: time_in_phase}
        
        # 初始化为第0相位
        for jid in self.junction_ids:
            self.current_phases[jid] = 0
            self.current_phase_times[jid] = 0.0

    def _init_phase_lane_map(self):
        """从地图初始化相位-车道映射，并预计算每相位的进口lane_id列表。"""
        map_data = self.sim_runner.get_map_data()

        # 获取相位对应的进出车道索引（顺序与Engine内部遍历一致）
        phase_lanes = self.sim_runner.get_junction_phase_lanes(self.junction_ids)

        # 构建lane_index到lane_id的映射
        self.lane_index_to_id: Dict[int, int] = {
            i: lane.id for i, lane in enumerate(map_data.lanes)
        }

        # 还原 get_junction_phase_lanes 的路口遍历顺序，以保证索引对齐
        if self.junction_ids:
            selected_junctions = [j for j in map_data.junctions if j.id in self.junction_ids]
        else:
            selected_junctions = list(map_data.junctions)

        # 构建映射: {junction_id: [(in_lane_indices, out_lane_indices), ...]}
        self.phase_lane_map: Dict[int, List[Tuple[List[int], List[int]]]] = {}
        for junction, j_phase_lanes in zip(selected_junctions, phase_lanes):
            self.phase_lane_map[junction.id] = j_phase_lanes

        # 预计算：每个路口每个相位对应的“进口 lane_id 列表”
        # 这样 update() 可以一次拉全量排队数，然后本地 O(1) 聚合求和
        self.phase_in_lane_ids: Dict[int, List[List[int]]] = {}
        for jid, phases in self.phase_lane_map.items():
            phase_in_ids: List[List[int]] = []
            for in_lane_indices, _out_lane_indices in phases:
                phase_in_ids.append(
                    [
                        self.lane_index_to_id[idx]
                        for idx in in_lane_indices
                        if idx in self.lane_index_to_id
                    ]
                )
            self.phase_in_lane_ids[jid] = phase_in_ids

        self.logger.info(f"初始化完成，管理 {len(self.junction_ids)} 个路口")

    def compute_phase_pressures_from_queues(
        self, junction_id: int, lane_queues: Dict[int, int]
    ) -> List[float]:
        """
        用“已获取到的车道排队数”计算指定路口各相位压力（不再发RPC）。

        Args:
            junction_id: 路口ID
            lane_queues: {lane_id: queue_count}（通常为全量或覆盖相关lane的子集）

        Returns:
            各相位的压力值列表
        """
        phase_in_ids = self.phase_in_lane_ids.get(junction_id)
        if not phase_in_ids:
            return []

        # 压力 = 进口车道排队数之和
        return [sum(lane_queues.get(lane_id, 0) for lane_id in in_ids) for in_ids in phase_in_ids]

    def select_best_phase_from_queues(
        self, junction_id: int, lane_queues: Dict[int, int]
    ) -> int:
        """
        用缓存的 lane_queues 选择最佳相位（不再发RPC）。

        Args:
            junction_id: 路口ID
            lane_queues: {lane_id: queue_count}

        Returns:
            最佳相位索引
        """
        current_phase = self.current_phases[junction_id]
        current_time = self.current_phase_times[junction_id]
        
        # 检查最小绿灯时间约束
        if current_time < self.min_green_seconds:
            return current_phase

        # 计算各相位压力（本地）
        pressures = self.compute_phase_pressures_from_queues(junction_id, lane_queues)
        if not pressures:
            return current_phase
        
        # 选择压力最大的相位
        best_phase = int(pressures.index(max(pressures)))
        
        return best_phase

    async def update(self, dt: float):
        """
        更新所有路口的信号灯状态
        
        Args:
            dt: 时间步长（秒）
        """
        # 关键优化：每个仿真步只拉取一次车道排队数（全量）
        lane_queues = await self.sim_runner.get_lane_queue_counts(None)

        for junction_id in self.junction_ids:
            current_phase = self.current_phases[junction_id]

            # 选择最佳相位（基于缓存的 lane_queues）
            best_phase = self.select_best_phase_from_queues(junction_id, lane_queues)
            
            # 如果需要切换相位
            if best_phase != current_phase:
                # 设置新相位 - 使用已有接口
                await self.sim_runner.set_traffic_light_phase(
                    junction_id=junction_id,
                    phase_index=best_phase,
                    time_remaining=1e99  # 大值，表示不限制
                )
                
                self.current_phases[junction_id] = best_phase
                self.current_phase_times[junction_id] = 0.0
                
                self.logger.debug(f"路口 {junction_id}: 切换相位 {current_phase} -> {best_phase}")
            else:
                # 累积当前相位时间
                self.current_phase_times[junction_id] += dt

    def get_current_phase(self, junction_id: int) -> int:
        """获取路口当前相位"""
        return self.current_phases.get(junction_id, 0)

    def get_current_phase_time(self, junction_id: int) -> float:
        """获取路口当前相位持续时间"""
        return self.current_phase_times.get(junction_id, 0.0)
