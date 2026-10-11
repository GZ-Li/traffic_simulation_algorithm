#!/usr/bin/env python3
"""Create polished visualizations for assignment locations and schedule span.

Outputs:
1) assignment_map.png   : hospital-accident location assignment on SUMO net background
2) span_gantt.png       : parallel (optimized) vs serial-like (greedy) Gantt comparison
"""

from __future__ import annotations

import json
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OPT_FILE = os.path.join(BASE_DIR, "optimization_result.json")
TIME_FILE = os.path.join(BASE_DIR, "time_matrix.json")
NET_FILE = os.path.abspath(os.path.join(BASE_DIR, "../../Shaped.net.xml"))
MAP_OUT = os.path.join(BASE_DIR, "assignment_map.png")
GANTT_OUT = os.path.join(BASE_DIR, "span_gantt.png")


COLORS = [
    "#2563eb",  # blue
    "#ea580c",  # orange
    "#16a34a",  # green
    "#dc2626",  # red
    "#0891b2",  # cyan
    "#7c3aed",  # violet
]


@dataclass
class Task:
    hospital: str
    accident: str
    start: float
    duration: float


def parse_numeric_id(name: str) -> str:
    m = re.search(r"(\d+)", name)
    if not m:
        raise ValueError(f"Cannot parse numeric id from '{name}'")
    return m.group(1)


def parse_first_point_from_shape(shape: str) -> Tuple[float, float]:
    # shape example: "7363.67,2165.74 7403.87,2163.92"
    first = shape.split()[0]
    x_str, y_str = first.split(",")
    return float(x_str), float(y_str)


def load_edge_points(net_file: str) -> Dict[str, Tuple[float, float]]:
    if not os.path.exists(net_file):
        raise FileNotFoundError(f"NET file not found: {net_file}")

    edge_points: Dict[str, Tuple[float, float]] = {}

    # Iterparse keeps memory usage low for large net files.
    current_edge_id = None
    current_edge_function = None
    got_lane = False

    for event, elem in ET.iterparse(net_file, events=("start", "end")):
        if event == "start" and elem.tag == "edge":
            current_edge_id = elem.attrib.get("id")
            current_edge_function = elem.attrib.get("function")
            got_lane = False

        elif event == "start" and elem.tag == "lane":
            if current_edge_id and current_edge_function != "internal" and not got_lane:
                shape = elem.attrib.get("shape")
                if shape:
                    edge_points[current_edge_id] = parse_first_point_from_shape(shape)
                    got_lane = True

        elif event == "end" and elem.tag == "edge":
            current_edge_id = None
            current_edge_function = None
            got_lane = False
            elem.clear()

    if not edge_points:
        raise RuntimeError("No edge points parsed from net file")

    return edge_points


def build_tasks(assignments: Dict[str, List[str]], response_times: Dict[str, Dict[str, float]]) -> List[Task]:
    tasks: List[Task] = []
    for hospital, accidents in assignments.items():
        t = 0.0
        for accident in accidents:
            dur = float(response_times[hospital][accident])
            tasks.append(Task(hospital=hospital, accident=accident, start=t, duration=dur))
            t += dur
    return tasks


def makespan(tasks: List[Task]) -> float:
    if not tasks:
        return 0.0
    return max(task.start + task.duration for task in tasks)


def plot_assignment_map(
    edge_points: Dict[str, Tuple[float, float]],
    opt_assign: Dict[str, List[str]],
    greedy_assign: Dict[str, List[str]],
    out_file: str,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 7), dpi=180)
    fig.patch.set_facecolor("#f8fafc")

    def draw_one(ax, assignments: Dict[str, List[str]], title: str) -> None:
        ax.set_facecolor("#ffffff")

        # Draw all known points lightly to create context.
        all_x = [pt[0] for pt in edge_points.values()]
        all_y = [pt[1] for pt in edge_points.values()]
        ax.scatter(all_x, all_y, s=1, c="#e2e8f0", alpha=0.35, linewidths=0)

        hospitals = list(assignments.keys())
        for i, hospital_name in enumerate(hospitals):
            color = COLORS[i % len(COLORS)]
            h_id = parse_numeric_id(hospital_name)
            if h_id not in edge_points:
                continue
            hx, hy = edge_points[h_id]

            ax.scatter([hx], [hy], s=240, c=color, marker="s", edgecolors="#0f172a", linewidths=1.5, zorder=4)
            ax.text(hx + 22, hy + 22, f"H {h_id}", color="#0f172a", fontsize=9, weight="bold")

            accidents = assignments[hospital_name]
            total = max(1, len(accidents))
            for idx, acc_name in enumerate(accidents):
                a_id = parse_numeric_id(acc_name)
                if a_id not in edge_points:
                    continue
                ax_x, ax_y = edge_points[a_id]
                alpha = 0.45 + 0.5 * ((idx + 1) / total)
                ax.scatter(
                    [ax_x],
                    [ax_y],
                    s=130,
                    c=color,
                    marker="X",
                    edgecolors="#111827",
                    linewidths=0.9,
                    alpha=alpha,
                    zorder=5,
                )
                ax.plot([hx, ax_x], [hy, ax_y], color=color, linewidth=2.0, alpha=0.65, zorder=3)
                ax.text(ax_x + 18, ax_y - 18, f"A {a_id}", color="#334155", fontsize=8)

        ax.set_title(title, fontsize=13, fontweight="bold", color="#0f172a", pad=12)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#cbd5e1")
            spine.set_linewidth(1)

    draw_one(axes[0], opt_assign, "Optimized Assignment (More Parallel)")
    draw_one(axes[1], greedy_assign, "Greedy Assignment (More Serial)")

    fig.suptitle("Hospital -> Accident Spatial Assignment", fontsize=15, fontweight="bold", color="#0f172a")
    plt.tight_layout(rect=(0, 0.02, 1, 0.95))
    plt.savefig(out_file, dpi=220)
    plt.close(fig)


def plot_span_gantt(
    opt_tasks: List[Task],
    greedy_tasks: List[Task],
    opt_makespan: float,
    greedy_makespan: float,
    out_file: str,
) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), dpi=180, sharex=True)
    fig.patch.set_facecolor("#f8fafc")

    def draw_gantt(ax, tasks: List[Task], title: str, ms: float) -> None:
        ax.set_facecolor("#ffffff")

        hospitals = sorted({task.hospital for task in tasks})
        y_map = {h: i for i, h in enumerate(hospitals)}

        color_map = {h: COLORS[i % len(COLORS)] for i, h in enumerate(hospitals)}

        for task in tasks:
            y = y_map[task.hospital]
            c = color_map[task.hospital]
            ax.barh(y, task.duration, left=task.start, height=0.5, color=c, alpha=0.92, edgecolor="#111827")
            label = parse_numeric_id(task.accident)
            if task.duration >= 40:
                ax.text(task.start + task.duration / 2, y, f"A{label}", ha="center", va="center", color="white", fontsize=8, weight="bold")

        ax.axvline(ms, color="#b91c1c", linestyle="--", linewidth=2)
        ax.text(ms + 2, len(hospitals) - 0.1, f"makespan={ms:.0f}s", color="#b91c1c", fontsize=9, va="bottom")

        ax.set_yticks(list(y_map.values()))
        ax.set_yticklabels([f"H{parse_numeric_id(h)}" for h in hospitals], fontsize=9)
        ax.grid(axis="x", linestyle="--", alpha=0.25)
        ax.set_title(title, fontsize=12, fontweight="bold", color="#0f172a", loc="left", pad=10)
        for spine in ax.spines.values():
            spine.set_color("#cbd5e1")

    draw_gantt(axes[0], opt_tasks, "Optimized Schedule (Parallel across hospitals)", opt_makespan)
    draw_gantt(axes[1], greedy_tasks, "Greedy Schedule (Serial bottleneck)", greedy_makespan)

    delta = greedy_makespan - opt_makespan
    pct = 0.0 if math.isclose(greedy_makespan, 0.0) else 100.0 * delta / greedy_makespan

    axes[1].set_xlabel("Time (seconds)", fontsize=10)
    fig.suptitle(
        f"Span (Makespan) Gantt Comparison  |  Improvement: {delta:.0f}s ({pct:.1f}%)",
        fontsize=14,
        fontweight="bold",
        color="#0f172a",
    )
    plt.tight_layout(rect=(0, 0.02, 1, 0.95))
    plt.savefig(out_file, dpi=220)
    plt.close(fig)


def main() -> None:
    with open(OPT_FILE, "r", encoding="utf-8") as f:
        opt_data = json.load(f)

    with open(TIME_FILE, "r", encoding="utf-8") as f:
        time_data = json.load(f)

    opt_assign = opt_data["optimized_assignment"]
    greedy_assign = opt_data["greedy_assignment"]

    edge_points = load_edge_points(NET_FILE)

    plot_assignment_map(edge_points, opt_assign, greedy_assign, MAP_OUT)

    opt_tasks = build_tasks(opt_assign, time_data)
    greedy_tasks = build_tasks(greedy_assign, time_data)
    opt_ms = makespan(opt_tasks)
    greedy_ms = makespan(greedy_tasks)

    plot_span_gantt(opt_tasks, greedy_tasks, opt_ms, greedy_ms, GANTT_OUT)

    print(f"Saved: {MAP_OUT}")
    print(f"Saved: {GANTT_OUT}")
    print(f"Optimized makespan: {opt_ms:.1f}s")
    print(f"Greedy makespan:    {greedy_ms:.1f}s")


if __name__ == "__main__":
    main()
