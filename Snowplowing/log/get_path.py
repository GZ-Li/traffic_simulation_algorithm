import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import math
import random
from collections import defaultdict
import networkx as nx
import heapq
import json
import datetime
from pycityproto.city.map.v2.map_pb2 import Map
from pycityproto.city.person.v2.person_pb2 import Persons, Person
from pymongo import MongoClient
import os

connection_url = "mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017"
od_db_name = "tsingroc"
od_coll_name = "person_test_20250724_1015cc"
od_output_name = "current_od_file.json"
map_db_name = "tsingroc"
map_coll_name = "map_pku_wuhan_demo_1015c_rl"
map_output_name = "current_map_file.json"
max_iter = 1000

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
    
def euclid_dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def get_snowplow_path(net_path = '/home/nkk/work/src/python/Snowplowing/Shaped.net.xml', clean_speed = 3, pass_speed = 10, route_save_path = 'plowing_path/cleaned_roads_by_interval.json', record_window = 1800):

    # 扫雪车位置初始化
    start_edges = {
        "0": "200000550",
        "1": "200000543",
        "2": "200000560",
        "3": "200000558"
    }

    # 扫雪路线初始化
    net_file_path = net_path
    tree = ET.parse(net_file_path)
    root = tree.getroot()
    region_paths = {
        "0": [],
        "1": [],
        "2": [],
        "3": [],
    }

    # 统计每个道路的历史流量作为决策依据
    traffic_flow = defaultdict(int)
    for ind_od in od_dict:
        for road_id in ind_od['data']['schedules'][0]['trips'][0]['routes'][0]['driving']['road_ids']:
            if road_id not in traffic_flow:
                traffic_flow[road_id] = 0
            traffic_flow[road_id] += 1
    # print(traffic_flow)

    # 将路网转换为图用于路径搜索
    all_nodes = set()
    G = nx.DiGraph()
    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if edge_id.startswith(':'):
            continue
        from_node = edge.get('from')
        to_node = edge.get('to')
        if from_node:
            all_nodes.add(from_node)
        if to_node:
            all_nodes.add(to_node)
    for node in all_nodes:
        G.add_node(node)
    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if edge_id.startswith(':'):
            continue
        from_node = edge.get('from')
        to_node = edge.get('to')
        if from_node and to_node:
            lanes = edge.findall('lane')
            edge_length = float(lanes[0].get('length'))
            lane_count = len(lanes) if lanes else 1
            G.add_edge(from_node, to_node, 
                            id=edge_id,
                            length = edge_length,
                            lane_count=lane_count,
                            total_length=edge_length*lane_count)
            
    node_positions = {} 
    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if edge_id.startswith(':'):
            continue
        lanes = edge.findall('lane')
        if lanes:
            shape_attr = lanes[0].get('shape')
        if shape_attr:
            coords = [tuple(map(float, p.split(','))) for p in shape_attr.split()]
            if len(coords) >= 2:
                from_coord = coords[0]
                to_coord = coords[-1]
                from_node = edge.get("from")
                to_node = edge.get("to")
                if from_node in G.nodes and from_node not in node_positions:
                    node_positions[from_node] = from_coord
                if to_node in G.nodes and to_node not in node_positions:
                    node_positions[to_node] = to_coord

    network_total_length = 0           
    for edge in G.edges(data=True):
        network_total_length += edge[2]['total_length']

    route_len_lst = []
    edge_data_dict = {}
    node_edges_dict = {} 
    for u, v, d in G.edges(data=True):
        eid = d["id"]
        edge_data_dict[eid] = {
            "from": u,
            "to": v,
            "single_length": float(d["length"]),
            "lane_count": d["lane_count"],
            "length": float(d["length"]) * d["lane_count"],
            "flow": traffic_flow.get(eid, 0),
            "region": None
        }
        node_edges_dict.setdefault(u, []).append(eid)
    
    # 读取每个区域包括哪些road    
    with open('/home/nkk/work/src/python/Snowplowing/util/region_roads_mapping.json', 'r') as f:
        region_edges = json.load(f)
    for region, edges in region_edges.items():
        for eid in edges:
            if eid in edge_data_dict:
                edge_data_dict[eid]["region"] = region

    # 车辆位置初始化
    car_states = {}
    for region, start_node in start_edges.items():
        car_states[region] = {
            "current_node": edge_data_dict[start_node]["from"],
            "cleaned_edges": set(),
            "cleaning_paths": [],
            "cleaned_length": 0,
            "penalty": 0
        }
    path_num = 0
    for region in region_edges.keys():
        cleaned_edges = set()
        state = car_states[region]
        sub_G = nx.DiGraph()
        for u, v, d in G.edges(data=True):
            if d["id"] in region_edges[region]:
                sub_G.add_edge(u, v, **d)
                if u in G.nodes:
                    sub_G.add_node(u, **G.nodes[u])
                if v in G.nodes:
                    sub_G.add_node(v, **G.nodes[v])
        for i in range(max_iter):
            current_node = state["current_node"]
            successors = []
            uncleaned_successors = []
            for kk, vv, d in sub_G.out_edges(current_node, data=True):
                if d["id"] not in cleaned_edges:
                    uncleaned_successors.append((kk, vv, d))
                successors.append((kk, vv, d))
            if len(uncleaned_successors) > 0:
                uncleaned_successors.sort(key=lambda x: traffic_flow[x[2]["id"]], reverse=True)
                this_step_start_node, next_node, chosen_edge = uncleaned_successors[0]
                if chosen_edge['lane_count'] % 2 == 0:
                    this_step_next_node = this_step_start_node
                else:
                    this_step_next_node = next_node
                cleaned_edges.add(chosen_edge["id"])
                state["cleaned_edges"].add(chosen_edge["id"])
                edge_len = edge_data_dict[chosen_edge["id"]]["length"]
                state["cleaned_length"] += edge_len
                state["cleaning_paths"].extend([chosen_edge["id"]]*edge_data_dict[chosen_edge["id"]]["lane_count"])
                state["current_node"] = this_step_next_node
            else:
                candidates = [eid for eid in region_edges[region] if eid in edge_data_dict and eid not in cleaned_edges]
                if len(candidates) == 0:
                    break
                else:
                    candidate_nodes = []
                    for eid in candidates:
                        ed = edge_data_dict[eid]
                        if ed["from"] != current_node:
                            candidate_nodes.append(ed["from"])
                    candidate_nodes = list(set(candidate_nodes))
                    
                    def node_min_dist(nid):
                        if nid in node_positions:
                            return euclid_dist(node_positions[current_node], node_positions[nid])
                        else:
                            return float("inf") 
                    
                    candidate_node_list = heapq.nsmallest(min(10, len(candidates)), candidate_nodes, key=node_min_dist)
                    path_found = False
                    for nid in candidate_node_list:
                        try:
                            path_nodes = nx.shortest_path(sub_G, source=current_node, target=nid, weight='length')
                            path_found = True
                            temp_route_length = 0
                            for j in range(len(path_nodes) - 1):
                                u, v = path_nodes[j], path_nodes[j + 1]
                                elen = sub_G[u][v]["length"]
                                temp_route_length += elen
                                eid = sub_G[u][v]["id"]
                                if eid not in cleaned_edges:
                                    route_len_lst.append(temp_route_length)
                                    cleaned_edges.add(eid)
                                    state["cleaned_edges"].add(eid)
                                    edge_len = edge_data_dict[eid]["length"]
                                    state["cleaned_length"] += edge_len
                                    state["cleaning_paths"].extend([eid] * edge_data_dict[eid]["lane_count"])
                                    if edge_data_dict[eid]["lane_count"] % 2 == 0:
                                        this_step_next_node = u
                                    else:
                                        this_step_next_node = v
                                    state["current_node"] = this_step_next_node
                                    break
                            state["current_node"] = nid
                            path_num += 1
                            route_len_lst.append(temp_route_length)
                            break
                        except:
                            # print("no paths")
                            continue
                    if not path_found:
                        state["current_node"] = candidate_node_list[0]
                        state['cleaning_paths'].append("penalty")
                        # print("Warning: No path found, using the closest node")
        region_paths[region] = state["cleaning_paths"]
    
    # print(len(region_edges[region]))
    # print(region_paths['0'])

    # 清扫时间计算与时间间隔划分
    speed_clean = clean_speed  # 清扫速度
    speed_pass = pass_speed   # 路过速度
    car_times = {}       # region -> list of (eid, cumulative_time)
    car_total_times = {} # region -> 总时间
    total_time = 0       # 所有车完成最长时间
    for region, path in region_paths.items():
        t = 0
        cleaned_set = set()
        seq = []
        for eid in path:
            if eid == "penalty":
                length = 3000
            else:
                length = edge_data_dict[eid]["single_length"]
            if eid not in cleaned_set:
                t += length / speed_clean
                cleaned_set.add(eid)
            else:
                t += length / speed_pass
            seq.append((eid, t))
        car_times[region] = seq
        car_total_times[region] = t
        total_time = max(total_time, t)  # 更新总时间

    cleaned_roads_by_interval = {}
    for region, seq in car_times.items():
        for interval_num in range(1, 11):
            time_threshold = interval_num * 1800
            interval_key = f'interval_{interval_num}'
            
            if interval_key not in cleaned_roads_by_interval:
                cleaned_roads_by_interval[interval_key] = {}
            
            cleaned_roads = []
            for eid, cumulative_time in seq:
                if eid != "penalty" and cumulative_time <= time_threshold:
                    cleaned_roads.append(eid)
            
            cleaned_roads_by_interval[interval_key][str(region)] = list(dict.fromkeys(cleaned_roads))
            
            if time_threshold >= car_total_times[region]:
                break

    # 保存为JSON
    with open(route_save_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_roads_by_interval, f, indent=2, ensure_ascii=False)
        

def main():
    import argparse

    parser = argparse.ArgumentParser(description="雪车路线规划模拟器")
    parser.add_argument("--net_file", type=str, default="/home/nkk/work/src/python/Snowplowing/Shaped.net.xml", help="路网XML文件路径")
    parser.add_argument("--od_mongo_uri", type=str, default=connection_url, help="MongoDB URI")
    parser.add_argument("--od_db", type=str, default=od_db_name, help="OD数据库名")
    parser.add_argument("--od_coll", type=str, default=od_coll_name, help="OD集合名")
    parser.add_argument("--region_edges_file", type=str, default="/home/nkk/work/src/python/Snowplowing/util/region_roads_mapping.json", help="区域道路映射JSON文件")
    parser.add_argument("--clean_speed", type=float, default=3.0, help="扫雪速度 (m/s)")
    parser.add_argument("--pass_speed", type=float, default=10.0, help="空车行驶速度 (m/s)")
    parser.add_argument("--route_save_path", type=str, default="/home/nkk/work/src/python/Snowplowing/plowing_path/cleaned_roads_by_interval_0422.json", help="输出路径")
    parser.add_argument("--max_iter", type=int, default=max_iter, help="每辆车的最大迭代次数")
    parser.add_argument("--record_window", type=int, default=1800, help="每辆车的最大迭代次数")
    args = parser.parse_args()

    # 导出OD数据
    od_docs = export_mongodb_to_json(
        connection_url=args.od_mongo_uri,
        database_name=args.od_db,
        collection_name=args.od_coll,
        output_filename=od_output_name
    )
    if not od_docs:
        print("⚠️ 没有获取到OD数据，退出")
        return

    # 运行雪车路径规划
    get_snowplow_path(
        net_path=args.net_file,
        clean_speed=args.clean_speed,
        pass_speed=args.pass_speed,
        route_save_path=args.route_save_path,
        record_window=args.record_window
    )

    print(f"\n✓ 模拟完成，结果保存到: {args.route_save_path}")

if __name__ == "__main__":
    main()
