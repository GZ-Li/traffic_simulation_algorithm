#!/usr/bin/env python3
"""
在 SUMO 路网底图上可视化一批公交的行驶路径。

输入优先级：
1. --planned-routes-json：读取运行时采样或 AddPerson 规划结果中的 resolved_route_road_ids
2. --added-bus-json + --bus-ids-json：只在输入 JSON 自身没有 person_id 时，用于补 person_id / index

常见用法：

python python/incremental_algos/visualization/visualize_bus_routes_on_sumo.py \
  --net-file python/Multi_Accident/Shaped.net.xml \
  --planned-routes-json results/bus_opt/direct_od_test/planned_routes.json \
  --added-bus-json results/bus_opt/direct_od_test/added_bus_persons.json \
  --bus-ids-json results/bus_opt/direct_od_test/bus_ids.json \
  --output results/bus_opt/direct_od_test/bus_routes.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(os.path.dirname(_CURRENT_DIR))
if _PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _PYTHON_ROOT)

from incremental_algos.visualization.visualize_selection_on_sumo import (
    DEFAULT_BASE_ROAD_COLOR,
    NetGeometry,
    _annotate_edge,
    _bbox_from_points,
    _edge_intersects_bbox,
    _expand_bbox,
    _parse_figsize,
    _plot_edge,
    parse_sumo_net,
)

import tempfile

_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "wuhan_simulator_matplotlib_cache")
os.makedirs(os.path.join(_CACHE_ROOT, "mpl"), exist_ok=True)
os.makedirs(os.path.join(_CACHE_ROOT, "xdg"), exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_ROOT, "mpl"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_CACHE_ROOT, "xdg"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


@dataclass(frozen=True)
class BusRouteRecord:
    route_index: int
    person_id: Optional[int]
    departure_time: Optional[float]
    road_ids: Tuple[int, ...]
    start_lane_id: Optional[int]
    end_lane_id: Optional[int]


def _try_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _try_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _load_json(path: str) -> Any:
    with open(path, "r") as f:
        return json.load(f)


def _load_person_ids_by_index(path: Optional[str]) -> Dict[int, int]:
    if not path:
        return {}
    payload = _load_json(path)
    if not isinstance(payload, list):
        return {}
    result: Dict[int, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        index_value = _try_int(item.get("index"))
        person_id = _try_int(item.get("person_id"))
        if index_value is None or person_id is None:
            continue
        result[index_value] = person_id
    return result


def _load_bus_id_set(path: Optional[str]) -> Set[int]:
    if not path:
        return set()
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return set()
    raw_ids = payload.get("bus_ids", [])
    if not isinstance(raw_ids, list):
        return set()
    result: Set[int] = set()
    for value in raw_ids:
        maybe_int = _try_int(value)
        if maybe_int is not None:
            result.add(maybe_int)
    return result


def _load_bus_route_records(
    planned_routes_json: str,
    added_bus_json: Optional[str],
    bus_ids_json: Optional[str],
) -> List[BusRouteRecord]:
    planned_payload = _load_json(planned_routes_json)
    if not isinstance(planned_payload, list):
        raise ValueError(f"{planned_routes_json} 不是 routes 列表")

    person_ids_by_index = _load_person_ids_by_index(added_bus_json)
    selected_bus_ids = _load_bus_id_set(bus_ids_json)

    records: List[BusRouteRecord] = []
    for idx, item in enumerate(planned_payload):
        if not isinstance(item, dict):
            continue
        route_index = _try_int(item.get("index"))
        if route_index is None:
            route_index = idx

        person_id = _try_int(item.get("person_id"))
        if person_id is None:
            person_id = person_ids_by_index.get(route_index)
        if selected_bus_ids and person_id is not None and person_id not in selected_bus_ids:
            continue

        road_ids_raw = item.get("resolved_route_road_ids", [])
        if not isinstance(road_ids_raw, list):
            road_ids_raw = []
        road_ids = tuple(
            road_id
            for road_id in (_try_int(value) for value in road_ids_raw)
            if road_id is not None
        )
        if not road_ids:
            continue

        start_lane = None
        end_lane = None
        start_lane_position = item.get("start_lane_position")
        if isinstance(start_lane_position, dict):
            start_lane = _try_int(start_lane_position.get("lane_id"))
        end_lane_position = item.get("end_lane_position")
        if isinstance(end_lane_position, dict):
            end_lane = _try_int(end_lane_position.get("lane_id"))

        records.append(
            BusRouteRecord(
                route_index=route_index,
                person_id=person_id,
                departure_time=_try_float(item.get("departure_time")),
                road_ids=road_ids,
                start_lane_id=start_lane,
                end_lane_id=end_lane,
            )
        )

    return records


def _compute_bus_route_bbox(
    net: NetGeometry,
    records: Sequence[BusRouteRecord],
) -> Optional[Tuple[float, float, float, float]]:
    points: List[Tuple[float, float]] = []
    for record in records:
        for road_id in record.road_ids:
            edge = net.edges.get(road_id)
            if edge is None:
                continue
            points.extend(edge.shape)
    return _bbox_from_points(points)


def _sample_route_labels(records: Sequence[BusRouteRecord], max_labels: int) -> List[BusRouteRecord]:
    if max_labels <= 0 or len(records) <= max_labels:
        return list(records)
    if max_labels == 1:
        return [records[0]]

    sampled: List[BusRouteRecord] = []
    last_idx = len(records) - 1
    for slot in range(max_labels):
        pos = round(slot * last_idx / (max_labels - 1))
        sampled.append(records[pos])
    seen: Set[int] = set()
    deduped: List[BusRouteRecord] = []
    for record in sampled:
        if record.route_index in seen:
            continue
        seen.add(record.route_index)
        deduped.append(record)
    return deduped


def _route_label(record: BusRouteRecord) -> str:
    if record.person_id is not None:
        return f"bus {record.person_id}"
    return f"route {record.route_index}"


def _sanitize_route_filename(record: BusRouteRecord) -> str:
    if record.person_id is not None:
        return f"bus_{record.person_id}.png"
    return f"route_{record.route_index}.png"


def _collect_visible_edges(
    net: NetGeometry,
    view_bbox: Tuple[float, float, float, float],
) -> Dict[int, Any]:
    return {
        edge_id: edge
        for edge_id, edge in net.edges.items()
        if _edge_intersects_bbox(edge, view_bbox)
    }


def _draw_base_roads(ax, visible_edges: Dict[int, Any]) -> None:
    for edge in visible_edges.values():
        _plot_edge(
            ax,
            edge,
            color=DEFAULT_BASE_ROAD_COLOR,
            linewidth=0.7,
            alpha=0.55,
            zorder=1,
        )


def _draw_route_records(
    ax,
    net: NetGeometry,
    visible_edges: Dict[int, Any],
    records: Sequence[BusRouteRecord],
    route_alpha: float,
    route_linewidth: float,
    label_routes: bool,
    max_route_labels: int,
    color_mode: str = "multi",
) -> Tuple[Set[int], Set[int]]:
    cmap = plt.get_cmap("tab20")
    matched_road_ids: Set[int] = set()
    missing_road_ids: Set[int] = set()

    for idx, record in enumerate(records):
        color = "#2563EB" if color_mode == "single" else cmap(idx % 20)
        for road_id in record.road_ids:
            edge = net.edges.get(road_id)
            if edge is None:
                missing_road_ids.add(road_id)
                continue
            matched_road_ids.add(road_id)
            _plot_edge(
                ax,
                edge,
                color=color,
                linewidth=route_linewidth,
                alpha=route_alpha,
                zorder=3,
            )

    if label_routes:
        for record in _sample_route_labels(records, max_route_labels):
            if not record.road_ids:
                continue
            anchor_road_id = record.road_ids[len(record.road_ids) // 2]
            edge = visible_edges.get(anchor_road_id) or net.edges.get(anchor_road_id)
            if edge is None:
                continue
            _annotate_edge(ax, edge, color="#111827", fontsize=7)
            x, y = edge.midpoint
            ax.text(
                x,
                y,
                _route_label(record),
                fontsize=7,
                color="#111827",
                ha="center",
                va="bottom",
                bbox={
                    "boxstyle": "round,pad=0.2",
                    "facecolor": "white",
                    "alpha": 0.9,
                    "edgecolor": "none",
                },
                zorder=6,
            )

    return matched_road_ids, missing_road_ids


def _render_route_figure(
    net: NetGeometry,
    records: Sequence[BusRouteRecord],
    output_path: str,
    title: str,
    figsize: Tuple[float, float],
    dpi: int,
    route_alpha: float,
    route_linewidth: float,
    view: str,
    padding: float,
    label_routes: bool,
    max_route_labels: int,
    color_mode: str = "multi",
) -> Tuple[Set[int], Set[int], Tuple[float, float, float, float]]:
    selected_bbox = _compute_bus_route_bbox(net, records)
    if view == "selection" and selected_bbox is not None:
        view_bbox = _expand_bbox(selected_bbox, padding)
    else:
        view_bbox = net.bbox

    visible_edges = _collect_visible_edges(net, view_bbox)
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    _draw_base_roads(ax, visible_edges)
    matched_road_ids, missing_road_ids = _draw_route_records(
        ax=ax,
        net=net,
        visible_edges=visible_edges,
        records=records,
        route_alpha=route_alpha,
        route_linewidth=route_linewidth,
        label_routes=label_routes,
        max_route_labels=max_route_labels,
        color_mode=color_mode,
    )

    min_x, min_y, max_x, max_y = view_bbox
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal")
    ax.set_xlabel("SUMO X")
    ax.set_ylabel("SUMO Y")
    ax.set_title(title)
    ax.grid(True, linestyle="--", alpha=0.25)
    ax.legend(
        handles=[
            Line2D([0], [0], color=DEFAULT_BASE_ROAD_COLOR, linewidth=1.2, label="Base Roads"),
            Line2D([0], [0], color="#2563EB", linewidth=2.0, label="Bus Routes"),
        ],
        loc="upper right",
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return matched_road_ids, missing_road_ids, view_bbox


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在 SUMO net.xml 底图上绘制公交行驶路径")
    parser.add_argument("--net-file", required=True, help="SUMO net.xml 文件路径")
    parser.add_argument("--planned-routes-json", required=True, help="planned_routes.json 路径")
    parser.add_argument("--added-bus-json", default="", help="added_bus_persons.json 路径")
    parser.add_argument("--bus-ids-json", default="", help="bus_ids.json 路径")
    parser.add_argument("--output", required=True, help="输出图片路径")
    parser.add_argument("--report-json", default="", help="报告 JSON 路径；默认同名 .report.json")
    parser.add_argument("--title", default="", help="图片标题")
    parser.add_argument("--view", choices=["selection", "all"], default="selection", help="视图范围")
    parser.add_argument("--padding", type=float, default=300.0, help="selection 视图外扩边距")
    parser.add_argument("--figsize", default="16,11", help="画布大小 width,height")
    parser.add_argument("--dpi", type=int, default=180, help="输出 DPI")
    parser.add_argument("--route-alpha", type=float, default=0.35, help="单条路径透明度")
    parser.add_argument("--route-linewidth", type=float, default=1.8, help="单条路径线宽")
    parser.add_argument("--label-routes", action="store_true", help="给部分路径标注 bus/person id")
    parser.add_argument("--max-route-labels", type=int, default=20, help="最多标注多少条路径")
    parser.add_argument("--per-bus-dir", default="", help="如果指定，则额外按公交分别输出到该目录")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    net = parse_sumo_net(args.net_file)
    records = _load_bus_route_records(
        planned_routes_json=args.planned_routes_json,
        added_bus_json=args.added_bus_json or None,
        bus_ids_json=args.bus_ids_json or None,
    )
    if not records:
        raise ValueError("没有在输入 JSON 中解析到任何可绘制的公交路径")
    figsize = _parse_figsize(args.figsize)
    unique_road_count = len({road_id for record in records for road_id in record.road_ids})
    auto_title = f"Bus Route Overlay | buses={len(records)} roads={unique_road_count}"
    matched_road_ids, missing_road_ids, view_bbox = _render_route_figure(
        net=net,
        records=records,
        output_path=args.output,
        title=args.title or auto_title,
        figsize=figsize,
        dpi=args.dpi,
        route_alpha=args.route_alpha,
        route_linewidth=args.route_linewidth,
        view=args.view,
        padding=args.padding,
        label_routes=args.label_routes,
        max_route_labels=args.max_route_labels,
        color_mode="multi",
    )

    per_bus_dir = args.per_bus_dir.strip()
    per_bus_outputs: List[Dict[str, Any]] = []
    if per_bus_dir:
        os.makedirs(per_bus_dir, exist_ok=True)
        for record in records:
            output_path = os.path.join(per_bus_dir, _sanitize_route_filename(record))
            single_title = f"{_route_label(record)} | roads={len(record.road_ids)}"
            single_matched, single_missing, single_bbox = _render_route_figure(
                net=net,
                records=[record],
                output_path=output_path,
                title=single_title,
                figsize=figsize,
                dpi=args.dpi,
                route_alpha=max(args.route_alpha, 0.8),
                route_linewidth=max(args.route_linewidth, 2.6),
                view=args.view,
                padding=args.padding,
                label_routes=True,
                max_route_labels=1,
                color_mode="single",
            )
            per_bus_outputs.append(
                {
                    "route_index": record.route_index,
                    "person_id": record.person_id,
                    "output_image": os.path.abspath(output_path),
                    "matched_roads": len(single_matched),
                    "missing_roads": len(single_missing),
                    "view_bbox": {
                        "min_x": single_bbox[0],
                        "min_y": single_bbox[1],
                        "max_x": single_bbox[2],
                        "max_y": single_bbox[3],
                    },
                }
            )

    report_json = args.report_json
    if not report_json:
        base, _ext = os.path.splitext(args.output)
        report_json = f"{base}.report.json"

    report = {
        "net_file": os.path.abspath(args.net_file),
        "planned_routes_json": os.path.abspath(args.planned_routes_json),
        "added_bus_json": os.path.abspath(args.added_bus_json) if args.added_bus_json else "",
        "bus_ids_json": os.path.abspath(args.bus_ids_json) if args.bus_ids_json else "",
        "output_image": os.path.abspath(args.output),
        "per_bus_dir": os.path.abspath(per_bus_dir) if per_bus_dir else "",
        "view": args.view,
        "padding": args.padding,
        "counts": {
            "bus_routes": len(records),
            "matched_roads": len(matched_road_ids),
            "missing_roads": len(missing_road_ids),
            "per_bus_images": len(per_bus_outputs),
        },
        "bus_routes": [
            {
                "route_index": record.route_index,
                "person_id": record.person_id,
                "departure_time": record.departure_time,
                "start_lane_id": record.start_lane_id,
                "end_lane_id": record.end_lane_id,
                "road_ids": list(record.road_ids),
            }
            for record in records
        ],
        "per_bus_outputs": per_bus_outputs,
        "missing_road_ids": sorted(missing_road_ids),
        "view_bbox": {
            "min_x": view_bbox[0],
            "min_y": view_bbox[1],
            "max_x": view_bbox[2],
            "max_y": view_bbox[3],
        },
    }
    with open(report_json, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"总公交路径数: {len(records)}")
    print(f"成功匹配道路数: {len(matched_road_ids)}")
    print(f"未匹配道路数: {len(missing_road_ids)}")
    print(f"图片已保存: {os.path.abspath(args.output)}")
    if per_bus_dir:
        print(f"单公交图片目录: {os.path.abspath(per_bus_dir)}")
    print(f"报告已保存: {os.path.abspath(report_json)}")


if __name__ == "__main__":
    main()
