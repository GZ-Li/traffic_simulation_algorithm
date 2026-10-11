#!/usr/bin/env python3
"""
将 Shaped.net.xml 中的所有道路分成四个子区域（按经纬度平均分）
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple
import re

def parse_shape(shape_str: str) -> List[Tuple[float, float]]:
    """解析 shape 字符串成坐标点列表"""
    if not shape_str:
        return []
    points = []
    for point_str in shape_str.split():
        x, y = map(float, point_str.split(','))
        points.append((x, y))
    return points

def get_edge_bounds(edge_elem) -> Tuple[float, float, float, float]:
    """获取 edge 的坐标范围（min_x, min_y, max_x, max_y）"""
    # 首先尝试从 edge 属性中获取 shape
    shape_str = edge_elem.get('shape', '')
    
    all_points = []
    if shape_str:
        all_points.extend(parse_shape(shape_str))
    
    # 如果没有找到，尝试从 lane 子元素中获取
    if not all_points:
        lanes = edge_elem.findall('lane')
        for lane in lanes:
            lane_shape = lane.get('shape', '')
            if lane_shape:
                all_points.extend(parse_shape(lane_shape))
                break  # 只需要一条车道的坐标就可以了
    
    if not all_points:
        return None
    
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    
    return min(xs), min(ys), max(xs), max(ys)

def get_region(bounds: Tuple[float, float, float, float], 
               x_mid: float, y_mid: float) -> int:
    """根据边界确定所属区域（0-3）
    区域划分：
    0: 左下 (x < x_mid, y < y_mid)
    1: 右下 (x >= x_mid, y < y_mid)
    2: 左上 (x < x_mid, y >= y_mid)
    3: 右上 (x >= x_mid, y >= y_mid)
    """
    min_x, min_y, max_x, max_y = bounds
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    
    if center_x < x_mid and center_y < y_mid:
        return 0
    elif center_x >= x_mid and center_y < y_mid:
        return 1
    elif center_x < x_mid and center_y >= y_mid:
        return 2
    else:
        return 3

def main():
    # 读取 XML 文件
    tree = ET.parse('/home/nkk/work/src/python/Snowplowing/Shaped.net.xml')
    root = tree.getroot()
    
    # 获取所有 edge 元素
    edges = root.findall('.//edge')
    
    # 过滤出非 internal 的 edges
    normal_edges = [e for e in edges if e.get('function') != 'internal']
    
    print(f"总共找到 {len(edges)} 条边，其中 {len(normal_edges)} 条是正常的道路")
    
    # 计算所有道路的坐标范围和中心点
    all_bounds = []
    valid_edges = []
    edge_centers = []
    
    for edge in normal_edges:
        bounds = get_edge_bounds(edge)
        if bounds:
            all_bounds.append(bounds)
            valid_edges.append((edge, bounds))
            # 计算边的中心点
            center_x = (bounds[0] + bounds[2]) / 2
            center_y = (bounds[1] + bounds[3]) / 2
            edge_centers.append((center_x, center_y))
    
    if not all_bounds:
        print("错误：未找到任何有效的道路坐标")
        return
    
    # 找到全局的坐标范围
    min_x_global = min(b[0] for b in all_bounds)
    min_y_global = min(b[1] for b in all_bounds)
    max_x_global = max(b[2] for b in all_bounds)
    max_y_global = max(b[3] for b in all_bounds)
    
    print(f"\n全局坐标范围:")
    print(f"  X: [{min_x_global:.2f}, {max_x_global:.2f}]")
    print(f"  Y: [{min_y_global:.2f}, {max_y_global:.2f}]")
    
    # 使用中位数而不是平均值来分割，保证每个方向上道路数量均匀
    x_coords = sorted([c[0] for c in edge_centers])
    y_coords = sorted([c[1] for c in edge_centers])
    
    # 取中位数
    x_mid = x_coords[len(x_coords) // 2]
    y_mid = y_coords[len(y_coords) // 2]
    
    print(f"\n区域划分线（按中位数）:")
    print(f"  X分割线: {x_mid:.2f}")
    print(f"  Y分割线: {y_mid:.2f}")
    
    # 给每条道路分配区域标签
    region_counts = [0, 0, 0, 0]
    
    for edge, bounds in valid_edges:
        region = get_region(bounds, x_mid, y_mid)
        edge.set('region', str(region))
        region_counts[region] += 1
    
    # 打印统计信息
    print(f"\n区域分布:")
    print(f"  区域0 (左下): {region_counts[0]} 条道路")
    print(f"  区域1 (右下): {region_counts[1]} 条道路")
    print(f"  区域2 (左上): {region_counts[2]} 条道路")
    print(f"  区域3 (右上): {region_counts[3]} 条道路")
    print(f"  总计: {sum(region_counts)} 条道路")
    
    # 计算分布均匀程度
    avg_per_region = sum(region_counts) / 4
    variance = sum((count - avg_per_region) ** 2 for count in region_counts) / 4
    std_dev = variance ** 0.5
    print(f"\n分布均匀性:")
    print(f"  平均每个区域: {avg_per_region:.1f} 条道路")
    print(f"  标准差: {std_dev:.1f}")
    
    # 保存修改后的 XML
    output_file = '/home/nkk/work/src/python/Snowplowing/Shaped.net.xml'
    tree.write(output_file, encoding='UTF-8', xml_declaration=True)
    
    print(f"\n✓ 已成功修改文件: {output_file}")
    print("  所有正常道路已添加 'region' 属性（均匀分布）")

if __name__ == '__main__':
    main()
