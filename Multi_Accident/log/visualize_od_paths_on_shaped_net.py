#!/usr/bin/env python3
"""Visualize OD start/end and candidate paths on Shaped.net.xml.

Usage examples:
  python3 visualize_od_paths_on_shaped_net.py
  python3 visualize_od_paths_on_shaped_net.py --hospital_id 200002126 --accident_id 200002141
  python3 visualize_od_paths_on_shaped_net.py --hospital_id 200002126 --accident_id 200002141 --show_edge_ids
"""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


Point = Tuple[float, float]


@dataclass
class EdgeGeom:
    edge_id: str
    points: List[Point]


def parse_shape(shape_text: str) -> List[Point]:
    points: List[Point] = []
    for token in shape_text.strip().split():
        x_text, y_text = token.split(",")
        points.append((float(x_text), float(y_text)))
    return points


def edge_midpoint(points: Sequence[Point]) -> Point:
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]

    seg_lengths: List[float] = []
    total = 0.0
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        seg = math.hypot(dx, dy)
        seg_lengths.append(seg)
        total += seg

    if total <= 1e-9:
        return points[len(points) // 2]

    target = total / 2.0
    acc = 0.0
    for i, seg in enumerate(seg_lengths, start=1):
        if acc + seg >= target:
            ratio = (target - acc) / seg if seg > 0 else 0.0
            x = points[i - 1][0] + ratio * (points[i][0] - points[i - 1][0])
            y = points[i - 1][1] + ratio * (points[i][1] - points[i - 1][1])
            return (x, y)
        acc += seg

    return points[-1]


def load_edge_geometries(net_xml_path: Path) -> Dict[str, EdgeGeom]:
    edge_geoms: Dict[str, EdgeGeom] = {}

    # iterparse keeps memory usage reasonable for large SUMO nets.
    for event, elem in ET.iterparse(str(net_xml_path), events=("end",)):
        if elem.tag != "edge":
            continue

        edge_id = elem.get("id")
        if not edge_id:
            elem.clear()
            continue

        # Skip internal edges like :300000000_0
        if edge_id.startswith(":"):
            elem.clear()
            continue

        lane_with_shape = None
        for lane in elem.findall("lane"):
            shape_text = lane.get("shape")
            if shape_text:
                lane_with_shape = shape_text
                break

        if lane_with_shape:
            try:
                points = parse_shape(lane_with_shape)
                if points:
                    edge_geoms[edge_id] = EdgeGeom(edge_id=edge_id, points=points)
            except Exception:
                pass

        elem.clear()

    return edge_geoms


def choose_default_od(paths_data: Dict) -> Tuple[str, str]:
    for hospital_id, accident_map in paths_data.items():
        if not isinstance(accident_map, dict):
            continue
        for accident_id, candidates in accident_map.items():
            if isinstance(candidates, list) and candidates:
                return str(hospital_id), str(accident_id)
    raise RuntimeError("No valid OD found in shortest_paths.json")


def collect_paths_for_od(paths_data: Dict, hospital_id: str, accident_id: str) -> List[List[str]]:
    candidates = paths_data.get(hospital_id, {}).get(accident_id)
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(f"OD not found or empty: {hospital_id} -> {accident_id}")

    od_paths: List[List[str]] = []
    for row in candidates:
        path = row.get("path", []) if isinstance(row, dict) else []
        if isinstance(path, list) and len(path) >= 2:
            od_paths.append([str(e) for e in path])

    if not od_paths:
        raise RuntimeError(f"OD has no usable path list: {hospital_id} -> {accident_id}")
    return od_paths


def collect_all_od_pairs(paths_data: Dict) -> List[Tuple[str, str]]:
    all_pairs: List[Tuple[str, str]] = []
    for hospital_id, accident_map in paths_data.items():
        if not isinstance(accident_map, dict):
            continue
        for accident_id, candidates in accident_map.items():
            if isinstance(candidates, list) and candidates:
                all_pairs.append((str(hospital_id), str(accident_id)))
    if not all_pairs:
        raise RuntimeError("No valid OD pairs found in shortest_paths.json")
    return all_pairs


def sanitize_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)


def draw_base_map(ax, edge_geoms: Dict[str, EdgeGeom]) -> None:
    for geom in edge_geoms.values():
        xs = [p[0] for p in geom.points]
        ys = [p[1] for p in geom.points]
        ax.plot(xs, ys, color="#d0d7de", linewidth=0.4, alpha=0.7, zorder=1)


def draw_od_paths(
    ax,
    edge_geoms: Dict[str, EdgeGeom],
    od_paths: List[List[str]],
    show_edge_ids: bool,
    line_alpha: float = 0.95,
    line_width: float = 2.0,
) -> Tuple[bool, set[str]]:
    palette = ["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf"]
    drawn_any = False
    missing_edges: set[str] = set()

    for idx, path in enumerate(od_paths, start=1):
        color = palette[(idx - 1) % len(palette)]
        for edge_id in path:
            geom = edge_geoms.get(edge_id)
            if not geom:
                missing_edges.add(edge_id)
                continue
            xs = [p[0] for p in geom.points]
            ys = [p[1] for p in geom.points]
            ax.plot(xs, ys, color=color, linewidth=line_width, alpha=line_alpha, zorder=3)
            drawn_any = True

        start_edge = path[0]
        end_edge = path[-1]
        start_geom = edge_geoms.get(start_edge)
        end_geom = edge_geoms.get(end_edge)

        if start_geom:
            sx, sy = edge_midpoint(start_geom.points)
            ax.scatter([sx], [sy], s=55, marker="o", color="#22c55e", edgecolors="black", linewidths=0.4, zorder=5)
            if show_edge_ids:
                ax.text(sx, sy, f"S:{start_edge}", fontsize=7, color="#166534", zorder=6)

        if end_geom:
            ex, ey = edge_midpoint(end_geom.points)
            ax.scatter([ex], [ey], s=65, marker="X", color="#ef4444", edgecolors="black", linewidths=0.4, zorder=5)
            if show_edge_ids:
                ax.text(ex, ey, f"E:{end_edge}", fontsize=7, color="#7f1d1d", zorder=6)

    return drawn_any, missing_edges


def render_single_od(
    edge_geoms: Dict[str, EdgeGeom],
    hospital_id: str,
    accident_id: str,
    od_paths: List[List[str]],
    show_edge_ids: bool,
    output_path: Path,
) -> Tuple[bool, set[str]]:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
    draw_base_map(ax, edge_geoms)
    drawn_any, missing_edges = draw_od_paths(ax, edge_geoms, od_paths, show_edge_ids=show_edge_ids)
    if not drawn_any:
        plt.close(fig)
        return False, missing_edges

    title = f"OD Path Visualization | hospital={hospital_id} -> accident={accident_id} | paths={len(od_paths)}"
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_aspect("equal", adjustable="box")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return True, missing_edges


def render_combined_all_ods(
    edge_geoms: Dict[str, EdgeGeom],
    all_od_paths: List[Tuple[str, str, List[List[str]]]],
    output_path: Path,
) -> Tuple[bool, set[str]]:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(14, 10), dpi=160)
    draw_base_map(ax, edge_geoms)

    drawn_any = False
    missing_edges: set[str] = set()
    for _, _, od_paths in all_od_paths:
        od_drawn, od_missing = draw_od_paths(
            ax,
            edge_geoms,
            od_paths,
            show_edge_ids=False,
            line_alpha=0.45,
            line_width=1.1,
        )
        drawn_any = drawn_any or od_drawn
        missing_edges.update(od_missing)

    if not drawn_any:
        plt.close(fig)
        return False, missing_edges

    ax.set_title(f"All OD Paths Overview | od_count={len(all_od_paths)}", fontsize=11)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_aspect("equal", adjustable="box")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)
    return True, missing_edges


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Visualize OD points and paths on Shaped.net.xml")
    parser.add_argument("--net_xml", default=str(script_dir / "Shaped.net.xml"))
    parser.add_argument("--paths_json", default=str(script_dir / "shortest_paths.json"))
    parser.add_argument("--hospital_id", default=None)
    parser.add_argument("--accident_id", default=None)
    parser.add_argument("--max_paths", type=int, default=3, help="How many candidate paths to draw")
    parser.add_argument("--show_edge_ids", action="store_true", help="Label start/end edge IDs")
    parser.add_argument("--all_ods", action="store_true", help="Render one image for every OD in shortest_paths.json")
    parser.add_argument("--combined_all", action="store_true", help="Also render one overview image that overlays all ODs")
    parser.add_argument(
        "--all_output_dir",
        default=str(script_dir / "visualization" / "all_od_paths"),
        help="Output directory for --all_ods mode",
    )
    parser.add_argument("--output", default=str(script_dir / "visualization" / "od_paths_on_shaped_net.png"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    net_xml_path = Path(args.net_xml)
    paths_json_path = Path(args.paths_json)
    output_path = Path(args.output)

    if not net_xml_path.exists():
        raise FileNotFoundError(f"net xml not found: {net_xml_path}")
    if not paths_json_path.exists():
        raise FileNotFoundError(f"paths json not found: {paths_json_path}")

    with paths_json_path.open("r", encoding="utf-8") as f:
        paths_data = json.load(f)

    edge_geoms = load_edge_geometries(net_xml_path)

    try:
        import matplotlib.pyplot as plt  # noqa: F401
    except Exception as exc:
        raise RuntimeError("matplotlib is required. Install with: pip install matplotlib") from exc

    if args.all_ods:
        all_output_dir = Path(args.all_output_dir)
        all_pairs = collect_all_od_pairs(paths_data)
        all_missing: set[str] = set()
        rendered_count = 0
        all_od_for_combined: List[Tuple[str, str, List[List[str]]]] = []

        for hospital_id, accident_id in all_pairs:
            od_paths = collect_paths_for_od(paths_data, hospital_id, accident_id)
            od_paths = od_paths[: max(1, int(args.max_paths))]
            all_od_for_combined.append((hospital_id, accident_id, od_paths))

            out_name = f"od_{sanitize_name(hospital_id)}__{sanitize_name(accident_id)}.png"
            out_path = all_output_dir / out_name
            drawn, missing = render_single_od(
                edge_geoms=edge_geoms,
                hospital_id=hospital_id,
                accident_id=accident_id,
                od_paths=od_paths,
                show_edge_ids=args.show_edge_ids,
                output_path=out_path,
            )
            all_missing.update(missing)
            if drawn:
                rendered_count += 1

        if rendered_count == 0:
            raise RuntimeError("No path segments were drawn for all ODs. Check edge IDs and net xml.")

        print(f"Saved {rendered_count} OD images to: {all_output_dir}")

        if args.combined_all:
            combined_out = output_path
            combined_drawn, combined_missing = render_combined_all_ods(
                edge_geoms=edge_geoms,
                all_od_paths=all_od_for_combined,
                output_path=combined_out,
            )
            all_missing.update(combined_missing)
            if not combined_drawn:
                raise RuntimeError("Combined overview draw failed: no segments were rendered")
            print(f"Saved combined overview: {combined_out}")

        if all_missing:
            print(f"[WARN] missing edges in net xml: {len(all_missing)} (showing first 15)")
            print(",".join(sorted(all_missing)[:15]))
        return 0

    hospital_id = str(args.hospital_id) if args.hospital_id is not None else None
    accident_id = str(args.accident_id) if args.accident_id is not None else None
    if hospital_id is None or accident_id is None:
        hospital_id, accident_id = choose_default_od(paths_data)

    od_paths = collect_paths_for_od(paths_data, hospital_id, accident_id)
    od_paths = od_paths[: max(1, int(args.max_paths))]

    drawn, missing_edges = render_single_od(
        edge_geoms=edge_geoms,
        hospital_id=hospital_id,
        accident_id=accident_id,
        od_paths=od_paths,
        show_edge_ids=args.show_edge_ids,
        output_path=output_path,
    )
    if not drawn:
        raise RuntimeError("No path segments were drawn. Check edge IDs in shortest_paths.json vs Shaped.net.xml")

    if missing_edges:
        print(f"[WARN] missing edges in net xml: {len(missing_edges)} (showing first 15)")
        print(",".join(sorted(missing_edges)[:15]))

    print(f"Saved visualization: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
