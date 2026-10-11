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

def get_random_snowplow_path(net_path = 'Shaped.net.xml', clean_speed = 3, pass_speed = 10, route_save_path = 'plowing_path/cleaned_roads_by_interval_random.json', record_window = 1800, seed = None):

    rng = random.Random(seed)

    net_file_path = net_path
    tree = ET.parse(net_file_path)
    root = tree.getroot()

    edges_info = {}
    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if edge_id.startswith(':'):
            continue
        shape_attr = edge.get('shape')
        if shape_attr is None:
            lanes = edge.findall('lane')
            if lanes:
                shape_attr = lanes[0].get('shape')
        if shape_attr:
            coords = [tuple(map(float, point.split(','))) for point in shape_attr.split()]
            center_x = sum(coord[0] for coord in coords) / len(coords)
            center_y = sum(coord[1] for coord in coords) / len(coords)
            center_point = (center_x, center_y)
            edges_info[edge_id] = {
                'coords': coords,
                'center': center_point,
            }

    def euclid_dist(a, b):
        return math.hypot(a[0]-b[0], a[1]-b[1])

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
        }
        node_edges_dict.setdefault(u, []).append(eid)


    # # a greedy algorithm; each truck starts with the start point of start edge and choose the direction with the highest traffic flow at each intersection
    car_states = {}
    no_path_case = 0
    for car_id in range(4):
        car_states[car_id] = {
            "current_node": edge_data_dict['200000550']["from"],
            "cleaned_edges": set(),
            "cleaning_paths": [],
            "cleaned_length": 0,
            "penalty": 0
        }
    path_num = 0
    res_dict = {}
    cleaned_edges = set()
    Finished = False
    temp_time = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

    # 让所有车完整地清理道路（不受时间限制）
    while not Finished:
        sub_G = G
        Finished = True
        for car in car_states.keys():
            current_node = car_states[car]["current_node"]
            successors = []
            uncleaned_successors = []
            for kk, vv, d in sub_G.out_edges(current_node, data=True):
                if d["id"] not in cleaned_edges:
                    uncleaned_successors.append((kk, vv, d))
                successors.append((kk, vv, d))
            if len(uncleaned_successors) > 0:
                Finished = False
                rng.shuffle(uncleaned_successors)
                this_step_start_node, next_node, chosen_edge = uncleaned_successors[0]
                if chosen_edge['lane_count'] % 2 == 0:
                    this_step_next_node = this_step_start_node
                else:
                    this_step_next_node = next_node
                cleaned_edges.add(chosen_edge["id"])
                car_states[car]["cleaned_edges"].add(chosen_edge["id"])
                edge_len = edge_data_dict[chosen_edge["id"]]["single_length"]
                car_states[car]["cleaned_length"] += edge_len * edge_data_dict[chosen_edge["id"]]["lane_count"]
                car_states[car]["cleaning_paths"].append(chosen_edge["id"])
                car_states[car]["current_node"] = this_step_next_node
                temp_time[car] += edge_len / 3  # 清扫速度3 m/s
            else:
                candidates = [eid for eid in edge_data_dict.keys() if eid in edge_data_dict and eid not in cleaned_edges]
                if len(candidates) == 0:
                    pass
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
                                    Finished = False
                                    cleaned_edges.add(eid)
                                    car_states[car]["cleaned_edges"].add(eid)
                                    edge_len = edge_data_dict[eid]["single_length"]
                                    car_states[car]["cleaned_length"] += edge_len * edge_data_dict[eid]["lane_count"]
                                    car_states[car]["cleaning_paths"].append(eid)
                                    if edge_data_dict[eid]["lane_count"] % 2 == 0:
                                        this_step_next_node = u
                                    else:
                                        this_step_next_node = v
                                    car_states[car]["current_node"] = this_step_next_node
                                    temp_time[car] += edge_len / 3  # 清扫速度3 m/s
                                    break
                            car_states[car]["current_node"] = nid
                            path_num += 1
                            temp_time[car] += temp_route_length / 10  # 路过速度10 m/s
                            break
                        except:
                            # print("no paths")
                            continue
                    if not path_found:
                        no_path_case += 1
                        car_states[car]["current_node"] = candidate_node_list[0]
                        car_states[car]['cleaning_paths'].append("penalty")

    # 转换成cleaned_roads_by_interval的格式
    # 初始化每个区域的seq
    car_times = {}
    for car_id in car_states.keys():
        car_times[str(car_id)] = []

    # 重新计算，记录时间序列
    cleaned_edges = set()
    temp_time = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

    speed_clean = clean_speed
    speed_pass = pass_speed

    for car in car_states.keys():
        car_times[str(car)] = []
        cleaned_edges_car = set()
        
        for eid in car_states[car]["cleaning_paths"]:
            if eid == "penalty":
                t_add = 3000 / speed_clean
            else:
                if eid not in cleaned_edges_car:
                    t_add = edge_data_dict[eid]["length"] / speed_clean
                    cleaned_edges_car.add(eid)
                else:
                    t_add = edge_data_dict[eid]["length"] / speed_pass
            
            temp_time[car] += t_add
            if eid != "penalty":
                car_times[str(car)].append((eid, temp_time[car]))

    # 每隔1800秒记录清理的道路
    cleaned_roads_by_interval = {}
    for region, seq in car_times.items():
        for interval_num in range(1, 11):
            time_threshold = interval_num * record_window
            interval_key = f'interval_{interval_num}'
            
            if interval_key not in cleaned_roads_by_interval:
                cleaned_roads_by_interval[interval_key] = {}
            
            cleaned_roads = []
            for eid, cumulative_time in seq:
                if cumulative_time <= time_threshold:
                    cleaned_roads.append(eid)
            
            cleaned_roads_by_interval[interval_key][region] = list(dict.fromkeys(cleaned_roads))
            
            if time_threshold >= temp_time[int(region)]:
                break

    # 保存为JSON
    with open(route_save_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_roads_by_interval, f, indent=2, ensure_ascii=False)
        
def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="随机扫雪路径模拟器")
    parser.add_argument("--net_file", type=str, default="Shaped.net.xml", help="路网XML文件路径")
    parser.add_argument("--route_save_path", type=str, default="plowing_path/cleaned_roads_by_interval_random.json", help="输出JSON文件路径")
    parser.add_argument("--clean_speed", type=float, default=3.0, help="扫雪速度 (m/s)")
    parser.add_argument("--pass_speed", type=float, default=10.0, help="空车行驶速度 (m/s)")
    parser.add_argument("--record_window", type=int, default=1800, help="记录时间间隔 (秒)")
    parser.add_argument("--cars", type=int, default=4, help="扫雪车数量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    # 调用随机扫雪路径函数
    get_random_snowplow_path(
        net_path=args.net_file,
        clean_speed=args.clean_speed,
        pass_speed=args.pass_speed,
        route_save_path=args.route_save_path,
        record_window=args.record_window,
        seed=args.seed,
    )

    print(f"\n✅ 模拟完成，结果已保存到: {args.route_save_path}")

if __name__ == "__main__":
    main()