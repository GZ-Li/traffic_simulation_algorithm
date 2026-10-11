#!/usr/bin/env python3
"""
从 SUMO .sumonet.xml 路网中提取地理边界并下载对应范围的 OSM 原始数据。

默认使用 Overpass /api/map?bbox=... 下载全量数据。
可选使用 --highway-only 通过 /api/interpreter 仅下载道路相关数据。
"""

import os
import math
import argparse
from typing import Tuple
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
import xml.etree.ElementTree as ET
from utils import HIGHWAY_FILTER


def _read_sumo_bounds(sumonet_path: str) -> Tuple[float, float, float, float, str]:
    """
    读取 SUMO 路网文件的地理边界，返回 (minLon, minLat, maxLon, maxLat, projString).

    从 XML 的 location 元素中提取:
    - origBoundary: "minLon,minLat,maxLon,maxLat" (WGS84 经纬度)
    - projParameter: PROJ.4 投影字符串
    """
    tree = ET.parse(sumonet_path)
    root = tree.getroot()

    location = root.find('location')
    if location is None:
        raise ValueError(f"SUMO 路网文件中未找到 <location> 元素: {sumonet_path}")

    # origBoundary 格式: "minLon,minLat,maxLon,maxLat"
    orig_boundary = location.get('origBoundary')
    if not orig_boundary:
        raise ValueError(f"<location> 元素中未找到 origBoundary 属性")

    parts = [float(x) for x in orig_boundary.split(',')]
    if len(parts) != 4:
        raise ValueError(f"origBoundary 格式错误，期望 4 个值: {orig_boundary}")

    minLon, minLat, maxLon, maxLat = parts

    # projParameter: PROJ.4 投影字符串
    proj_string = location.get('projParameter', '')

    return minLon, minLat, maxLon, maxLat, proj_string


def _expand_bbox(left: float, bottom: float, right: float, top: float, margin_km: float) -> Tuple[float, float, float, float]:
    """扩展边界框，添加边距（公里）."""
    if margin_km <= 0:
        return left, bottom, right, top
    # 近似：纬度方向 1° ~ 111km，经度按纬度修正
    margin_lat = margin_km / 111.0
    mid_lat = (bottom + top) / 2.0
    margin_lon = margin_km / (111.0 * max(0.1, abs(math.cos(math.radians(mid_lat)))))
    return left - margin_lon, bottom - margin_lat, right + margin_lon, top + margin_lat


def _download_map_bbox(url: str, bbox: Tuple[float, float, float, float], output_path: str, timeout: int):
    """使用 Overpass /api/map?bbox=... 下载全量 OSM 数据."""
    left, bottom, right, top = bbox
    overpass_url = f"{url.rstrip('/')}/api/map?bbox={left},{bottom},{right},{top}"
    resp = requests.get(overpass_url, stream=True, timeout=timeout)
    resp.raise_for_status()
    with open(output_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def _download_highway_only(url: str, bbox: Tuple[float, float, float, float], output_path: str, timeout: int):
    """使用 Overpass /api/interpreter 仅下载道路相关数据."""
    left, bottom, right, top = bbox
    highway_filter = [h for h in HIGHWAY_FILTER if isinstance(h, str) and h.strip()]
    if not highway_filter:
        raise RuntimeError("HIGHWAY_FILTER 为空，无法构建过滤条件")
    highway_regex = "|".join(sorted(set(highway_filter)))
    # 使用客户端 timeout 减去缓冲时间作为 Overpass 服务端超时
    server_timeout = max(60, timeout - 30)
    query = (
        f"[out:xml][timeout:{server_timeout}];\n"
        f"(\n  way[\"highway\"~\"^{highway_regex}$\"]({bottom},{left},{top},{right});\n"
        f"  relation[\"highway\"~\"^{highway_regex}$\"]({bottom},{left},{top},{right});\n"
        ");\n"
        "(._;>;);\n"
        "out body;\n"
    )
    overpass_url = f"{url.rstrip('/')}/api/interpreter"
    resp = requests.post(overpass_url, data=query.encode("utf-8"), stream=True, timeout=timeout)
    resp.raise_for_status()
    with open(output_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def main():
    parser = argparse.ArgumentParser(description="从 SUMO .sumonet.xml 读取边界并下载 OSM 数据")
    parser.add_argument("--net", default="format/network.sumonet.xml", help="SUMO 路网文件路径 (.sumonet.xml)")
    parser.add_argument("--output", default="data/sumo_region.osm", help="输出 .osm 文件路径")
    parser.add_argument("--margin-km", type=float, default=1.0, help="边界扩展距离（公里）")
    parser.add_argument("--overpass", default="https://overpass-api.de", help="Overpass API 基址")
    parser.add_argument("--highway-only", action="store_true", help="仅下载道路相关数据（减小数据量）")
    parser.add_argument("--timeout", type=int, default=600, help="请求超时时间（秒）")

    args = parser.parse_args()

    if not os.path.exists(args.net):
        raise FileNotFoundError(f"SUMO 路网文件不存在: {args.net}")

    # 读取 SUMO 路网的边界和投影信息
    minLon, minLat, maxLon, maxLat, proj_string = _read_sumo_bounds(args.net)

    # 扩展边界
    left, bottom, right, top = _expand_bbox(minLon, minLat, maxLon, maxLat, args.margin_km)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    bbox = (left, bottom, right, top)
    print(f"SUMO 原始边界: minLon={minLon:.6f}, minLat={minLat:.6f}, maxLon={maxLon:.6f}, maxLat={maxLat:.6f}")
    if proj_string:
        print(f"SUMO 投影: {proj_string}")
    print(f"扩展后边界: left={left:.6f}, bottom={bottom:.6f}, right={right:.6f}, top={top:.6f}")
    print(f"Overpass: {args.overpass}")
    print(f"输出: {args.output}")

    if args.highway_only:
        print("下载模式: highway-only (interpreter)")
        _download_highway_only(args.overpass, bbox, args.output, args.timeout)
    else:
        print("下载模式: map bbox (全量)")
        _download_map_bbox(args.overpass, bbox, args.output, args.timeout)

    print("下载完成")


if __name__ == "__main__":
    main()
