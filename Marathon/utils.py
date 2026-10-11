"""
OSM数据处理和绿化率计算工具模块
包含OSM地图数据获取、绿化率计算等功能
"""

import os
import osmnx as ox
import networkx as nx
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon
import matplotlib.pyplot as plt
from typing import List, Tuple, Dict, Optional, Any
import requests
import json

import xml.etree.ElementTree as ET
import pyproj
from shapely.geometry import Point


# ============================================================
# 道路类型过滤配置（OSM → NetworkX Graph 和 OSM → MOSS PB 统一使用）
# ============================================================
# 道路类型说明：
# - motorway/trunk: 高速公路/主干道
# - primary/secondary/tertiary: 主干道/次干道/支路
# - *_link: 各类道路的匝道/连接线
# - residential/service/unclassified: 住宅区道路/服务道路/未分类道路
HIGHWAY_FILTER = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    # "residential", "service", "unclassified",
]


def setup_matplotlib_for_plotting():
    """
    设置matplotlib绘图环境
    """
    import warnings
    import matplotlib.pyplot as plt
    import seaborn as sns

    # 确保警告被打印
    warnings.filterwarnings('default')

    # 配置matplotlib为非交互模式
    plt.switch_backend("Agg")

    # 设置图表样式
    plt.style.use("seaborn-v0_8")
    sns.set_palette("husl")

    # 配置跨平台字体兼容性
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "PingFang SC", "Arial Unicode MS", "Hiragino Sans GB"]
    plt.rcParams["axes.unicode_minus"] = False


class OSMDataProcessor:
    """OSM数据处理器"""
    
    def __init__(self):
        """初始化OSM数据处理器"""
        try:
            ox.config(use_cache=True, log_console=True)
        except AttributeError:
            # 新版本osmnx可能没有config方法
            pass

        os.environ['OVERPASS_ENDPOINT'] = "https://overpass.openstreetmap.cn/api/interpreter"
        
    def get_location_data(self, location_name: str) -> Tuple[float, float]:
        """
        获取地址名称对应的经纬度坐标

        Args:
            location_name: 地址名称，如"北京大学"

        Returns:
            (latitude, longitude): 经纬度坐标
        """
        # 使用OSM API获取地址信息
        base_url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': location_name,
            'format': 'json',
            'limit': 1
        }
        headers = {'User-Agent': 'RoutePlanner/1.0'}
        proxies = {
            'http': 'http://127.0.0.1:10190',
            'https': 'http://127.0.0.1:10190'
        }

        # 尝试使用代理，如果失败则直接连接
        try:
            response = requests.get(base_url, params=params, headers=headers, proxies=proxies, timeout=10)
        except Exception:
            response = requests.get(base_url, params=params, headers=headers, timeout=10)

        data = response.json()

        if data:
            lat = float(data[0]['lat'])
            lon = float(data[0]['lon'])
            return lat, lon
        else:
            raise ValueError(f"未找到地址: {location_name}")


    def get_drive_network_from_local(self, xml_filename: str, data_dir: str = "data") -> nx.MultiDiGraph:
        """
        从本地 XML 文件获取驾驶路网

        Args:
            xml_filename: XML 文件名（如 "beijing-latest.osm" 或 "THU-PKU.osm"）
            data_dir: 数据目录路径（默认 "data"）

        Returns:
            路网图
        """
        import os

        # 构建完整的文件路径
        file_path = os.path.join(data_dir, xml_filename)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"本地 XML 文件不存在: {file_path}")

        print(f"正在从本地 XML 文件读取路网数据...")
        print(f"  文件: {file_path}")

        # 检查文件大小
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        print(f"  大小: {file_size_mb:.2f} MB")

        # 从本地 XML 文件读取 OSM 数据并构建路网
        try:
            print(f"  正在解析 XML 文件...")

            # osmnx 2.0.7: graph_from_xml() 不支持 filter 参数
            # 先读取完整图，再按道路类型过滤
            G = ox.graph_from_xml(file_path)
            print(f"  原始图: {len(G.nodes)} 节点, {len(G.edges)} 边")

            # 按 highway 类型过滤边（使用统一配置 HIGHWAY_FILTER）
            edges_to_remove = []
            for u, v, data in G.edges(data=True):
                highway = data.get('highway', '')
                if highway not in HIGHWAY_FILTER:
                    edges_to_remove.append((u, v))

            G.remove_edges_from(edges_to_remove)
            # 移除孤立节点
            isolated_nodes = [n for n in G.nodes() if G.degree(n) == 0]
            G.remove_nodes_from(isolated_nodes)

            print(f"  过滤后: {len(G.nodes)} 节点, {len(G.edges)} 边")

            return G

        except AttributeError as e:
            if 'graph_from_xml' in str(e):
                raise RuntimeError(
                    f"osmnx 没有 graph_from_xml 方法\n"
                    f"错误信息: {e}\n\n"
                    f"可能的解决方案:\n"
                    f"  1. 更新 osmnx: pip install --upgrade osmnx\n"
                    f"  2. 使用在线方式: get_drive_network_from_bounds()"
                )
            else:
                raise
        except Exception as e:
            raise RuntimeError(
                f"读取 XML 文件失败: {e}\n\n"
                f"可能的解决方案:\n"
                f"  1. 检查 XML 文件格式是否正确\n"
                f"  2. 使用在线方式: get_drive_network_from_bounds()"
            )

    def get_drive_network_from_local_bounds(self, xml_filename: str,
                                           start_lat: float, start_lon: float,
                                           end_lat: float, end_lon: float,
                                           intermediate_coords: Optional[List[Tuple[float, float]]] = None,
                                           data_dir: str = "data",
                                           margin_km: float = 1.0) -> nx.MultiDiGraph:
        """
        从本地 XML 文件获取驾驶路网（通过边界框过滤）

        Args:
            xml_filename: XML 文件名
            start_lat: 起始纬度
            start_lon: 起始经度
            end_lat: 终止纬度
            end_lon: 终止经度
            intermediate_coords: 途经点坐标列表
            data_dir: 数据目录路径
            margin_km: 边界扩展距离（公里）

        Returns:
            过滤后的路网图
        """
        # 首先从本地文件读取完整路网
        G = self.get_drive_network_from_local(xml_filename, data_dir)

        # 计算边界框
        key_points = [(start_lat, start_lon), (end_lat, end_lon)]
        if intermediate_coords:
            key_points.extend(intermediate_coords)

        lats = [p[0] for p in key_points]
        lons = [p[1] for p in key_points]

        min_lat = min(lats)
        max_lat = max(lats)
        min_lon = min(lons)
        max_lon = max(lons)

        # 扩展边界
        margin_lat = margin_km / 111.0
        margin_lon = margin_km / (111.0 * max(0.1, abs((min_lat + max_lat) / 2)))

        north = max_lat + margin_lat
        south = min_lat - margin_lat
        east = max_lon + margin_lon
        west = min_lon - margin_lon

        print(f"\n正在通过边界框过滤路网...")
        print(f"  边界框: 北{north:.6f}°, 西{west:.6f}°, 南{south:.6f}°, 东{east:.6f}°")
        print(f"  扩展距离: {margin_km} km")

        # 过滤节点（在边界框内的节点）
        nodes_to_keep = []
        for node in G.nodes():
            node_data = G.nodes[node]
            node_lat = node_data['y']
            node_lon = node_data['x']

            if south <= node_lat <= north and west <= node_lon <= east:
                nodes_to_keep.append(node)

        # 创建子图
        G_filtered = G.subgraph(nodes_to_keep).copy()

        print(f"✓ 边界框过滤完成:")
        print(f"  原始节点: {len(G.nodes)}")
        print(f"  过滤后节点: {len(G_filtered.nodes)}")
        print(f"  过滤后边: {len(G_filtered.edges)}")

        # 移除孤立节点（度为0的节点）
        isolated_nodes = [node for node in G_filtered.nodes() if G_filtered.degree(node) == 0]
        if isolated_nodes:
            G_filtered.remove_nodes_from(isolated_nodes)
            print(f"  移除孤立节点: {len(isolated_nodes)}")

        print(f"  最终节点: {len(G_filtered.nodes)}")
        print(f"  最终边: {len(G_filtered.edges)}")

        return G_filtered

    def get_node_by_coordinates(self, G: nx.MultiDiGraph, lat: float, lon: float) -> int:
        """
        根据坐标找到最近的节点

        Args:
            G: 路网图
            lat: 纬度
            lon: 经度

        Returns:
            最近的节点ID
        """
        point = Point(float(lon), float(lat))
        min_dist = float('inf')
        closest_node = None

        for node in G.nodes():
            node_data = G.nodes[node]
            node_point = Point(float(node_data['x']), float(node_data['y']))
            dist = point.distance(node_point)

            if dist < min_dist:
                min_dist = dist
                closest_node = node

        return closest_node

    def _get_node_name(self, G: nx.MultiDiGraph, node: int) -> str:
        """
        获取节点的描述性名称（用于调试和日志）

        Args:
            G: 路网图
            node: 节点ID

        Returns:
            节点名称
        """
        if node in G.nodes:
            node_data = G.nodes[node]
            # 尝试从节点属性中获取名称
            if 'name' in node_data:
                return node_data['name']
            # 如果有坐标，返回坐标信息
            y = node_data.get('y', 0)
            x = node_data.get('x', 0)
            return f"Node_{node} ({y:.4f}, {x:.4f})"
        return f"Unknown_Node_{node}"
    
    # 该函数已经适配OSM和Net两种路网数据
    def calculate_congestion_score(self, G: nx.MultiDiGraph) -> Dict:
        """
        计算路网的拥堵系数

        拥堵系数越高表示越拥堵，应尽量避开。
        基于道路类型和速度估计：高速/主干道 = 高拥堵。

        Args:
            G: 路网图

        Returns:
            拥堵系数字典 {(u, v): congestion_score}
        """
        congestion_scores = {}

        for u, v, data in G.edges(data=True):
            score = None

            # 优先使用 OSM highway 类型（OSM 模式）
            # 道路等级越高，交通流量越大，拥堵系数越高
            highway = data.get('highway')
            if highway:
                if 'motorway' in highway or 'trunk' in highway:
                    score = 0.9
                elif 'primary' in highway:
                    score = 0.7
                elif 'secondary' in highway:
                    score = 0.4
                elif 'tertiary' in highway or 'residential' in highway:
                    score = 0.2
                elif 'service' in highway or 'footway' in highway:
                    score = 0.1
                else:
                    score = 0.5

            # Net 模式：使用 speed 计算拥堵系数
            # 速度越高，通常是主干道/高速公路（更拥堵）
            if score is None:
                speed = data.get('speed')
                if speed is not None:
                    speed = float(speed)
                    # 速度越高，拥堵系数越高
                    # >25 m/s (90km/h): 高速公路，高拥堵
                    # 16-25 m/s (58-90km/h): 主干道
                    # 10-16 m/s (36-58km/h): 次干道
                    # 5-10 m/s (18-36km/h): 支路
                    # <5 m/s: 服务道路/小区道路，低拥堵
                    if speed >= 25:
                        score = 0.85
                    elif speed >= 16:
                        score = 0.65
                    elif speed >= 10:
                        score = 0.45
                    elif speed >= 5:
                        score = 0.25
                    else:
                        score = 0.10

            # 默认值
            if score is None:
                score = 0.5

            congestion_scores[(u, v)] = min(1.0, max(0.0, score))

        return congestion_scores

    def calculate_route_stats(self, G: nx.MultiDiGraph, route: List[int], congestion_scores: Dict) -> Dict:
        """
        计算路线的统计信息

        Args:
            G: 路网图
            route: 路线节点列表
            congestion_scores: 拥堵系数字典

        Returns:
            路线统计信息
        """
        # 处理非法route输入
        if not route or not isinstance(route, (list, tuple)) or len(route) < 2:
            return {
                'total_distance': 0,
                'edge_count': 0,
                'avg_congestion_score': 0,
                'edge_details': [],
                'congestion_percentage': 0
            }

        total_distance = 0
        congestion_score_sum = 0
        edge_count = 0
        edge_details = []

        for i in range(len(route) - 1):
            u, v = route[i], route[i + 1]

            # 获取边的属性，若不存在则容错处理
            try:
                edge_data = G[u][v][0]
                distance = edge_data.get('length', 0)
                highway = edge_data.get('highway', 'unknown')
                congestion_score = congestion_scores.get((u, v), 0.5)
                coords = (G.nodes[u]['y'], G.nodes[u]['x'], G.nodes[v]['y'], G.nodes[v]['x'])
            except Exception:
                # 如果找不到该边或出现其他异常，使用默认值并继续
                distance = 1000
                highway = 'unknown'
                congestion_score = 0.5
                # 坐标可能缺失，尝试从节点属性读取，否则设为None
                try:
                    coords = (
                        G.nodes[u].get('y', None), G.nodes[u].get('x', None),
                        G.nodes[v].get('y', None), G.nodes[v].get('x', None)
                    )
                except Exception:
                    coords = (None, None, None, None)

            edge_details.append({
                'from_node': u,
                'to_node': v,
                'distance': distance,
                'highway_type': highway,
                'congestion_score': congestion_score,
                'coordinates': coords
            })

            total_distance += distance
            congestion_score_sum += congestion_score * distance
            edge_count += 1

        # 计算加权平均拥堵系数
        if total_distance > 0:
            avg_congestion_score = congestion_score_sum / total_distance
        else:
            avg_congestion_score = 0

        return {
            'total_distance': total_distance,
            'edge_count': edge_count,
            'avg_congestion_score': avg_congestion_score,
            'edge_details': edge_details,
            'congestion_percentage': avg_congestion_score * 100
        }


class NetDataProcessor:
    """Net路网数据处理器"""

    def __init__(self):
        """初始化Net数据处理器"""
        self.projection = None
        self.net_offset = None
        self.orig_boundary = None

    def load_network_from_net(self, net_file: str) -> nx.MultiDiGraph:
        """
        从Net .net.xml文件加载NetworkX图

        Args:
            net_file: Net路网文件路径（.net.xml或.sumonet.xml）

        Returns:
            G: nx.MultiDiGraph
        """
        import os

        if not os.path.exists(net_file):
            raise FileNotFoundError(f"Net路网文件不存在: {net_file}")

        print(f"正在读取Net路网文件: {net_file}")

        # 解析XML
        tree = ET.parse(net_file)
        root = tree.getroot()

        # 解析location信息用于坐标转换
        location = root.find('location')
        if location is not None:
            self._parse_location(location)
        else:
            # 尝试从edge/node推断
            self.projection = None

        # 读取节点（可能没有显式的<node>元素，需要从edge提取）
        nodes = {}
        node_positions = {}  # node_id -> (x, y) 从lane shape中提取

        # 读取边（跳过internal边）
        edge_id_to_info = {}
        edge_count = 0
        skipped_internal = 0

        for edge_elem in root.findall('.//edge'):
            edge_id = edge_elem.get('id')
            function = edge_elem.get('function', '')

            # 跳过internal边
            if function == 'internal' or edge_id.startswith(':'):
                skipped_internal += 1
                continue

            from_node = edge_elem.get('from')
            to_node = edge_elem.get('to')

            # 获取第一条lane的属性作为边的属性
            lane_elem = edge_elem.find('lane')
            if lane_elem is None:
                continue

            length = float(lane_elem.get('length', 0))
            speed = float(lane_elem.get('speed', 13.9))
            shape_str = lane_elem.get('shape', '')

            # 解析shape坐标
            shape = []
            if shape_str:
                for coord in shape_str.split():
                    parts = coord.split(',')
                    if len(parts) == 2:
                        shape.append((float(parts[0]), float(parts[1])))

            # 提取起点坐标（shape的第一个点）
            if shape and len(shape) >= 1:
                node_positions[from_node] = shape[0]
            # 提取终点坐标（shape的最后一个点）
            if shape and len(shape) >= 1:
                node_positions[to_node] = shape[-1]

            # 获取priority（用于绿化评分）
            priority = int(edge_elem.get('priority', 2))

            # 存储边信息
            edge_info = {
                'from': from_node,
                'to': to_node,
                'length': length,
                'speed': speed,
                'shape': shape,
                'priority': priority,
                'edge_id': edge_id
            }
            edge_id_to_info[edge_id] = edge_info
            edge_count += 1

        # 构建NetworkX图
        G = nx.MultiDiGraph()

        # 添加节点到图
        for node_id, (x, y) in node_positions.items():
            G.add_node(node_id, x=x, y=y)

        # 添加边到图
        for edge_id, edge_info in edge_id_to_info.items():
            from_node = edge_info['from']
            to_node = edge_info['to']
            if from_node in G.nodes() and to_node in G.nodes():
                G.add_edge(from_node, to_node,
                          length=edge_info['length'],
                          speed=edge_info['speed'],
                          shape=edge_info['shape'],
                          priority=edge_info['priority'],
                          edge_id=edge_id)

        print(f"  读取到 {edge_count} 条边（跳过 {skipped_internal} 条internal边）")
        print(f"  提取到 {len(node_positions)} 个节点坐标")
        print(f"  NetworkX图: {len(G.nodes)} 节点, {len(G.edges)} 边")

        return G

    def _parse_location(self, location_elem):
        """解析location元素获取投影参数"""
        self.net_offset = location_elem.get('netOffset', '0,0')
        self.orig_boundary = location_elem.get('origBoundary', '')
        self.conv_boundary = location_elem.get('convBoundary', '')

        proj_str = location_elem.get('projParameter', '')
        if proj_str:
            try:
                if '+proj=' in proj_str:
                    self.projection = pyproj.Proj(proj_str)
                else:
                    self.projection = None
            except Exception:
                self.projection = None

        # 解析边界用于坐标转换
        self.boundary_params = None
        if self.orig_boundary and self.conv_boundary:
            try:
                # origBoundary: minLon, minLat, maxLon, maxLat
                ob = list(map(float, self.orig_boundary.split(',')))
                # convBoundary: minX, minY, maxX, maxY
                cb = list(map(float, self.conv_boundary.split(',')))
                self.boundary_params = {
                    'min_lon': ob[0], 'min_lat': ob[1],
                    'max_lon': ob[2], 'max_lat': ob[3],
                    'min_x': cb[0], 'min_y': cb[1],
                    'max_x': cb[2], 'max_y': cb[3]
                }
            except Exception:
                self.boundary_params = None

    def latlon_to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        """
        经纬度转XY坐标

        Args:
            lat: 纬度
            lon: 经度

        Returns:
            (x, y) XY坐标
        """
        # 优先使用边界参数进行线性转换
        if self.boundary_params:
            bp = self.boundary_params
            lon_range = bp['max_lon'] - bp['min_lon']
            lat_range = bp['max_lat'] - bp['min_lat']
            x_range = bp['max_x'] - bp['min_x']
            y_range = bp['max_y'] - bp['min_y']

            if lon_range > 0 and lat_range > 0:
                x = bp['min_x'] + ((lon - bp['min_lon']) / lon_range) * x_range
                y = bp['min_y'] + ((lat - bp['min_lat']) / lat_range) * y_range
                return x, y

        # 备选：使用pyproj投影
        if self.projection is not None:
            try:
                x, y = self.projection(lon, lat)
                if self.net_offset:
                    parts = self.net_offset.split(',')
                    if len(parts) == 2:
                        offset_x, offset_y = float(parts[0]), float(parts[1])
                        x -= offset_x
                        y -= offset_y
                return x, y
            except Exception:
                pass

        return lon, lat

    def xy_to_latlon(self, x: float, y: float) -> Tuple[float, float]:
        """
        XY坐标转经纬度

        Args:
            x: X坐标
            y: Y坐标

        Returns:
            (lat, lon) 经纬度
        """
        # 优先使用边界参数进行线性转换（更可靠）
        if self.boundary_params:
            bp = self.boundary_params
            lon_range = bp['max_lon'] - bp['min_lon']
            lat_range = bp['max_lat'] - bp['min_lat']
            x_range = bp['max_x'] - bp['min_x']
            y_range = bp['max_y'] - bp['min_y']

            if x_range > 0 and y_range > 0:
                lon = bp['min_lon'] + ((x - bp['min_x']) / x_range) * lon_range
                lat = bp['min_lat'] + ((y - bp['min_y']) / y_range) * lat_range
                return lat, lon

        # 备选：使用pyproj投影
        if self.projection is not None:
            try:
                offset_x, offset_y = 0, 0
                if self.net_offset:
                    parts = self.net_offset.split(',')
                    if len(parts) == 2:
                        offset_x, offset_y = float(parts[0]), float(parts[1])
                lon, lat = self.projection(x + offset_x, y + offset_y, inverse=True)
                return lat, lon
            except Exception:
                pass

        return y, x

    def find_nearest_node(self, G: nx.MultiDiGraph, lat: float, lon: float) -> str:
        """
        找到最近的节点（使用经纬度）

        Args:
            G: 路网图
            lat: 纬度
            lon: 经度

        Returns:
            最近节点的ID
        """
        min_dist = float('inf')
        closest_node = None

        for node in G.nodes():
            node_data = G.nodes[node]
            node_x = node_data.get('x', 0)
            node_y = node_data.get('y', 0)

            # 将节点XY坐标转换为经纬度
            node_lat, node_lon = self.xy_to_latlon(node_x, node_y)

            # 计算距离（使用简单的欧氏距离近似）
            dist = np.sqrt((lat - node_lat) ** 2 + (lon - node_lon) ** 2)
            if dist < min_dist:
                min_dist = dist
                closest_node = node

        return closest_node

    def nodes_to_edge_ids(self, G: nx.MultiDiGraph, node_path: List[str],
                          edge_id_to_info: Dict[str, Dict]) -> Tuple[List[str], List[List[Tuple]]]:
        """
        将节点路径转换为edge_id序列

        Args:
            G: 路网图
            node_path: 节点ID列表
            edge_id_to_info: 边信息字典

        Returns:
            (edge_id序列, 对应的几何坐标点列表)
        """
        edge_ids = []
        edge_geometries = []

        # 构建快速查找索引：(from_node, to_node) -> edge_id
        # 将 O(路径长度 × 边数量) 的查找优化为 O(路径长度)
        edge_index = {}
        for eid, info in edge_id_to_info.items():
            key = (info['from'], info['to'])
            edge_index[key] = (eid, info.get('shape', []))

        for i in range(len(node_path) - 1):
            from_node = node_path[i]
            to_node = node_path[i + 1]

            # 使用索引快速查找
            key = (from_node, to_node)
            if key in edge_index:
                edge_id, geometry = edge_index[key]
                edge_ids.append(edge_id)
                edge_geometries.append(geometry)
            else:
                # 如果找不到，尝试从图中获取
                try:
                    edge_data = G[from_node][to_node][0]
                    edge_id = edge_data.get('edge_id')
                    geometry = edge_data.get('shape', [])
                    if edge_id:
                        edge_ids.append(edge_id)
                        edge_geometries.append(geometry)
                except Exception:
                    edge_ids.append(None)
                    edge_geometries.append([])

        return edge_ids, edge_geometries

    def build_edge_geometries_from_nodes(self, G: nx.MultiDiGraph, node_path: List[str]) -> List[List[Tuple]]:
        """
        从节点路径构建边的几何坐标

        Args:
            G: 路网图
            node_path: 节点ID列表

        Returns:
            每个edge对应的几何坐标点列表
        """
        edge_geometries = []

        for i in range(len(node_path) - 1):
            from_node = node_path[i]
            to_node = node_path[i + 1]

            try:
                edge_data = G[from_node][to_node][0]
                shape = edge_data.get('shape', [])

                if shape and len(shape) >= 2:
                    edge_geometries.append(shape)
                else:
                    # 使用节点坐标
                    x1, y1 = G.nodes[from_node]['x'], G.nodes[from_node]['y']
                    x2, y2 = G.nodes[to_node]['x'], G.nodes[to_node]['y']
                    edge_geometries.append([(x1, y1), (x2, y2)])
            except Exception:
                edge_geometries.append([])

        return edge_geometries


# ============================================================
# 路径评估指标计算函数
# ============================================================

def calculate_via_satisfaction(route_nodes: list, via_node_ids: list) -> float:
    """计算途经点满足度（路径中包含的途经点比例）

    Args:
        route_nodes: 路径节点列表
        via_node_ids: 途经点节点ID列表

    Returns:
        满足度百分比 (0-100)
    """
    if not via_node_ids:
        return 0.0
    satisfied_count = sum(1 for via_id in via_node_ids if via_id in route_nodes)
    return (satisfied_count / len(via_node_ids)) * 100


def calculate_distance_satisfaction(actual_distance: float, target_distance_km: float) -> float:
    """计算距离满足度

    距离满足度 = 100 - |实际距离 - 目标距离| / 目标距离 × 100
    100% 表示实际距离完全等于目标距离

    Args:
        actual_distance: 实际距离（米）
        target_distance_km: 目标距离（公里）

    Returns:
        满足度百分比，100%为最接近目标，低于100%表示偏离
    """
    if not target_distance_km:
        return 0.0
    target_distance_m = target_distance_km * 1000
    if target_distance_m == 0:
        return 0.0
    deviation = abs(actual_distance - target_distance_m) / target_distance_m * 100
    return max(0, 100 - deviation)


def calculate_route_metrics(route: list, G: nx.MultiDiGraph,
                           congestion_scores: Dict) -> Dict:
    """计算路径的统计指标

    Args:
        route: 路径节点列表
        G: 路网图
        congestion_scores: 拥堵系数字典

    Returns:
        dict with:
        - total_distance: 总距离（米）
        - edge_count: 边数
        - avg_congestion_score: 平均拥堵系数
        - congestion_percentage: 拥堵占比（%）
    """
    if not route or len(route) < 2:
        return {
            'total_distance': 0,
            'edge_count': 0,
            'avg_congestion_score': 0,
            'congestion_percentage': 0
        }

    total_distance = 0
    congestion_score_sum = 0
    edge_count = 0

    for i in range(len(route) - 1):
        u, v = route[i], route[i + 1]

        try:
            edge_data = G[u][v][0]
            distance = float(edge_data.get('length', 100))
            total_distance += distance

            congestion_score = congestion_scores.get((u, v), 0.5)
            congestion_score_sum += congestion_score * distance
            edge_count += 1
        except:
            total_distance += 1000

    if total_distance > 0:
        avg_congestion_score = congestion_score_sum / total_distance
        congestion_percentage = avg_congestion_score * 100
    else:
        avg_congestion_score = 0
        congestion_percentage = 0

    return {
        'total_distance': total_distance,
        'edge_count': edge_count,
        'avg_congestion_score': avg_congestion_score,
        'congestion_percentage': congestion_percentage
    }


if __name__ == "__main__":
    # 测试代码
    processor = OSMDataProcessor()
    print("OSM数据处理器初始化完成")

    net_processor = NetDataProcessor()
    print("Net数据处理器初始化完成")
