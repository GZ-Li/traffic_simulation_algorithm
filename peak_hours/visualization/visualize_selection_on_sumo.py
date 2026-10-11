#!/usr/bin/env python3
"""
在 SUMO 路网底图上高亮模拟器选中的 junction / road。

设计目标：
1. 模拟器侧与 SUMO 侧的 road_id / junction_id 一致时，直接做 ID 匹配；
2. lane_id 不参与匹配；
3. 既支持命令行直接传 CSV，也支持读取现有 JSON 结果文件。

常见用法：

1) 直接传 RL 选中的路口：
python python/incremental_algos/visualization/visualize_selection_on_sumo.py \
  --net-file python/Multi_Accident/Shaped.net.xml \
  --junction-json rl/selected_junctions_15/selected_rl_junctions.json \
  --highlight-junction-adjacent-roads \
  --output rl_selected.png

2) 直接画公交规划结果中的 road_ids：
python python/incremental_algos/visualization/visualize_selection_on_sumo.py \
  --net-file python/Multi_Accident/Shaped.net.xml \
  --road-json output/planned_routes.json \
  --output bus_routes.png

3) 直接传一组 id：
python python/incremental_algos/visualization/visualize_selection_on_sumo.py \
  --net-file python/Multi_Accident/Shaped.net.xml \
  --junction-ids 300000669,300000048 \
  --road-ids 200000000,200000001 \
  --output selection.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET

_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "wuhan_simulator_matplotlib_cache")
os.makedirs(os.path.join(_CACHE_ROOT, "mpl"), exist_ok=True)
os.makedirs(os.path.join(_CACHE_ROOT, "xdg"), exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_ROOT, "mpl"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_CACHE_ROOT, "xdg"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


DEFAULT_BASE_ROAD_COLOR = "#C7CDD4"
DEFAULT_CONTEXT_ROAD_COLOR = "#F4A261"
DEFAULT_SELECTED_ROAD_COLOR = "#D62828"
DEFAULT_SELECTED_JUNCTION_COLOR = "#1D4ED8"


@dataclass(frozen=True)
class JunctionGeometry:
    junction_id: int
    x: float
    y: float
    junction_type: str


@dataclass(frozen=True)
class EdgeGeometry:
    edge_id: int
    from_junction: Optional[int]
    to_junction: Optional[int]
    shape: Tuple[Tuple[float, float], ...]

    @property
    def midpoint(self) -> Tuple[float, float]:
        if not self.shape:
            return (0.0, 0.0)
        mid_idx = len(self.shape) // 2
        return self.shape[mid_idx]


@dataclass(frozen=True)
class NetGeometry:
    junctions: Dict[int, JunctionGeometry]
    edges: Dict[int, EdgeGeometry]
    bbox: Tuple[float, float, float, float]


def _parse_id_csv(raw: Optional[str]) -> List[int]:
    if raw is None or raw.strip() == "":
        return []
    ids: List[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        ids.append(int(item))
    return ids


def _try_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _collect_ints(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (int, float, str)):
        maybe_int = _try_int(value)
        return [] if maybe_int is None else [maybe_int]
    if isinstance(value, list):
        result: List[int] = []
        for item in value:
            result.extend(_collect_ints(item))
        return result
    return []


def _collect_values_by_key(obj: Any, target_keys: Set[str]) -> List[int]:
    found: List[int] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in target_keys:
                found.extend(_collect_ints(value))
            found.extend(_collect_values_by_key(value, target_keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_values_by_key(item, target_keys))
    return found


def _extract_junction_ids(payload: Any) -> List[int]:
    result: Set[int] = set()
    result.update(_collect_values_by_key(payload, {"junction_ids", "rl_junction_ids"}))

    if isinstance(payload, dict):
        selected = payload.get("selected")
        if isinstance(selected, dict):
            for key in selected.keys():
                maybe_int = _try_int(key)
                if maybe_int is not None:
                    result.add(maybe_int)

        junctions = payload.get("junctions")
        if isinstance(junctions, dict):
            for key in junctions.keys():
                maybe_int = _try_int(key)
                if maybe_int is not None:
                    result.add(maybe_int)

    return sorted(result)


def _extract_road_ids(payload: Any) -> List[int]:
    result: Set[int] = set()
    result.update(
        _collect_values_by_key(payload, {"road_ids", "resolved_route_road_ids", "path_road_ids"})
    )
    return sorted(result)


def _load_json_ids(
    paths: Sequence[str],
    extractor,
) -> List[int]:
    result: Set[int] = set()
    for path in paths:
        with open(path, "r") as f:
            payload = json.load(f)
        result.update(extractor(payload))
    return sorted(result)


def _parse_shape(shape: str) -> Tuple[Tuple[float, float], ...]:
    coords: List[Tuple[float, float]] = []
    for pair in shape.split():
        x_str, y_str = pair.split(",")
        coords.append((float(x_str), float(y_str)))
    return tuple(coords)


def _safe_int_attr(elem: ET.Element, name: str) -> Optional[int]:
    raw = elem.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_sumo_net(net_file: str) -> NetGeometry:
    tree = ET.parse(net_file)
    root = tree.getroot()

    junctions: Dict[int, JunctionGeometry] = {}
    for junction in root.findall("junction"):
        junction_id = _safe_int_attr(junction, "id")
        if junction_id is None:
            continue
        x = float(junction.get("x", "0"))
        y = float(junction.get("y", "0"))
        junctions[junction_id] = JunctionGeometry(
            junction_id=junction_id,
            x=x,
            y=y,
            junction_type=junction.get("type", ""),
        )

    edges: Dict[int, EdgeGeometry] = {}
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id is None or edge_id.startswith(":"):
            continue
        try:
            numeric_edge_id = int(edge_id)
        except ValueError:
            continue

        lanes = edge.findall("lane")
        if not lanes:
            continue
        preferred_lane = lanes[len(lanes) // 2]
        shape_attr = preferred_lane.get("shape")
        if not shape_attr:
            for lane in lanes:
                shape_attr = lane.get("shape")
                if shape_attr:
                    break
        if not shape_attr:
            continue

        shape = _parse_shape(shape_attr)
        if not shape:
            continue

        for x, y in shape:
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

        edges[numeric_edge_id] = EdgeGeometry(
            edge_id=numeric_edge_id,
            from_junction=_try_int(edge.get("from")),
            to_junction=_try_int(edge.get("to")),
            shape=shape,
        )

    if not edges:
        raise ValueError(f"未在 {net_file} 中解析到任何非 internal edge")

    return NetGeometry(
        junctions=junctions,
        edges=edges,
        bbox=(min_x, min_y, max_x, max_y),
    )


def _bbox_from_points(points: Iterable[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float]]:
    points = list(points)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _expand_bbox(
    bbox: Tuple[float, float, float, float],
    padding: float,
) -> Tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bbox
    if min_x == max_x:
        min_x -= 1.0
        max_x += 1.0
    if min_y == max_y:
        min_y -= 1.0
        max_y += 1.0
    return (min_x - padding, min_y - padding, max_x + padding, max_y + padding)


def _edge_intersects_bbox(edge: EdgeGeometry, bbox: Tuple[float, float, float, float]) -> bool:
    min_x, min_y, max_x, max_y = bbox
    xs = [coord[0] for coord in edge.shape]
    ys = [coord[1] for coord in edge.shape]
    edge_min_x = min(xs)
    edge_max_x = max(xs)
    edge_min_y = min(ys)
    edge_max_y = max(ys)
    return not (
        edge_max_x < min_x
        or edge_min_x > max_x
        or edge_max_y < min_y
        or edge_min_y > max_y
    )


def _collect_adjacent_roads(
    net: NetGeometry,
    junction_ids: Sequence[int],
) -> List[int]:
    junction_id_set = set(junction_ids)
    return sorted(
        edge_id
        for edge_id, edge in net.edges.items()
        if edge.from_junction in junction_id_set or edge.to_junction in junction_id_set
    )


def _plot_edge(
    ax,
    edge: EdgeGeometry,
    color: str,
    linewidth: float,
    alpha: float,
    zorder: int,
) -> None:
    xs = [coord[0] for coord in edge.shape]
    ys = [coord[1] for coord in edge.shape]
    ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha, zorder=zorder)


def _annotate_edge(ax, edge: EdgeGeometry, color: str, fontsize: int) -> None:
    x, y = edge.midpoint
    ax.text(
        x,
        y,
        str(edge.edge_id),
        fontsize=fontsize,
        color=color,
        ha="center",
        va="center",
        bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
        zorder=6,
    )


def _annotate_junction(ax, junction: JunctionGeometry, color: str, fontsize: int) -> None:
    ax.text(
        junction.x,
        junction.y,
        str(junction.junction_id),
        fontsize=fontsize,
        color=color,
        ha="left",
        va="bottom",
        bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
        zorder=7,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在 SUMO net.xml 上高亮 junction / road 选择结果")
    parser.add_argument(
        "--net-file",
        required=True,
        help="SUMO net.xml 文件路径，例如 python/Multi_Accident/Shaped.net.xml",
    )
    parser.add_argument(
        "--junction-ids",
        default="",
        help="直接传入 junction id，逗号分隔",
    )
    parser.add_argument(
        "--road-ids",
        default="",
        help="直接传入 road id，逗号分隔",
    )
    parser.add_argument(
        "--junction-json",
        action="append",
        default=[],
        help="读取 junction id 的 JSON 文件，可重复传入；兼容 selected_rl_junctions.json / rl_junctions.json",
    )
    parser.add_argument(
        "--road-json",
        action="append",
        default=[],
        help="读取 road id 的 JSON 文件，可重复传入；兼容 planned_routes.json 等",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="输出图片路径，如 output/selection.png",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="匹配结果报告路径；默认与输出图片同目录同名 .report.json",
    )
    parser.add_argument(
        "--title",
        default="",
        help="图标题；默认自动生成",
    )
    parser.add_argument(
        "--view",
        choices=["selection", "all"],
        default="selection",
        help="selection: 仅显示选区附近；all: 显示整个路网",
    )
    parser.add_argument(
        "--padding",
        type=float,
        default=300.0,
        help="selection 视图时在选区外扩的边距，单位为 SUMO net 坐标",
    )
    parser.add_argument(
        "--highlight-junction-adjacent-roads",
        action="store_true",
        help="高亮所有与所选 junction 直接相连的 road",
    )
    parser.add_argument(
        "--label-junctions",
        action="store_true",
        help="给选中 junction 标注 id",
    )
    parser.add_argument(
        "--label-roads",
        action="store_true",
        help="给显式选中的 road 标注 id",
    )
    parser.add_argument(
        "--figsize",
        default="14,10",
        help="画布大小，格式为 width,height",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=180,
        help="输出 DPI",
    )
    return parser


def _parse_figsize(raw: str) -> Tuple[float, float]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2:
        raise ValueError("--figsize 格式应为 width,height")
    return (float(parts[0]), float(parts[1]))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    net = parse_sumo_net(args.net_file)

    junction_ids: Set[int] = set(_parse_id_csv(args.junction_ids))
    road_ids: Set[int] = set(_parse_id_csv(args.road_ids))
    if args.junction_json:
        junction_ids.update(_load_json_ids(args.junction_json, _extract_junction_ids))
    if args.road_json:
        road_ids.update(_load_json_ids(args.road_json, _extract_road_ids))

    matched_junction_ids = sorted(junction_id for junction_id in junction_ids if junction_id in net.junctions)
    missing_junction_ids = sorted(junction_id for junction_id in junction_ids if junction_id not in net.junctions)
    matched_road_ids = sorted(road_id for road_id in road_ids if road_id in net.edges)
    missing_road_ids = sorted(road_id for road_id in road_ids if road_id not in net.edges)

    adjacent_road_ids: List[int] = []
    if args.highlight_junction_adjacent_roads and matched_junction_ids:
        adjacent_road_ids = _collect_adjacent_roads(net, matched_junction_ids)

    selection_points: List[Tuple[float, float]] = []
    for junction_id in matched_junction_ids:
        junction = net.junctions[junction_id]
        selection_points.append((junction.x, junction.y))
    for road_id in matched_road_ids:
        selection_points.extend(net.edges[road_id].shape)
    for road_id in adjacent_road_ids:
        selection_points.extend(net.edges[road_id].shape)

    if args.view == "selection" and selection_points:
        view_bbox = _expand_bbox(_bbox_from_points(selection_points), args.padding)
    else:
        view_bbox = net.bbox

    visible_edges = {
        edge_id: edge
        for edge_id, edge in net.edges.items()
        if _edge_intersects_bbox(edge, view_bbox)
    }

    road_ids_set = set(matched_road_ids)
    adjacent_road_ids_set = set(adjacent_road_ids) - road_ids_set

    figsize = _parse_figsize(args.figsize)
    fig, ax = plt.subplots(figsize=figsize, dpi=args.dpi)

    for edge_id, edge in visible_edges.items():
        if edge_id in road_ids_set:
            color = DEFAULT_SELECTED_ROAD_COLOR
            linewidth = 2.6
            alpha = 0.95
            zorder = 4
        elif edge_id in adjacent_road_ids_set:
            color = DEFAULT_CONTEXT_ROAD_COLOR
            linewidth = 1.7
            alpha = 0.9
            zorder = 3
        else:
            color = DEFAULT_BASE_ROAD_COLOR
            linewidth = 0.7
            alpha = 0.75
            zorder = 1
        _plot_edge(ax, edge, color=color, linewidth=linewidth, alpha=alpha, zorder=zorder)

    junction_xs = [net.junctions[junction_id].x for junction_id in matched_junction_ids]
    junction_ys = [net.junctions[junction_id].y for junction_id in matched_junction_ids]
    if junction_xs and junction_ys:
        ax.scatter(
            junction_xs,
            junction_ys,
            s=55,
            color=DEFAULT_SELECTED_JUNCTION_COLOR,
            edgecolors="white",
            linewidths=0.9,
            zorder=5,
        )

    if args.label_roads:
        for road_id in matched_road_ids:
            if road_id in visible_edges:
                _annotate_edge(ax, visible_edges[road_id], DEFAULT_SELECTED_ROAD_COLOR, fontsize=8)

    if args.label_junctions:
        for junction_id in matched_junction_ids:
            _annotate_junction(ax, net.junctions[junction_id], DEFAULT_SELECTED_JUNCTION_COLOR, fontsize=8)

    min_x, min_y, max_x, max_y = view_bbox
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal")
    ax.set_xlabel("SUMO X")
    ax.set_ylabel("SUMO Y")
    auto_title = (
        f"SUMO Selection Overlay | junctions={len(matched_junction_ids)} roads={len(matched_road_ids)}"
    )
    ax.set_title(args.title or auto_title)
    ax.grid(True, linestyle="--", alpha=0.25)

    legend_handles = [
        Line2D([0], [0], color=DEFAULT_BASE_ROAD_COLOR, linewidth=1.2, label="Base Roads"),
    ]
    if adjacent_road_ids_set:
        legend_handles.append(
            Line2D([0], [0], color=DEFAULT_CONTEXT_ROAD_COLOR, linewidth=2.0, label="Junction-Adjacent Roads")
        )
    if matched_road_ids:
        legend_handles.append(
            Line2D([0], [0], color=DEFAULT_SELECTED_ROAD_COLOR, linewidth=2.6, label="Selected Roads")
        )
    if matched_junction_ids:
        legend_handles.append(
            Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=DEFAULT_SELECTED_JUNCTION_COLOR,
                markeredgecolor="white",
                markersize=8,
                label="Selected Junctions",
            )
        )
    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper right")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(args.output, bbox_inches="tight")
    plt.close(fig)

    report_json = args.report_json
    if not report_json:
        base, _ext = os.path.splitext(args.output)
        report_json = f"{base}.report.json"

    report = {
        "net_file": os.path.abspath(args.net_file),
        "output_image": os.path.abspath(args.output),
        "view": args.view,
        "padding": args.padding,
        "highlight_junction_adjacent_roads": args.highlight_junction_adjacent_roads,
        "input": {
            "junction_ids": sorted(junction_ids),
            "road_ids": sorted(road_ids),
            "junction_json": [os.path.abspath(path) for path in args.junction_json],
            "road_json": [os.path.abspath(path) for path in args.road_json],
        },
        "matched": {
            "junction_ids": matched_junction_ids,
            "road_ids": matched_road_ids,
            "junction_adjacent_road_ids": adjacent_road_ids,
        },
        "missing": {
            "junction_ids": missing_junction_ids,
            "road_ids": missing_road_ids,
        },
        "counts": {
            "parsed_junctions": len(net.junctions),
            "parsed_non_internal_roads": len(net.edges),
            "visible_roads_in_view": len(visible_edges),
            "matched_junctions": len(matched_junction_ids),
            "matched_roads": len(matched_road_ids),
            "missing_junctions": len(missing_junction_ids),
            "missing_roads": len(missing_road_ids),
        },
        "view_bbox": {
            "min_x": view_bbox[0],
            "min_y": view_bbox[1],
            "max_x": view_bbox[2],
            "max_y": view_bbox[3],
        },
    }
    with open(report_json, "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report["counts"], ensure_ascii=False))
    print(f"saved image: {args.output}")
    print(f"saved report: {report_json}")


if __name__ == "__main__":
    main()
