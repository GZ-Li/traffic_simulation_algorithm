"""
从MongoDB读取10条数据并打印
基于runner.py的数据获取逻辑
"""
import json
import logging
from typing import List, Dict, Any, Optional

import pymongo


def fetch_data_from_mongo(
    mongo_uri: str, 
    agent_db: str, 
    agent_coll: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """从Mongo读取多条person配置"""
    logger = logging.getLogger(__name__)
    client = pymongo.MongoClient(mongo_uri)
    try:
        db = client[agent_db]
        coll = db[agent_coll]
        
        # 读取10条文档
        docs = list(coll.find({}, {"_id": 0}).limit(limit))
        
        if not docs:
            logger.warning(f"Mongo中未找到数据 (db={agent_db}, coll={agent_coll})")
            return []
        
        logger.info(f"成功读取 {len(docs)} 条文档")
        return docs
    
    except Exception as e:
        logger.error(f"读取MongoDB数据时出错: {e}")
        return []
    
    finally:
        client.close()


def print_data(docs: List[Dict[str, Any]], limit: int = 10):
    """打印数据"""
    print("\n" + "="*80)
    print(f"获取到 {len(docs)} 条数据")
    print("="*80 + "\n")
    
    for idx, doc in enumerate(docs[:limit], 1):
        print(f"--- 数据 {idx} ---")
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        print()


def save_data_to_json(docs: List[Dict[str, Any]], output_file: str):
    """将数据保存到JSON文件"""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2, ensure_ascii=False)
    print(f"✓ 数据已保存到: {output_file}")


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # MongoDB连接参数 (需要根据实际环境修改)
    # 这里使用的是runner.py中的参数
    mongo_uri = "mongodb://localhost:27017"  # 默认本地MongoDB
    agent_db = "agent_db"  # 需要替换为实际的数据库名
    agent_coll = "agent_collection"  # 需要替换为实际的集合名
    
    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║                    MongoDB 数据读取工具                                     ║
║                                                                            ║
║  说明：此脚本从runner.py中的MongoDB获取数据逻辑进行扩展                    ║
║       原始函数 load_person_template_from_mongo() 只读取1条                ║
║       此脚本改为读取10条数据进行展示                                       ║
╚════════════════════════════════════════════════════════════════════════════╝
    """)
    
    print(f"MongoDB连接参数:")
    print(f"  URI: {mongo_uri}")
    print(f"  Database: {agent_db}")
    print(f"  Collection: {agent_coll}")
    print()
    
    try:
        # 从MongoDB读取数据
        print("📡 正在连接MongoDB...")
        docs = fetch_data_from_mongo(mongo_uri, agent_db, agent_coll, limit=10)
        
        if docs:
            # 打印数据
            print_data(docs, limit=10)
            
            # 保存到JSON文件
            output_file = "/home/nkk/work/src/python/incremental_algos/concert_evacuation/mongo_data.json"
            save_data_to_json(docs, output_file)
            
            # 输出统计信息
            print("\n" + "="*80)
            print("📊 数据统计信息:")
            print("="*80)
            print(f"总数据条数: {len(docs)}")
            
            if docs and "data" in docs[0]:
                print(f"第一条数据中的data字段键: {list(docs[0]['data'].keys()) if isinstance(docs[0]['data'], dict) else '非字典类型'}")
            
            print()
        else:
            print("❌ 未获取到任何数据，请检查:")
            print("  1. MongoDB服务是否运行")
            print("  2. 数据库名称和集合名称是否正确")
            print("  3. MongoDB连接参数是否正确")
    
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        print("\n💡 提示:")
        print("  - 确保MongoDB正在运行")
        print("  - 修改脚本中的mongo_uri, agent_db, agent_coll参数")
        print("  - 参数值可以从runner.py的函数调用中找到")
