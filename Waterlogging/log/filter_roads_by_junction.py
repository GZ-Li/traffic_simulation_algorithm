#!/usr/bin/env python3

import xml.etree.ElementTree as ET
import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from Waterlogging_Location_Match_TBD import latlon2junction

# plt.rcParams['font.sans-serif'] = ['SimHei']  # 显示中文
# plt.rcParams['axes.unicode_minus'] = False    # 解决负号显示问题


# 读取sumo路网
def parse_sumo_net(net_file: str):
    tree = ET.parse(net_file)
    root = tree.getroot()
    
    junctions = {}
    for junc in root.findall('.//junction'):
        junc_id = junc.get('id')
        x = float(junc.get('x'))
        y = float(junc.get('y'))
        junctions[junc_id] = {'x': x, 'y': y, 'element': junc}
    
    edges = {}
    for edge in root.findall('.//edge[@id]'):
        edge_id = edge.get('id')
        if edge_id.startswith(':'):
            continue
        lanes = edge.findall('lane')
        if lanes:
            first_lane = lanes[0]
            shape = first_lane.get('shape', '')
            if shape:
                coords = [tuple(map(float, p.split(','))) for p in shape.split()]
                if coords:
                    mid_idx = len(coords) // 2
                    center = coords[mid_idx]
                    edges[edge_id] = {
                        'center': center,
                        'shape': coords,
                        'from': edge.get('from'),
                        'to': edge.get('to'),
                        'element': edge
                    }
    return junctions, edges

def distance(p1, p2):
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

# 假设涝点附近的路段会受到影响，此处通过阈值筛选受涝点影响的路段
def filter_roads_by_junction(net_file: str, junction_ids: list, distance_threshold: float = 500.0):
    junctions, edges = parse_sumo_net(net_file)
    target_junctions = {jid: junctions[jid] for jid in junction_ids if jid in junctions}
    nearby_roads = {}
    for edge_id, edge_info in edges.items():
        if 'center' not in edge_info:
            continue
        edge_center = edge_info['center']
        for jid, junc in target_junctions.items():
            junc_center = (junc['x'], junc['y'])
            dist = distance(junc_center, edge_center)
            if dist <= distance_threshold:
                if edge_id not in nearby_roads or dist < nearby_roads[edge_id]['nearest_distance']:
                    nearby_roads[edge_id] = {
                        'center': edge_center,
                        'from': edge_info['from'],
                        'to': edge_info['to'],
                        'nearest_junction': jid,
                        'nearest_distance': dist
                    }
    result_dict = {jid: [] for jid in target_junctions.keys()}
    for edge_id, info in nearby_roads.items():
        result_dict[info['nearest_junction']].append(edge_id)
    return result_dict

# 可视化受影响的路段
def visualize_roads(net_file: str, result_dict: dict, junction_ids: list, output_file: str = 'roads_visualization.png', show_all_roads: bool = False):
    junctions, edges = parse_sumo_net(net_file)
    matched_road_ids = set()
    for road_list in result_dict.values():
        matched_road_ids.update(road_list)
    
    target_junctions = {jid: junctions[jid] for jid in junction_ids if jid in junctions}
    
    if show_all_roads:
        roads_to_display = edges
        display_mode = "所有道路"
    else:
        display_range = 500
        roads_to_display = {}
        for edge_id in matched_road_ids:
            if edge_id in edges:
                roads_to_display[edge_id] = edges[edge_id]
        for edge_id, edge_info in edges.items():
            if edge_id in matched_road_ids or 'center' not in edge_info:
                continue
            for junc_info in target_junctions.values():
                junc_center = (junc_info['x'], junc_info['y'])
                if distance(edge_info['center'], junc_center) <= display_range:
                    roads_to_display[edge_id] = edge_info
                    break
        display_mode = f"周围道路（{display_range}m范围内）"
    
    fig, ax = plt.subplots(figsize=(14, 11), dpi=100)
    
    # 绘制道路
    for edge_id, edge_info in roads_to_display.items():
        if 'shape' not in edge_info:
            continue
        xs = [c[0] for c in edge_info['shape']]
        ys = [c[1] for c in edge_info['shape']]
        color = 'red' if edge_id in matched_road_ids else '#666666'
        lw = 2.5 if edge_id in matched_road_ids else 0.8
        ax.plot(xs, ys, color=color, linewidth=lw, alpha=0.9 if edge_id in matched_road_ids else 0.7, zorder=2 if edge_id in matched_road_ids else 1)
    
    # 绘制 junctions
    for junc_id, junc_info in target_junctions.items():
        x, y = junc_info['x'], junc_info['y']
        ax.scatter(x, y, color='blue', s=200, marker='o', zorder=3, edgecolors='darkblue', linewidth=2)
        circle = patches.Circle((x, y), 200, fill=False, edgecolor='blue', linestyle='--', linewidth=1, alpha=0.5, zorder=2)
        ax.add_patch(circle)
        # 黄色背景，但文字在点上方
        ax.text(x, y + 300, junc_id, fontsize=9, ha='center', fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='yellow', alpha=0.8), color='blue')
    
    ax.set_xlabel('X Coordinate (m)', fontsize=12)
    ax.set_ylabel('Y Coordinate (m)', fontsize=12)
    ax.set_title(f'Waterlogging Points Distribution', fontsize=13, fontweight='bold')
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color='red', linewidth=2.5, label=f'Matched Roads ({len(matched_road_ids)} total)'),
        Line2D([0], [0], color='#666666', linewidth=0.8, label=f'Other Roads ({len(roads_to_display) - len(matched_road_ids)} total)'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='blue', markersize=8, label='Target Junctions')
    ]
    ax.legend(handles=legend_elements, loc='upper left', fontsize=10)
    
    plt.tight_layout()
    plt.savefig(output_file, dpi=100, bbox_inches='tight')
    print(f"图形已保存到: {output_file}")
    plt.show()

def main():
    net_file = 'Shaped.net.xml'
    latlon = [(30.4746, 114.4639), (30.4864, 114.4638), (30.4879, 114.4808), (30.4882, 114.5344)]
    junction_ids = [latlon2junction(lat, lon) for lat, lon in latlon]
    result_dict = filter_roads_by_junction(net_file, junction_ids, distance_threshold=200.0)
    
    print("\n=== 最终结果 (junction: road_ids) ===")
    for junc_id, road_ids in result_dict.items():
        print(f"{junc_id}: {len(road_ids)} roads")
    
    visualize_roads(net_file, result_dict, junction_ids, output_file='roads_visualization.png', show_all_roads=True)
    return result_dict

if __name__ == '__main__':
    main()