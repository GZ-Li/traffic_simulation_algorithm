#!/usr/bin/env python3
"""
提取区域与道路ID的映射关系，并找出每个区域最近的道路
"""

import xml.etree.ElementTree as ET
import json
from collections import defaultdict
from typing import List, Tuple

def parse_shape(shape_str: str) -> List[Tuple[float, float]]:
    """解析 shape 字符串成坐标点列表"""
    if not shape_str:
        return []
    points = []
    for point_str in shape_str.split():
        x, y = map(float, point_str.split(','))
        points.append((x, y))
    return points

def get_edge_center(shape_str: str) -> Tuple[float, float]:
    """获取edge的中心点"""
    points = parse_shape(shape_str)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))

def distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """计算两点间距离"""
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

def main():
    # 读取 XML 文件
    tree = ET.parse('/home/nkk/work/src/python/Snowplowing/Shaped.net.xml')
    root = tree.getroot()
    
    # 获取所有 edge 元素
    edges = root.findall('.//edge')
    
    # 按区域分组道路
    region_roads = defaultdict(list)
    region_closest_road = {}
    
    # 中点坐标
    x_mid = 7463.60
    y_mid = 1825.74
    center_point = (x_mid, y_mid)
    
    for edge in edges:
        region = edge.get('region')
        if region is not None:  # 只处理有region属性的道路
            edge_id = edge.get('id')
            region_roads[int(region)].append(edge_id)
    
    # 找每个区域最近的道路
    for edge in edges:
        region = edge.get('region')
        if region is not None:
            region = int(region)
            edge_id = edge.get('id')
            shape_str = edge.get('shape', '')
            if shape_str:
                edge_center = get_edge_center(shape_str)
                if edge_center:
                    dist = distance(edge_center, center_point)
                    
                    if region not in region_closest_road:
                        region_closest_road[region] = {
                            'road_id': edge_id,
                            'distance': dist,
                            'center': edge_center
                        }
                    else:
                        if dist < region_closest_road[region]['distance']:
                            region_closest_road[region] = {
                                'road_id': edge_id,
                                'distance': dist,
                                'center': edge_center
                            }
    
    # 转换为普通字典
    region_roads_dict = {k: v for k, v in sorted(region_roads.items())}
    
    # 打印统计
    print("========== 区域与道路ID映射 ==========\n")
    for region, roads in sorted(region_roads_dict.items()):
        print(f"区域 {region}: {len(roads)} 条道路")
    
    # 打印最近的道路
    print("\n========== 每个区域距离中点(7463.60, 1825.74)最近的道路 ==========\n")
    for region in sorted(region_closest_road.keys()):
        info = region_closest_road[region]
        print(f"区域 {region}: road_id = '{info['road_id']}', 距离 = {info['distance']:.2f}m, 中心点 = ({info['center'][0]:.2f}, {info['center'][1]:.2f})")
    
    # 保存映射关系为 JSON
    output_file = '/home/nkk/work/src/python/Snowplowing/region_roads_mapping.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(region_roads_dict, f, indent=2, ensure_ascii=False)
    print(f"\n✓ 区域-道路映射已保存到: {output_file}")
    
    # 保存最近的道路为 JSON
    closest_roads_dict = {
        str(region): {
            'road_id': info['road_id'],
            'distance': round(info['distance'], 2),
            'center': [round(info['center'][0], 2), round(info['center'][1], 2)]
        }
        for region, info in region_closest_road.items()
    }
    output_file2 = '/home/nkk/work/src/python/Snowplowing/region_closest_roads.json'
    with open(output_file2, 'w', encoding='utf-8') as f:
        json.dump(closest_roads_dict, f, indent=2, ensure_ascii=False)
    print(f"✓ 最近的道路信息已保存到: {output_file2}")
    
    return region_roads_dict, closest_roads_dict

if __name__ == '__main__':
    region_roads_dict, closest_roads_dict = main()
