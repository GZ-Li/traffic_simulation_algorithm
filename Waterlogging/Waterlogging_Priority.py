import json
from collections import defaultdict
# from pycityproto.city.map.v2.map_pb2 import Map
# from pycityproto.city.person.v2.person_pb2 import Persons, Person

from pymongo import MongoClient
import json
from datetime import datetime
import os

connection_url = "mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017"
od_db_name = "tsingroc"
od_coll_name = "person_test_20250724_1015cc"
od_output_name = "current_od_file.json"
map_db_name = "tsingroc"
map_coll_name = "map_pku_wuhan_demo_1015c_rl"
map_output_name = "current_map_file.json"

with open('waterlogging_points.json', 'r', encoding='utf-8') as f:
    Waterlogging_Points = json.load(f)

def export_mongodb_to_json(connection_url, database_name, collection_name, output_filename=None):
    """
    从MongoDB导出集合中的所有记录到JSON文件
    
    Args:
        connection_url (str): MongoDB连接URL
        database_name (str): 数据库名
        collection_name (str): 集合名
        output_filename (str, optional): 输出文件名，如果不指定则自动生成
    
    Returns:
        str: 输出文件的路径
    """
    
    try:
        print("🔗 正在连接到远程MongoDB...")
        print(f"连接URL: {connection_url}")
        
        # 连接到MongoDB
        client = MongoClient(connection_url)
        
        # 测试连接
        client.admin.command('ping')
        print("✅ 成功连接到远程MongoDB!")
        
        # 选择数据库和集合
        db = client[database_name]
        collection = db[collection_name]
        
        # 查询集合中的文档数量
        doc_count = collection.count_documents({})
        print(f"\n📊 集合 '{collection_name}' 中共有 {doc_count} 条记录")
        
        if doc_count == 0:
            print(f"⚠️  集合 '{collection_name}' 中没有数据")
            return None
        
        # 获取所有文档
        print("📥 正在导出所有记录...")
        documents = list(collection.find())
        
        # 生成输出文件名
        if output_filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{database_name}.{collection_name}_{timestamp}.json"
        
        # 确保输出文件有.json扩展名
        if not output_filename.endswith('.json'):
            output_filename += '.json'
        
        # 保存到JSON文件
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(documents, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"✅ 成功导出 {doc_count} 条记录到文件: {output_filename}")
        print(f"📁 文件大小: {os.path.getsize(output_filename) / 1024 / 1024:.2f} MB")
        
        return documents
        
    except Exception as e:
        print(f"❌ 导出失败: {e}")
        return None
        
    finally:
        # 关闭连接
        if client:
            client.close()
            print("🔒 连接已关闭")

od_dict = export_mongodb_to_json(
    connection_url=connection_url,
    database_name=od_db_name,
    collection_name=od_coll_name,
    output_filename=od_output_name
)

map_dict = export_mongodb_to_json(
    connection_url=connection_url,
    database_name=map_db_name,
    collection_name=map_coll_name,
    output_filename=map_output_name
)

def get_strategy(wlp, od_file):
    res_dict = {}
    for k, v in Waterlogging_Points.items():
        temp_lst = []
        for road_id in v:
            for ind_od in od_dict:
                # print(ind_od['data']['schedules'][0]['trips'][0]['routes'][0]['driving']['road_ids'])
                if int(road_id) in ind_od['data']['schedules'][0]['trips'][0]['routes'][0]['driving']['road_ids']:
                    temp_lst.append(ind_od['_id'])
        res_dict[k] = len(temp_lst)
    sorted_res_dict = {k: res_dict[k] for k in sorted(res_dict, reverse=True)}
    return res_dict

waterlogging_strategy = get_strategy(Waterlogging_Points, od_dict)
# print(waterlogging_strategy)
with open('waterlogging_strategy.json', 'w', encoding='utf-8') as f:
    json.dump(waterlogging_strategy, f)