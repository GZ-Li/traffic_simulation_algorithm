"""
仿真运行器封装 - 只使用已有接口
提供统一的启动、step推进、状态抓取能力
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any
from trafficlight_rl.engine import Engine, SimulatorConfig


class SimulationRunner:
    """
    仿真运行器 - 封装Engine的启动与基础操作
    只用已有接口，不新增仿真能力
    """

    def __init__(self, config: SimulatorConfig, log_dir: str = "./log"):
        """
        初始化仿真运行器
        
        Args:
            config: SimulatorConfig 配置对象
            log_dir: 日志目录
        """
        self.config = config
        self.log_dir = log_dir
        self.engine: Optional[Engine] = None
        self.logger = logging.getLogger(__name__)

    def start(self):
        """启动仿真引擎"""
        self.engine = Engine(self.config, self.log_dir)
        self.engine._start_simulator()
        self.logger.info("仿真引擎启动成功")

    def stop(self):
        """停止仿真引擎"""
        if self.engine:
            self.engine._stop_simulator()
            self.logger.info("仿真引擎停止")

    def step(self, step_size: int = 1):
        """
        推进仿真步
        
        Args:
            step_size: 推进的步数
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")
        self.engine.next_step(step_size)

    async def get_lane_states(
        self, lane_ids: Optional[List[int]] = None
    ) -> Dict[int, Dict[str, float]]:
        """
        获取车道状态快照。

        Args:
            lane_ids: 车道ID列表，None表示全部

        Returns:
            {
                lane_id: {
                    "vehicle_cnt": int,
                    "queue_cnt": int,
                }
            }
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        req = {"exclude_person": True}
        if lane_ids is not None:
            req["lane_ids"] = lane_ids

        res = await self.engine.client.lane_service.GetLane(req)

        result: Dict[int, Dict[str, float]] = {}
        for lane_state in res.get("states", []):
            lane_id = lane_state.get("id")
            if lane_id is None:
                continue
            result[lane_id] = {
                "vehicle_cnt": lane_state.get("vehicle_cnt", 0),
                "queue_cnt": lane_state.get("total_queuing_vehicle_cnt", 0),
            }
        return result

    async def get_lane_queue_counts(self, lane_ids: Optional[List[int]] = None) -> Dict[int, int]:
        """
        获取车道排队数 - 使用已有接口
        
        Args:
            lane_ids: 车道ID列表，None表示全部
            
        Returns:
            {lane_id: queue_count}
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")
        lane_states = await self.get_lane_states(lane_ids)
        return {
            lane_id: int(state.get("queue_cnt", 0))
            for lane_id, state in lane_states.items()
        }

    async def get_road_avg_speeds(self, road_ids: Optional[List[int]] = None) -> Dict[int, float]:
        """
        获取道路平均速度 - 使用已有接口
        
        Args:
            road_ids: 道路ID列表，None表示全部
            
        Returns:
            {road_id: avg_speed}
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        req = {"exclude_person": True, "exclude_lane": True}
        if road_ids:
            req["road_ids"] = road_ids
        else:
            req["road_ids"] = []

        res = await self.engine.client.road_service.GetRoad(req)
        
        result = {}
        for road_state in res["states"]:
            result[road_state["id"]] = road_state.get("avg_v", 0.0)
        
        return result

    async def get_global_statistics(self) -> Dict[str, Any]:
        """
        获取全局统计信息 - 使用已有接口
        
        Returns:
            全局统计字典
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        return await self.engine.client.person_service.GetGlobalStatistics({})

    async def get_person_speeds(self, person_ids: List[int]) -> Dict[int, float]:
        """
        获取指定人员（车辆）的速度 - 使用已有接口
        
        Args:
            person_ids: 人员ID列表
            
        Returns:
            {person_id: speed}
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        res = await self.engine.client.person_service.GetPersons({"person_ids": person_ids})
        result = {}
        for person in res.get("persons", []):
            # 从motion中获取person_id和速度
            motion = person.get("motion", {})
            if not motion:
                continue
            
            person_id = motion.get("id")
            if person_id is None:
                continue  # 跳过无法识别ID的person
            
            speed = motion.get("v", 0.0)
            result[person_id] = speed
        
        return result

    async def get_all_vehicle_ids(self) -> List[int]:
        """
        获取所有车辆ID - 使用已有接口

        Returns:
            车辆ID列表
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        res = await self.engine.client.person_service.GetAllVehicles({})
        vehicle_ids: List[int] = []
        for vehicle in res.get("vehicles", []):
            base = vehicle.get("base", {})
            if "id" in base:
                vehicle_ids.append(base["id"])
        return vehicle_ids

    async def set_traffic_light_phase(self, junction_id: int, phase_index: int, time_remaining: float = 1e99):
        """
        设置信号灯相位 - 使用已有接口
        
        Args:
            junction_id: 路口ID
            phase_index: 相位索引
            time_remaining: 剩余时间（秒）
        """
        if not self.engine:
            raise RuntimeError("仿真未启动")

        await self.engine.client.light_service.SetTrafficLightPhase({
            "junction_id": junction_id,
            "phase_index": phase_index,
            "time_remaining": time_remaining
        })

    def get_map_data(self):
        """获取地图数据"""
        if not self.engine:
            raise RuntimeError("仿真未启动")
        return self.engine.get_map()

    def get_junction_phase_lanes(self, junction_ids: List[int]):
        """获取路口相位对应的车道索引"""
        if not self.engine:
            raise RuntimeError("仿真未启动")
        return self.engine.get_junction_phase_lanes(junction_ids)

    def get_junction_inout_lanes(self, junction_ids: List[int]):
        """获取路口进出车道索引"""
        if not self.engine:
            raise RuntimeError("仿真未启动")
        return self.engine.get_junction_inout_lanes(junction_ids)

    def __enter__(self):
        """支持with语句"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持with语句"""
        self.stop()
