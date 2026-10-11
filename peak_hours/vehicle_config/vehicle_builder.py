"""
车辆集准备模块 - 只使用已有的MongoDB机制
通过操作MongoDB collection实现车辆数量调整
"""
import logging
import random
from typing import List, Optional
import pymongo


class VehicleSetBuilder:
    """
    车辆集构建器 - 在MongoDB层面做车辆下采样
    利用现有的"SimulatorConfig从Mongo读agent_coll"机制
    """

    def __init__(self, mongo_uri: str, agent_db: str, agent_coll_src: str):
        """
        Args:
            mongo_uri: MongoDB连接URI
            agent_db: Agent数据库名
            agent_coll_src: 源agent集合名
        """
        self.mongo_uri = mongo_uri
        self.agent_db = agent_db
        self.agent_coll_src = agent_coll_src
        self.logger = logging.getLogger(__name__)

    def build_downsampled_collection(
        self, 
        target_vehicle_count: int, 
        agent_coll_runtime: str,
        mode: str = "random",
        seed: Optional[int] = None
    ) -> List[int]:
        """
        构建下采样的车辆集合（写入新collection）
        
        Args:
            target_vehicle_count: 目标车辆数量
            agent_coll_runtime: 运行时collection名
            mode: 采样模式 ("random" | "first_k")
            seed: 随机种子
            
        Returns:
            选中的车辆ID列表
        """
        client = pymongo.MongoClient(self.mongo_uri)
        db = client[self.agent_db]
        src_coll = db[self.agent_coll_src]
        runtime_coll = db[agent_coll_runtime]
        
        try:
            # 获取源集合总数
            total_count = src_coll.count_documents({})
            self.logger.info(f"源集合 {self.agent_coll_src} 总车辆数: {total_count}")
            
            if target_vehicle_count > total_count:
                self.logger.warning(
                    f"目标车辆数 {target_vehicle_count} 超过源集合数量 {total_count}，"
                    f"将使用全部车辆"
                )
                target_vehicle_count = total_count
            
            # 先清空运行时集合
            runtime_coll.delete_many({})
            
            # 获取车辆ID列表
            all_docs = list(src_coll.find({}, {"_id": 1, "id": 1}))
            
            # 选择车辆
            if mode == "random":
                if seed is not None:
                    random.seed(seed)
                selected_docs = random.sample(all_docs, target_vehicle_count)
            elif mode == "first_k":
                selected_docs = all_docs[:target_vehicle_count]
            else:
                raise ValueError(f"不支持的采样模式: {mode}")
            
            # 获取完整文档并插入运行时集合
            selected_mongo_ids = [doc["_id"] for doc in selected_docs]
            full_docs = list(src_coll.find({"_id": {"$in": selected_mongo_ids}}))
            
            if full_docs:
                runtime_coll.insert_many(full_docs)
            
            # 提取车辆ID（用于日志/验证）
            vehicle_ids = [doc.get("id", doc.get("_id")) for doc in selected_docs]
            
            self.logger.info(
                f"成功创建运行时集合 {agent_coll_runtime}，"
                f"包含 {len(vehicle_ids)} 辆车辆"
            )
            
            return vehicle_ids
            
        finally:
            client.close()

    def cleanup_runtime_collection(self, agent_coll_runtime: str):
        """
        清理运行时集合
        
        Args:
            agent_coll_runtime: 运行时collection名
        """
        client = pymongo.MongoClient(self.mongo_uri)
        try:
            db = client[self.agent_db]
            db.drop_collection(agent_coll_runtime)
            self.logger.info(f"已清理运行时集合: {agent_coll_runtime}")
        finally:
            client.close()
