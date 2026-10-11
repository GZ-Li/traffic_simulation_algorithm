#!/usr/bin/env python3
"""
可视化区域分布
"""

import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from typing import List, Tuple
import json

def parse_shape(shape_str: str) -> List[Tuple[float, float]]:
    """解析 shape 字符串成坐标点列表"""
    if not shape_str:
        return []
    points = []
    for point_str in shape_str.split():
        x, y = map(float, point_str.split(','))
        points.append((x, y))
    return points

def main():
    # 读取 XML 文件
    tree = ET.parse('/home/nkk/work/src/python/Snowplowing/Shaped.net.xml')
    root = tree.getroot()
    
    # 定义颜色（更深的颜色）
    colors = {
        0: '#D32F2F',  # 深红色 - 左下
        1: '#0097A7',  # 深青色 - 右下
        2: '#F57F17',  # 深橙色 - 左上
        3: '#00796B',  # 深绿色 - 右上
    }
    
    region_names = {
        0: 'Lower-Left',
        1: 'Lower-Right',
        2: 'Upper-Left',
        3: 'Upper-Right',
    }
    
    # 创建图表
    fig, ax = plt.subplots(figsize=(14, 10))
    
    # 获取所有 edge 元素
    edges = root.findall('.//edge')
    normal_edges = [e for e in edges if e.get('function') != 'internal' and e.get('region')]
    
    # 绘制每条道路
    for edge in normal_edges:
        shape_str = edge.get('shape', '')
        if shape_str:
            points = parse_shape(shape_str)
            if points:
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                region = int(edge.get('region', 0))
                ax.plot(xs, ys, color=colors[region], alpha=0.8, linewidth=1.0)
    
    # 添加分割线
    x_mid = 7463.60
    y_mid = 1825.74
    
    # 获取坐标范围
    all_x = []
    all_y = []
    for edge in normal_edges:
        shape_str = edge.get('shape', '')
        if shape_str:
            points = parse_shape(shape_str)
            if points:
                all_x.extend([p[0] for p in points])
                all_y.extend([p[1] for p in points])
    
    if all_x and all_y:
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        
        # 绘制分割线
        ax.axvline(x=x_mid, color='black', linestyle='--', linewidth=2, alpha=0.7, label='X分割线')
        ax.axhline(y=y_mid, color='black', linestyle='--', linewidth=2, alpha=0.7, label='Y分割线')
        
        # 设置坐标范围
        ax.set_xlim(min_x - 100, max_x + 100)
        ax.set_ylim(min_y - 100, max_y + 100)
    
    # 创建图例
    from matplotlib.patches import Patch
    # legend_elements = [
    #     Patch(facecolor=colors[0], label=f'({region_names[0]}) - 560条'),
    #     Patch(facecolor=colors[1], label=f'区域1 ({region_names[1]}) - 665条'),
    #     Patch(facecolor=colors[2], label=f'区域2 ({region_names[2]}) - 665条'),
    #     Patch(facecolor=colors[3], label=f'区域3 ({region_names[3]}) - 560条'),
    # ]
    # ax.legend(handles=legend_elements, loc='upper right', fontsize=10)
    
    # 设置标题和标签
    ax.set_title('Road Network - Region Division', fontsize=14, fontweight='bold')
    ax.set_xlabel('X Coordinate (meters)', fontsize=11)
    ax.set_ylabel('Y Coordinate (meters)', fontsize=11)
    ax.grid(True, alpha=0.3)
    
    # 保存图表
    output_file = '/home/nkk/work/src/python/Snowplowing/region_visualization.png'
    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"✓ 可视化已保存到: {output_file}")
    
    # 关闭图表
    plt.close()

if __name__ == '__main__':
    main()
