"""
公交车选择器 - 从车辆集中选择指定ID为公交车
"""
import logging
import random
from typing import List, Optional


class BusSelector:
    """
    公交车选择器 - 管理公交车ID列表
    """

    def __init__(self, bus_ids: List[int]):
        """
        Args:
            bus_ids: 公交车ID列表
        """
        self.bus_ids = list(bus_ids)
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"公交车数量: {len(self.bus_ids)}")

    def get_bus_ids(self) -> List[int]:
        """获取公交车ID列表"""
        return self.bus_ids.copy()

    def is_bus(self, vehicle_id: int) -> bool:
        """判断是否为公交车"""
        return vehicle_id in self.bus_ids

    @staticmethod
    def from_list(bus_ids: List[int]) -> "BusSelector":
        """
        从ID列表创建
        
        Args:
            bus_ids: 公交车ID列表
            
        Returns:
            BusSelector实例
        """
        return BusSelector(bus_ids)

    @staticmethod
    def select_random(
        all_vehicle_ids: List[int],
        bus_count: int,
        seed: Optional[int] = None
    ) -> "BusSelector":
        """
        随机选择指定数量的车辆作为公交车
        
        Args:
            all_vehicle_ids: 所有车辆ID列表
            bus_count: 公交车数量
            seed: 随机种子
            
        Returns:
            BusSelector实例
        """
        if seed is not None:
            random.seed(seed)
        
        bus_count = min(bus_count, len(all_vehicle_ids))
        bus_ids = random.sample(all_vehicle_ids, bus_count)
        
        return BusSelector(bus_ids)

    @staticmethod
    def select_smallest(all_vehicle_ids: List[int], bus_count: int) -> "BusSelector":
        """
        选择ID最小的若干车辆作为公交车
        
        Args:
            all_vehicle_ids: 所有车辆ID列表
            bus_count: 公交车数量
            
        Returns:
            BusSelector实例
        """
        sorted_ids = sorted(all_vehicle_ids)
        bus_ids = sorted_ids[:bus_count]
        
        return BusSelector(bus_ids)
    
    @staticmethod
    def select_by_ratio(
        all_vehicle_ids: List[int],
        bus_ratio: float,
        seed: Optional[int] = None
    ) -> "BusSelector":
        """
        按比例随机选择车辆作为公交车
        
        Args:
            all_vehicle_ids: 所有车辆ID列表
            bus_ratio: 公交车比例 (0.0-1.0)
            seed: 随机种子
            
        Returns:
            BusSelector实例
        """
        if seed is not None:
            random.seed(seed)
        
        bus_ratio = max(0.0, min(1.0, bus_ratio))  # 限制在[0, 1]
        bus_count = int(len(all_vehicle_ids) * bus_ratio)
        bus_count = max(1, bus_count)  # 至少1辆
        
        bus_ids = random.sample(all_vehicle_ids, bus_count)
        
        logger = logging.getLogger(__name__)
        logger.info(f"按比例 {bus_ratio:.2%} 从 {len(all_vehicle_ids)} 辆车中选择了 {bus_count} 辆公交车")
        
        return BusSelector(bus_ids)
