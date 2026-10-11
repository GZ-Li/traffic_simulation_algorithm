#!/usr/bin/env python3
"""Visualize hospital-accident assignment and parallel vs serial span.

Inputs (same folder):
- optimization_result.json
- time_matrix.json
- optional: eta_res.json

Optional map background source:
- ../../Shaped.net.xml

Outputs (same folder):
- assignment_map.png
- span_gantt.png
- assignment_span_dashboard.png
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import matplotlib.pyplot as plt


ColorMap = Dict[str, str]
Assignments = Dict[str, List[str]]
ResponseTimes = Dict[str, Dict[str, float]]


def _edge_id_from_label(label: str) -> str:
    # Accept labels like "Hospital 200000480" or "Accident 200000387".
    return label.split()[-1]


def _parse_lane_shape(shape_text: str) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for part in shape_text.strip().split():
        if "," not in part:
            continue
        x_str, y_str = part.split(",", 1)
        points.append((float(x_str), float(y_str)))
    return points


def _edge_midpoint_from_points(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not points:
        raise ValueError("empty points")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def read_edge_positions(net_xml: Path) -> Tuple[Dict[str, Tuple[float, float]], List[List[Tuple[float, float]]]]:
    """Return edge midpoint positions and a lightweight polyline background.

    edge_positions: edge_id -> (x, y)
    bg_lines: list of edge polylines for plotting the road background
    """
    edge_positions: Dict[str, Tuple[float, float]] = {}
    bg_lines: List[List[Tuple[float, float]]] = []

    tree = ET.parse(net_xml)
    root = tree.getroot()

    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":"):
            continue

        lane = edge.find("lane")
        if lane is None:
            continue

        shape_text = lane.attrib.get("shape", "")
        points = _parse_lane_shape(shape_text)
        if len(points) < 2:
            continue

        bg_lines.append(points)
        edge_positions[edge_id] = _edge_midpoint_from_points(points)

    return edge_positions, bg_lines


def build_parallel_tasks(assignments: Assignments, response_times: ResponseTimes) -> List[Dict[str, object]]:
    tasks: List[Dict[str, object]] = []
    for hospital, accidents in assignments.items():
        t = 0.0
        for idx, accident in enumerate(accidents, start=1):
            dur = float(response_times[hospital][accident])
            start = t
            end = t + dur
            tasks.append(
                {
                    "resource": hospital,
                    "accident": accident,
                    "order": idx,
                    "start": start,
                    "end": end,
                    "duration": dur,
                }
            )
            t = end
    return tasks


def makespan(tasks: Iterable[Dict[str, object]]) -> float:
    end_times = [float(t["end"]) for t in tasks]
    return max(end_times) if end_times else 0.0


def plot_assignment_map(
    ax: plt.Axes,
    title: str,
    assignments: Assignments,
    edge_positions: Dict[str, Tuple[float, float]],
    bg_lines: List[List[Tuple[float, float]]],
    color_map: ColorMap,
) -> None:
    # Road network background
    for line in bg_lines:
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        ax.plot(xs, ys, color="#9ca3af", linewidth=0.6, alpha=0.65, zorder=0)

    used_hospitals = list(assignments.keys())
    used_accidents = sorted({a for accs in assignments.values() for a in accs})

    for hospital in used_hospitals:
        hid = _edge_id_from_label(hospital)
        if hid not in edge_positions:
            continue
        hx, hy = edge_positions[hid]
        c = color_map[hospital]

        ax.scatter(
            [hx],
            [hy],
            s=140,
            marker="s",
            color=c,
            edgecolors="black",
            linewidths=0.8,
            zorder=4,
        )
        ax.text(hx, hy + 15, hid, fontsize=8, color=c, ha="center", va="bottom")

        accidents = assignments.get(hospital, [])
        for order_idx, accident in enumerate(accidents, start=1):
            aid = _edge_id_from_label(accident)
            if aid not in edge_positions:
                continue
            axx, axy = edge_positions[aid]

            ax.scatter(
                [axx],
                [axy],
                s=120,
                marker="X",
                color="#f97316",
                edgecolors="black",
                linewidths=0.6,
                zorder=5,
            )
            ax.text(axx, axy + 15, aid, fontsize=8, color="#9a3412", ha="center", va="bottom")

            ax.annotate(
                "",
                xy=(axx, axy),
                xytext=(hx, hy),
                arrowprops=dict(arrowstyle="->", lw=1.4, color=c, alpha=0.85),
                zorder=3,
            )

            mx = (hx + axx) / 2.0
            my = (hy + axy) / 2.0
            ax.text(
                mx,
                my,
                str(order_idx),
                fontsize=8,
                color="white",
                ha="center",
                va="center",
                bbox=dict(boxstyle="circle,pad=0.15", facecolor=c, edgecolor="none", alpha=0.95),
                zorder=6,
            )

    ax.set_title(title, fontsize=13, pad=10)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor("#f8fafc")

    # Zoom to used entities only
    xy = []
    for h in used_hospitals:
        hid = _edge_id_from_label(h)
        if hid in edge_positions:
            xy.append(edge_positions[hid])
    for a in used_accidents:
        aid = _edge_id_from_label(a)
        if aid in edge_positions:
            xy.append(edge_positions[aid])

    if xy:
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)
        pad_x = max(80.0, 0.35 * x_span)
        pad_y = max(80.0, 0.35 * y_span)
        ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
        ax.set_ylim(min(ys) - pad_y, max(ys) + pad_y)


def plot_span_gantt(
    ax_parallel: plt.Axes,
    ax_greedy: plt.Axes,
    tasks_parallel: List[Dict[str, object]],
    tasks_greedy: List[Dict[str, object]],
    color_map: ColorMap,
) -> Tuple[float, float]:
    # Parallel chart
    hospitals = sorted({str(t["resource"]) for t in tasks_parallel})
    y_step = 0.62
    y_map_parallel = {h: i * y_step for i, h in enumerate(hospitals)}
    parallel_bar_h = 0.46

    for t in tasks_parallel:
        h = str(t["resource"])
        y = y_map_parallel[h]
        c = color_map[h]
        start = float(t["start"])
        dur = float(t["duration"])
        end = float(t["end"])
        aid = _edge_id_from_label(str(t["accident"]))

        ax_parallel.barh(y=y, width=dur, left=start, height=parallel_bar_h, color=c, alpha=0.9, edgecolor="white")
        ax_parallel.text(start + dur / 2.0, y, aid, ha="center", va="center", fontsize=8, color="white")
        ax_parallel.text(end + 3, y, f"{int(end)}s", ha="left", va="center", fontsize=8, color="#334155")

    parallel_span = makespan(tasks_parallel)
    ax_parallel.axvline(parallel_span, color="#0f172a", linestyle="--", linewidth=1.2)
    ax_parallel.set_yticks(list(y_map_parallel.values()), list(y_map_parallel.keys()))
    if hospitals:
        max_y = (len(hospitals) - 1) * y_step
        ax_parallel.set_ylim(-parallel_bar_h * 0.75, max_y + parallel_bar_h * 0.75)
    ax_parallel.set_xlabel("Time (s)")
    ax_parallel.set_title(f"Parallel Dispatch (span = {int(parallel_span)}s)", fontsize=12)
    ax_parallel.grid(axis="x", linestyle=":", alpha=0.35)
    ax_parallel.set_facecolor("#f8fafc")

    # Greedy chart (this case is serial with span=390s)
    greedy_hospitals = sorted({str(t["resource"]) for t in tasks_greedy})
    y_map_greedy = {h: i * y_step for i, h in enumerate(greedy_hospitals)}
    greedy_bar_h = 0.44
    for t in tasks_greedy:
        h = str(t["resource"])
        y = y_map_greedy[h]
        c = color_map[h]
        start = float(t["start"])
        dur = float(t["duration"])
        aid = _edge_id_from_label(str(t["accident"]))
        end = float(t["end"])
        ax_greedy.barh(y=y, width=dur, left=start, height=greedy_bar_h, color=c, alpha=0.9, edgecolor="white")
        ax_greedy.text(start + dur / 2.0, y, aid, ha="center", va="center", fontsize=8, color="white")
        ax_greedy.text(end + 3, y, f"{int(end)}s", ha="left", va="center", fontsize=8, color="#334155")

    greedy_span = makespan(tasks_greedy)
    ax_greedy.axvline(greedy_span, color="#7f1d1d", linestyle="--", linewidth=1.3)
    ax_greedy.set_yticks(list(y_map_greedy.values()), [f"Greedy {h}" for h in y_map_greedy.keys()])
    if greedy_hospitals:
        max_y = (len(greedy_hospitals) - 1) * y_step
        ax_greedy.set_ylim(-greedy_bar_h * 0.75, max_y + greedy_bar_h * 0.75)
    ax_greedy.set_xlabel("Time (s)")
    ax_greedy.set_title(f"Greedy Dispatch (serial in this case, span = {int(greedy_span)}s)", fontsize=12)
    ax_greedy.grid(axis="x", linestyle=":", alpha=0.35)
    ax_greedy.set_facecolor("#fff7ed")

    # Make x-axis comparable
    xmax = max(parallel_span, greedy_span) * 1.08
    ax_parallel.set_xlim(0, xmax)
    ax_greedy.set_xlim(0, xmax)

    return parallel_span, greedy_span


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    opt_file = base_dir / "optimization_result.json"
    tm_file = base_dir / "time_matrix.json"
    net_file = (base_dir / "../../Shaped.net.xml").resolve()

    with opt_file.open("r", encoding="utf-8") as f:
        opt = json.load(f)

    with tm_file.open("r", encoding="utf-8") as f:
        response_times: ResponseTimes = json.load(f)

    optimized_assignment: Assignments = opt.get("optimized_assignment", {})
    greedy_assignment: Assignments = opt.get("greedy_assignment", {})

    # Use all hospitals in response matrix to keep color stable.
    hospitals = list(response_times.keys())
    palette = ["#0f766e", "#1d4ed8", "#b45309", "#be123c", "#6d28d9", "#0f766e"]
    color_map: ColorMap = {h: palette[i % len(palette)] for i, h in enumerate(hospitals)}

    edge_positions, bg_lines = read_edge_positions(net_file)

    # -------------------------------
    # Figure 1: Assignment map compare
    # -------------------------------
    fig_map, axes_map = plt.subplots(1, 2, figsize=(16, 7), dpi=160)
    plot_assignment_map(
        ax=axes_map[0],
        title="Optimized Assignment Map",
        assignments=optimized_assignment,
        edge_positions=edge_positions,
        bg_lines=bg_lines,
        color_map=color_map,
    )
    plot_assignment_map(
        ax=axes_map[1],
        title="Greedy Assignment Map",
        assignments=greedy_assignment,
        edge_positions=edge_positions,
        bg_lines=bg_lines,
        color_map=color_map,
    )

    fig_map.suptitle("Hospital-Accident Spatial Assignment", fontsize=16, y=0.98)
    fig_map.tight_layout()
    map_out = base_dir / "assignment_map.png"
    fig_map.savefig(map_out, bbox_inches="tight")
    plt.close(fig_map)

    # -------------------------------
    # Figure 2: Span Gantt (optimized parallel vs greedy)
    # -------------------------------
    parallel_tasks = build_parallel_tasks(optimized_assignment, response_times)
    greedy_tasks = build_parallel_tasks(greedy_assignment, response_times)

    fig_gantt, axes_gantt = plt.subplots(2, 1, figsize=(14, 8), dpi=160, sharex=True)
    parallel_span, serial_span = plot_span_gantt(
        ax_parallel=axes_gantt[0],
        ax_greedy=axes_gantt[1],
        tasks_parallel=parallel_tasks,
        tasks_greedy=greedy_tasks,
        color_map=color_map,
    )

    ratio = (serial_span / parallel_span) if parallel_span > 0 else math.nan
    fig_gantt.suptitle(
        f"Span Comparison: Serial vs Parallel | {int(serial_span)}s vs {int(parallel_span)}s ({ratio:.2f}x)",
        fontsize=15,
        y=0.98,
    )
    fig_gantt.tight_layout()
    gantt_out = base_dir / "span_gantt.png"
    fig_gantt.savefig(gantt_out, bbox_inches="tight")
    plt.close(fig_gantt)

    # -------------------------------
    # Figure 3: Dashboard
    # -------------------------------
    fig = plt.figure(figsize=(17, 10), dpi=160)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1.0], width_ratios=[1.0, 1.0])

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, :])

    plot_assignment_map(
        ax=ax1,
        title="Optimized Assignment",
        assignments=optimized_assignment,
        edge_positions=edge_positions,
        bg_lines=bg_lines,
        color_map=color_map,
    )
    plot_assignment_map(
        ax=ax2,
        title="Greedy Assignment",
        assignments=greedy_assignment,
        edge_positions=edge_positions,
        bg_lines=bg_lines,
        color_map=color_map,
    )

    # Re-plot compact span bars in dashboard bottom panel.
    ax3.set_facecolor("#f8fafc")
    parallel_rows = sorted({str(t["resource"]) for t in parallel_tasks})
    y_step_dashboard = 0.48
    y_base_parallel = 0.95
    y_map_parallel = {h: y_base_parallel + i * y_step_dashboard for i, h in enumerate(parallel_rows)}
    y_greedy = 0

    for t in parallel_tasks:
        h = str(t["resource"])
        c = color_map[h]
        ax3.barh(y_map_parallel[h], float(t["duration"]), left=float(t["start"]), height=0.30, color=c, edgecolor="white")
    for t in greedy_tasks:
        h = str(t["resource"])
        c = color_map[h]
        ax3.barh(y_greedy, float(t["duration"]), left=float(t["start"]), height=0.30, color=c, edgecolor="white")

    ax3.axvline(parallel_span, linestyle="--", color="#0f172a", linewidth=1.2, label=f"Parallel span: {int(parallel_span)}s")
    ax3.axvline(serial_span, linestyle="--", color="#7f1d1d", linewidth=1.2, label=f"Serial span: {int(serial_span)}s")
    yticks = [0] + [y_map_parallel[h] for h in parallel_rows]
    ylabels = ["Greedy"] + [f"Parallel {h}" for h in parallel_rows]
    ax3.set_yticks(yticks, ylabels)
    if parallel_rows:
        ax3.set_ylim(-0.35, y_map_parallel[parallel_rows[-1]] + 0.35)
    ax3.set_xlabel("Time (s)")
    ax3.set_title("Span Gantt (parallel rows + greedy serial)")
    ax3.grid(axis="x", linestyle=":", alpha=0.35)
    ax3.legend(loc="upper right", frameon=True)

    fig.suptitle("Hospital-Accident Assignment and Span Dashboard", fontsize=18, y=0.99)
    fig.tight_layout()
    dashboard_out = base_dir / "assignment_span_dashboard.png"
    fig.savefig(dashboard_out, bbox_inches="tight")
    plt.close(fig)

    print("Saved:")
    print(f"- {map_out}")
    print(f"- {gantt_out}")
    print(f"- {dashboard_out}")


if __name__ == "__main__":
    main()
