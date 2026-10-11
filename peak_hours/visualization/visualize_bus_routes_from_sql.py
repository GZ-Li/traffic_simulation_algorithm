#!/usr/bin/env python3
"""
从 PostgreSQL 的 *_s_cars 表回放公交实际经过的道路序列，并绘制到 SUMO 底图上。

适用场景：
- direct_od 模式下 planned_routes.json 中的 resolved_route_road_ids 为空；
- 但仿真已开启 output_sql_dsn，且 *_s_cars 表已落盘；
- 希望按“实际经过的 road_id 序列”可视化公交路径。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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

_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "wuhan_simulator_matplotlib_cache")
os.makedirs(os.path.join(_CACHE_ROOT, "mpl"), exist_ok=True)
os.makedirs(os.path.join(_CACHE_ROOT, "xdg"), exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_ROOT, "mpl"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_CACHE_ROOT, "xdg"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

try:
    import psycopg  # type: ignore
except ModuleNotFoundError:
    psycopg = None

try:
    from pyproj import CRS, Transformer  # type: ignore
except ModuleNotFoundError:
    CRS = None
    Transformer = None


@dataclass(frozen=True)
class BusSqlRouteRecord:
    person_id: int
    road_ids: Tuple[int, ...] = ()
    points: Tuple[Tuple[float, float], ...] = ()


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


def _load_json(path: str) -> Any:
    with open(path, "r") as f:
        return json.load(f)


def _load_bus_id_set(path: str) -> Set[int]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 不是 bus_ids.json 格式")
    raw_ids = payload.get("bus_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError(f"{path} 缺少 bus_ids 列表")
    result: Set[int] = set()
    for value in raw_ids:
        maybe_int = _try_int(value)
        if maybe_int is not None:
            result.add(maybe_int)
    return result


def _extract_job_name(log_file: str) -> str:
    pattern = re.compile(r"enable postgresql output to ([A-Za-z0-9_]+)_s_cars\b")
    with open(log_file, "r") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                return match.group(1)
    raise ValueError(f"未能从 {log_file} 解析到 PostgreSQL job 名")


def _build_lonlat_to_sumo_xy_projector(
    net_file: str,
) -> Optional[Callable[[float, float], Tuple[float, float]]]:
    location = ET.parse(net_file).getroot().find("location")
    if location is None:
        return None

    proj_parameter = location.get("projParameter", "")
    net_offset_raw = location.get("netOffset", "")
    if not proj_parameter or not net_offset_raw or Transformer is None or CRS is None:
        return None

    try:
        net_offset_x, net_offset_y = (float(value) for value in net_offset_raw.split(","))
        transformer = Transformer.from_crs(
            "EPSG:4326",
            CRS.from_proj4(proj_parameter),
            always_xy=True,
        )
    except Exception:
        return None

    def _project(lng: float, lat: float) -> Tuple[float, float]:
        projected_x, projected_y = transformer.transform(lng, lat)
        return (projected_x + net_offset_x, projected_y + net_offset_y)

    return _project


def _load_routes_from_sql(
    sql_dsn: str,
    job_name: str,
    bus_ids: Sequence[int],
    net_file: str,
) -> List[BusSqlRouteRecord]:
    if not bus_ids:
        return []

    table_name = f"{job_name}_s_cars"
    table_columns = _load_table_columns(sql_dsn, table_name)
    id_column = _pick_first_existing_column(table_columns, ["id", "person_id", "vehicle_id", "car_id"])
    step_column = _pick_first_existing_column(table_columns, ["step", "t", "time"])
    road_column = _pick_first_existing_column(table_columns, ["road_id", "edge_id", "road"])
    x_column = _pick_first_existing_column(table_columns, ["x", "position_x", "pos_x", "coord_x"])
    y_column = _pick_first_existing_column(table_columns, ["y", "position_y", "pos_y", "coord_y"])
    lng_column = _pick_first_existing_column(table_columns, ["lng", "lon", "longitude"])
    lat_column = _pick_first_existing_column(table_columns, ["lat", "latitude"])

    if not id_column or not step_column:
        raise ValueError(f"{table_name} 缺少必要列。当前列: {table_columns}")

    if road_column:
        ordered_routes: Dict[int, List[int]] = {}
        rows = _query_sql_rows(
            sql_dsn=sql_dsn,
            table_name=table_name,
            select_columns=(id_column, road_column, step_column),
            where_clause=f"{id_column} = ANY(%s) AND {road_column} IS NOT NULL",
            bus_ids=bus_ids,
            order_by=(id_column, step_column),
        )
        for person_id, road_id, _step in rows:
            pid = _try_int(person_id)
            rid = _try_int(road_id)
            if pid is None or rid is None:
                continue
            ordered_routes.setdefault(pid, [])
            if not ordered_routes[pid] or ordered_routes[pid][-1] != rid:
                ordered_routes[pid].append(rid)

        return [
            BusSqlRouteRecord(person_id=person_id, road_ids=tuple(road_ids))
            for person_id, road_ids in ordered_routes.items()
            if road_ids
        ]

    if x_column and y_column:
        ordered_points: Dict[int, List[Tuple[float, float]]] = {}
        rows = _query_sql_rows(
            sql_dsn=sql_dsn,
            table_name=table_name,
            select_columns=(id_column, x_column, y_column, step_column),
            where_clause=f"{id_column} = ANY(%s) AND {x_column} IS NOT NULL AND {y_column} IS NOT NULL",
            bus_ids=bus_ids,
            order_by=(id_column, step_column),
        )
        for person_id, x_value, y_value, _step in rows:
            pid = _try_int(person_id)
            if pid is None:
                continue
            try:
                point = (float(x_value), float(y_value))
            except (TypeError, ValueError):
                continue
            ordered_points.setdefault(pid, [])
            if not ordered_points[pid] or ordered_points[pid][-1] != point:
                ordered_points[pid].append(point)

        return [
            BusSqlRouteRecord(person_id=person_id, points=tuple(points))
            for person_id, points in ordered_points.items()
            if points
        ]

    if lng_column and lat_column:
        projector = _build_lonlat_to_sumo_xy_projector(net_file)
        if projector is None:
            raise ValueError(
                f"{table_name} 只有 lng/lat 列，但无法从 {net_file} 构造经纬度到 SUMO 坐标的投影器"
            )

        ordered_points: Dict[int, List[Tuple[float, float]]] = {}
        rows = _query_sql_rows(
            sql_dsn=sql_dsn,
            table_name=table_name,
            select_columns=(id_column, lng_column, lat_column, step_column),
            where_clause=f"{id_column} = ANY(%s) AND {lng_column} IS NOT NULL AND {lat_column} IS NOT NULL",
            bus_ids=bus_ids,
            order_by=(id_column, step_column),
        )
        for person_id, lng_value, lat_value, _step in rows:
            pid = _try_int(person_id)
            if pid is None:
                continue
            try:
                point = projector(float(lng_value), float(lat_value))
            except (TypeError, ValueError):
                continue
            ordered_points.setdefault(pid, [])
            if not ordered_points[pid] or ordered_points[pid][-1] != point:
                ordered_points[pid].append(point)

        return [
            BusSqlRouteRecord(person_id=person_id, points=tuple(points))
            for person_id, points in ordered_points.items()
            if points
        ]

    raise ValueError(
        f"{table_name} 中既没有可用的道路列，也没有可用的轨迹点列。当前列: {table_columns}"
    )


def _pick_first_existing_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    column_set = set(columns)
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    return None


def _load_table_columns(sql_dsn: str, table_name: str) -> List[str]:
    query = (
        "SELECT column_name "
        "FROM information_schema.columns "
        "WHERE table_name = %s "
        "ORDER BY ordinal_position"
    )
    if psycopg is not None:
        with psycopg.connect(sql_dsn) as conn:  # type: ignore[union-attr]
            with conn.cursor() as cur:
                cur.execute(query, (table_name,))
                rows = cur.fetchall()
        return [str(row[0]) for row in rows]

    psql_path = shutil.which("psql")
    if not psql_path:
        raise RuntimeError(
            "当前环境既没有 psycopg，也没有可执行的 psql 命令，无法查询 PostgreSQL"
        )

    escaped_table_name = table_name.replace("'", "''")
    psql_query = (
        "COPY ("
        "SELECT column_name "
        "FROM information_schema.columns "
        f"WHERE table_name = '{escaped_table_name}' "
        "ORDER BY ordinal_position"
        ") TO STDOUT WITH CSV"
    )
    result = subprocess.run(
        [psql_path, sql_dsn, "-v", "ON_ERROR_STOP=1", "-c", psql_query],
        check=True,
        capture_output=True,
        text=True,
    )
    reader = csv.reader(result.stdout.splitlines())
    return [row[0] for row in reader if row]


def _query_sql_rows(
    sql_dsn: str,
    table_name: str,
    select_columns: Sequence[str],
    where_clause: str,
    bus_ids: Sequence[int],
    order_by: Sequence[str],
) -> List[Tuple[Any, ...]]:
    if psycopg is not None:
        query = (
            f"SELECT {', '.join(select_columns)} "
            f"FROM {table_name} "
            f"WHERE {where_clause} "
            f"ORDER BY {', '.join(order_by)}"
        )
        with psycopg.connect(sql_dsn) as conn:  # type: ignore[union-attr]
            with conn.cursor() as cur:
                cur.execute(query, (list(bus_ids),))
                return cur.fetchall()

    return _query_sql_rows_via_psql(
        sql_dsn=sql_dsn,
        table_name=table_name,
        select_columns=select_columns,
        where_clause=where_clause.replace("%s", f"ARRAY[{','.join(str(bus_id) for bus_id in bus_ids)}]"),
        order_by=order_by,
    )


def _query_sql_rows_via_psql(
    sql_dsn: str,
    table_name: str,
    select_columns: Sequence[str],
    where_clause: str,
    order_by: Sequence[str],
) -> List[Tuple[Any, ...]]:
    psql_path = shutil.which("psql")
    if not psql_path:
        raise RuntimeError(
            "当前环境既没有 psycopg，也没有可执行的 psql 命令，无法查询 PostgreSQL"
        )

    query = (
        f"COPY ("
        f"SELECT {', '.join(select_columns)} "
        f"FROM {table_name} "
        f"WHERE {where_clause} "
        f"ORDER BY {', '.join(order_by)}"
        f") TO STDOUT WITH CSV"
    )
    result = subprocess.run(
        [psql_path, sql_dsn, "-v", "ON_ERROR_STOP=1", "-c", query],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: List[Tuple[Any, ...]] = []
    reader = csv.reader(result.stdout.splitlines())
    for row in reader:
        rows.append(tuple(row))
    return rows


def _compute_bbox(net: NetGeometry, records: Sequence[BusSqlRouteRecord]) -> Optional[Tuple[float, float, float, float]]:
    points: List[Tuple[float, float]] = []
    for record in records:
        if record.road_ids:
            for road_id in record.road_ids:
                edge = net.edges.get(road_id)
                if edge is None:
                    continue
                points.extend(edge.shape)
        elif record.points:
            points.extend(record.points)
    return _bbox_from_points(points)


def _sample_records(records: Sequence[BusSqlRouteRecord], max_labels: int) -> List[BusSqlRouteRecord]:
    if max_labels <= 0 or len(records) <= max_labels:
        return list(records)
    if max_labels == 1:
        return [records[0]]

    sampled: List[BusSqlRouteRecord] = []
    last_idx = len(records) - 1
    for slot in range(max_labels):
        pos = round(slot * last_idx / (max_labels - 1))
        sampled.append(records[pos])

    deduped: List[BusSqlRouteRecord] = []
    seen: Set[int] = set()
    for record in sampled:
        if record.person_id in seen:
            continue
        seen.add(record.person_id)
        deduped.append(record)
    return deduped


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 PostgreSQL *_s_cars 表可视化公交真实路径")
    parser.add_argument("--net-file", required=True, help="SUMO net.xml 文件路径")
    parser.add_argument("--sql-dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument("--job-log-file", required=True, help="包含 sidecar/pg 输出的 logrus.log 路径")
    parser.add_argument("--bus-ids-json", required=True, help="bus_ids.json 路径")
    parser.add_argument("--output", required=True, help="输出图片路径")
    parser.add_argument("--report-json", default="", help="输出报告路径，默认同名 .report.json")
    parser.add_argument("--view", choices=["selection", "all"], default="selection", help="视图范围")
    parser.add_argument("--padding", type=float, default=300.0, help="selection 视图外扩边距")
    parser.add_argument("--figsize", default="16,11", help="画布大小 width,height")
    parser.add_argument("--dpi", type=int, default=180, help="输出 DPI")
    parser.add_argument("--route-alpha", type=float, default=0.35, help="路径透明度")
    parser.add_argument("--route-linewidth", type=float, default=1.8, help="路径线宽")
    parser.add_argument("--label-routes", action="store_true", help="给部分路径标注 bus id")
    parser.add_argument("--max-route-labels", type=int, default=20, help="最多标注多少条路径")
    parser.add_argument("--title", default="", help="标题")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    net = parse_sumo_net(args.net_file)
    bus_ids = sorted(_load_bus_id_set(args.bus_ids_json))
    if not bus_ids:
        raise ValueError("bus_ids.json 中没有 bus_ids")

    job_name = _extract_job_name(args.job_log_file)
    records = _load_routes_from_sql(args.sql_dsn, job_name, bus_ids, args.net_file)
    if not records:
        raise ValueError("没有从 PostgreSQL *_s_cars 表中解析到任何公交路径")

    selected_bbox = _compute_bbox(net, records)
    if args.view == "selection" and selected_bbox is not None:
        view_bbox = _expand_bbox(selected_bbox, args.padding)
    else:
        view_bbox = net.bbox

    visible_edges = {
        edge_id: edge
        for edge_id, edge in net.edges.items()
        if _edge_intersects_bbox(edge, view_bbox)
    }

    figsize = _parse_figsize(args.figsize)
    fig, ax = plt.subplots(figsize=figsize, dpi=args.dpi)

    for edge in visible_edges.values():
        _plot_edge(ax, edge, color=DEFAULT_BASE_ROAD_COLOR, linewidth=0.7, alpha=0.55, zorder=1)

    cmap = plt.get_cmap("tab20")
    matched_road_ids: Set[int] = set()
    missing_road_ids: Set[int] = set()
    plotted_from_points = 0

    for idx, record in enumerate(records):
        color = cmap(idx % 20)
        if record.road_ids:
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
                    linewidth=args.route_linewidth,
                    alpha=args.route_alpha,
                    zorder=3,
                )
        elif record.points:
            xs = [point[0] for point in record.points]
            ys = [point[1] for point in record.points]
            ax.plot(
                xs,
                ys,
                color=color,
                linewidth=args.route_linewidth,
                alpha=args.route_alpha,
                zorder=4,
            )
            plotted_from_points += 1

    if args.label_routes:
        for record in _sample_records(records, args.max_route_labels):
            if record.road_ids:
                anchor_road_id = record.road_ids[len(record.road_ids) // 2]
                edge = visible_edges.get(anchor_road_id) or net.edges.get(anchor_road_id)
                if edge is None:
                    continue
                _annotate_edge(ax, edge, color="#111827", fontsize=7)
                x, y = edge.midpoint
            elif record.points:
                x, y = record.points[len(record.points) // 2]
            else:
                continue
            ax.text(
                x,
                y,
                f"bus {record.person_id}",
                fontsize=7,
                color="#111827",
                ha="center",
                va="bottom",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.9, "edgecolor": "none"},
                zorder=6,
            )

    min_x, min_y, max_x, max_y = view_bbox
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect("equal")
    ax.set_xlabel("SUMO X")
    ax.set_ylabel("SUMO Y")
    ax.set_title(args.title or f"Bus Route Replay From SQL | buses={len(records)} roads={len(matched_road_ids)}")
    ax.grid(True, linestyle="--", alpha=0.25)
    ax.legend(
        handles=[
            Line2D([0], [0], color=DEFAULT_BASE_ROAD_COLOR, linewidth=1.2, label="Base Roads"),
            Line2D([0], [0], color="#2563EB", linewidth=2.0, label="Bus Routes From SQL"),
        ],
        loc="upper right",
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    plt.tight_layout()
    plt.savefig(args.output, bbox_inches="tight")
    plt.close(fig)

    report_json = args.report_json or f"{os.path.splitext(args.output)[0]}.report.json"
    report = {
        "net_file": os.path.abspath(args.net_file),
        "sql_dsn": args.sql_dsn,
        "job_name": job_name,
        "job_log_file": os.path.abspath(args.job_log_file),
        "bus_ids_json": os.path.abspath(args.bus_ids_json),
        "output_image": os.path.abspath(args.output),
        "counts": {
            "bus_routes": len(records),
            "matched_roads": len(matched_road_ids),
            "missing_roads": len(missing_road_ids),
            "plotted_from_points": plotted_from_points,
        },
        "bus_routes": [
            {
                "person_id": record.person_id,
                "road_ids": list(record.road_ids),
                "points": [list(point) for point in record.points],
            }
            for record in records
        ],
        "missing_road_ids": sorted(missing_road_ids),
    }
    with open(report_json, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"job_name: {job_name}")
    print(f"总公交路径数: {len(records)}")
    print(f"成功匹配道路数: {len(matched_road_ids)}")
    print(f"未匹配道路数: {len(missing_road_ids)}")
    print(f"按轨迹点绘制的公交数: {plotted_from_points}")
    print(f"图片已保存: {os.path.abspath(args.output)}")
    print(f"报告已保存: {os.path.abspath(report_json)}")


if __name__ == "__main__":
    main()
