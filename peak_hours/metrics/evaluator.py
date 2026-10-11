"""
指标计算模块 - 只使用已有接口
统一计算三类算法的评估指标
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any
import numpy as np


class MetricsEvaluator:
    """
    指标评估器 - 按确认的口径计算指标
    - 平均车速：RoadService.GetRoad -> avg_v
    - 平均通行时间：PersonService.GetGlobalStatistics -> completed_avg_travel_time
    - 平均等待队列（拥堵指数）：LaneService.GetLane -> total_queuing_vehicle_cnt

    兼容性约束：
    - 保持既有接口不变：sample_metrics(lane_ids=None) / compute_final_metrics() / reset()
    - 保持既有核心字段不变：avg_speed / avg_travel_time / avg_queue
    - 新增统计字段只追加，不覆盖旧字段语义
    """

    def __init__(self, sim_runner):
        """
        Args:
            sim_runner: SimulationRunner实例
        """
        self.sim_runner = sim_runner
        self.logger = logging.getLogger(__name__)
        
        # 用于累积采样
        self.speed_samples = []
        self.queue_samples = []
        self.last_global_stats: Dict[str, Any] = {}

    @staticmethod
    def _compute_att_metrics(stats: Dict[str, Any]) -> Dict[str, float]:
        completed_avg = float(stats.get("completed_avg_travel_time", 0.0) or 0.0)
        num_completed = int(stats.get("num_completed_trips", 0) or 0)
        num_running = int(stats.get("num_vehicles", 0) or 0)
        running_total = float(stats.get("running_total_travel_time", 0.0) or 0.0)
        departed = num_completed + num_running
        att_departed = running_total / departed if departed > 0 else 0.0
        return {
            "avg_travel_time": completed_avg,
            "att_finished": completed_avg,
            "att_departed": att_departed,
            "throughput": float(num_completed),
        }

    async def sample_metrics(self, lane_ids: Optional[List[int]] = None):
        """
        采样一次当前指标（用于时间平均）
        
        Args:
            lane_ids: 关注的车道ID列表，None表示全部
        """
        try:
            road_speeds = await self.sim_runner.get_road_avg_speeds()
            if road_speeds:
                avg_speed = np.mean(list(road_speeds.values()))
                self.speed_samples.append(avg_speed)
        except Exception as e:
            self.logger.warning(f"采样道路平均速度失败: {e}")
        
        try:
            lane_queues = await self.sim_runner.get_lane_queue_counts(lane_ids)
            if lane_queues:
                avg_queue = np.mean(list(lane_queues.values()))
                self.queue_samples.append(avg_queue)
        except Exception as e:
            self.logger.warning(f"采样车道排队失败: {e}")

        try:
            stats = await self.sim_runner.get_global_statistics()
            if isinstance(stats, dict):
                self.last_global_stats = stats
        except Exception as e:
            self.logger.warning(f"采样全局统计失败: {e}")

    async def compute_final_metrics(self) -> Dict[str, float]:
        """
        计算最终指标
        
        Returns:
            {
                "avg_speed": 平均车速 (m/s),
                "avg_travel_time": 平均通行时间 (s),
                "avg_queue": 平均等待队列（拥堵指数）
            }

        说明：
        - 为兼容旧调用方，上述三个核心字段始终保留
        - 其他字段如 att_finished / att_departed / throughput / queue_p95 / queue_max
          仅作为附加输出追加
        """
        metrics = {}
        
        # 1. 平均车速（时间平均）
        if self.speed_samples:
            metrics["avg_speed"] = float(np.mean(self.speed_samples))
        else:
            metrics["avg_speed"] = 0.0
        
        # 2. 平均通行时间
        stats: Dict[str, Any] = {}
        try:
            fresh_stats = await self.sim_runner.get_global_statistics()
            if isinstance(fresh_stats, dict):
                self.last_global_stats = fresh_stats
            stats = self.last_global_stats
        except Exception as e:
            self.logger.warning(f"获取全局统计失败: {e}")
            stats = self.last_global_stats

        if stats:
            metrics.update(self._compute_att_metrics(stats))
        else:
            metrics["avg_travel_time"] = 0.0
            metrics["att_finished"] = 0.0
            metrics["att_departed"] = 0.0
            metrics["throughput"] = 0.0
        
        # 3. 平均等待队列（拥堵指数，时间平均）
        if self.queue_samples:
            metrics["avg_queue"] = float(np.mean(self.queue_samples))
            metrics["queue_p95"] = float(np.percentile(self.queue_samples, 95))
            metrics["queue_max"] = float(np.max(self.queue_samples))
        else:
            metrics["avg_queue"] = 0.0
            metrics["queue_p95"] = 0.0
            metrics["queue_max"] = 0.0
        
        return metrics

    def reset(self):
        """重置采样累积"""
        self.speed_samples = []
        self.queue_samples = []
        self.last_global_stats = {}


class BusUtilizationEvaluator:
    """
    公交车利用率评估器
    利用率 = (公交车速度>阈值的时间) / (公交车总运行时间)
    """

    def __init__(self, sim_runner, bus_ids: List[int], speed_threshold: float):
        """
        Args:
            sim_runner: SimulationRunner实例
            bus_ids: 公交车ID列表
            speed_threshold: 速度阈值 (m/s)
        """
        self.sim_runner = sim_runner
        self.bus_ids = bus_ids
        self.speed_threshold = speed_threshold
        self.logger = logging.getLogger(__name__)
        
        # 累积统计
        self.time_above_threshold = 0.0
        self.total_time = 0.0
        # 记录“已启动”的公交（从SLEEP进入非SLEEP）
        self._started_bus_ids = set()

    @staticmethod
    def _is_sleep_status(status: Any) -> bool:
        """
        判断是否为“睡眠/未启动”状态。
        后端可能返回 int(enum) 或 string(enum name)。
        """
        if status is None:
            return True
        if isinstance(status, int):
            # 经验：示例里 STATUS_SLEEP 常见为 1
            return status == 1
        if isinstance(status, str):
            s = status.upper()
            return s == "STATUS_SLEEP" or s.endswith("_SLEEP")
        return False

    async def sample(self, dt: float):
        """
        采样一次公交车速度
        
        Args:
            dt: 时间步长（秒）
        """
        try:
            # 这里不能只拿速度：需要 motion.status 来判断“何时启动”
            if not self.sim_runner.engine:
                raise RuntimeError("仿真未启动")

            res = await self.sim_runner.engine.client.person_service.GetPersons(
                {"person_ids": self.bus_ids}
            )

            for person in res.get("persons", []):
                motion = person.get("motion", {})
                if not isinstance(motion, dict):
                    continue
                pid = motion.get("id")
                status = motion.get("status")
                v = motion.get("v", 0.0)

                if not isinstance(pid, int):
                    continue

                # 未启动（SLEEP）不计入分母，也不计入分子
                if self._is_sleep_status(status):
                    continue

                # 记录启动过（可用于调试/扩展）
                self._started_bus_ids.add(pid)

                # 启动后：累计总运行时间；超过阈值则累计分子
                self.total_time += dt
                if isinstance(v, (int, float)) and v > self.speed_threshold:
                    self.time_above_threshold += dt
                
        except Exception as e:
            self.logger.warning(f"采样公交速度失败: {e}")

    def compute_utilization(self) -> float:
        """
        计算公交利用率
        
        Returns:
            利用率 [0, 1]
        """
        if self.total_time > 0:
            return self.time_above_threshold / self.total_time
        return 0.0

    def get_metrics(self) -> Dict[str, float]:
        """
        获取完整指标
        
        Returns:
            {
                "bus_utilization": 公交利用率,
                "time_above_threshold": 超过阈值的时间,
                "total_time": 总运行时间
            }
        """
        return {
            "bus_utilization": self.compute_utilization(),
            "time_above_threshold": self.time_above_threshold,
            "total_time": self.total_time,
            "num_buses": len(self.bus_ids)
        }

    def reset(self):
        """重置统计"""
        self.time_above_threshold = 0.0
        self.total_time = 0.0
        self._started_bus_ids = set()
