#!/usr/bin/env python3
import csv
from pathlib import Path

import matplotlib.pyplot as plt


def read_stage_speeds(csv_path: Path):
    stages = []
    speeds = []
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stages.append(int(row["stage_index"]))
            speeds.append(float(row["flooded_avg_speed"]))
    return stages, speeds


def main():
    base = Path(__file__).resolve().parent
    best_csv = base / "best/20260423-130611/stage_summary.csv"
    random_csv = base / "random/20260423-130654/stage_summary.csv"

    best_stages, best_speed = read_stage_speeds(best_csv)
    random_stages, random_speed = read_stage_speeds(random_csv)

    if best_stages != random_stages:
        raise ValueError("best/random 的 stage_index 不一致，无法直接对比")

    improvements = [b - r for b, r in zip(best_speed, random_speed)]
    avg_improvement = sum(improvements) / len(improvements)

    # Style
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=160)
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#fdfdfd")

    color_best = "#0f766e"   # teal
    color_random = "#dc2626"  # red
    color_fill = "#14b8a6"    # light teal

    ax.plot(
        best_stages,
        best_speed,
        marker="o",
        linewidth=3,
        markersize=8,
        color=color_best,
        label="Best strategy",
        zorder=3,
    )
    ax.plot(
        random_stages,
        random_speed,
        marker="o",
        linewidth=3,
        markersize=8,
        color=color_random,
        label="Random strategy",
        zorder=3,
    )

    # Highlight stages where best is better
    ax.fill_between(
        best_stages,
        best_speed,
        random_speed,
        where=[b >= r for b, r in zip(best_speed, random_speed)],
        interpolate=True,
        color=color_fill,
        alpha=0.18,
        label="Best >= Random",
        zorder=1,
    )

    for x, b, r, d in zip(best_stages, best_speed, random_speed, improvements):
        ax.annotate(
            f"Δ={d:+.2f}",
            xy=(x, max(b, r)),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            color="#334155",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "#cbd5e1", "alpha": 0.9},
            zorder=4,
        )

    ax.set_title(
        "Flooded Average Speed Comparison: Best vs Random",
        fontsize=16,
        fontweight="bold",
        pad=12,
    )
    ax.set_xlabel("Stage index", fontsize=12)
    ax.set_ylabel("flooded_avg_speed", fontsize=12)
    ax.set_xticks(best_stages)

    min_y = min(min(best_speed), min(random_speed))
    max_y = max(max(best_speed), max(random_speed))
    margin = max(0.2, (max_y - min_y) * 0.15)
    ax.set_ylim(min_y - margin, max_y + margin)

    ax.legend(loc="upper left", frameon=True, framealpha=0.95)

    summary = (
        f"Avg Δ(best-random): {avg_improvement:+.2f}\n"
        f"Stage-1 Δ: {improvements[1]:+.2f}"
    )
    ax.text(
        0.985,
        0.04,
        summary,
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=10,
        bbox={"boxstyle": "round,pad=0.4", "fc": "#f1f5f9", "ec": "#94a3b8"},
    )

    out_png = base / "best_vs_random_flooded_avg_speed.png"
    out_svg = base / "best_vs_random_flooded_avg_speed.svg"
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    fig.savefig(out_svg, bbox_inches="tight")
    print(f"Saved: {out_png}")
    print(f"Saved: {out_svg}")


if __name__ == "__main__":
    main()
