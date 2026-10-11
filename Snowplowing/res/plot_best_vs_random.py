#!/usr/bin/env python3
"""Visualize best vs random strategy comparison for Snowplowing.

Metrics: global_avg_speed, union_avg_speed
Source:  res/best_vs_random_comparison.csv
"""
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ---------- data ----------
BASE = Path(__file__).resolve().parent
CSV = BASE / "best_vs_random_comparison.csv"

rows = []
with CSV.open("r", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

x_labels = [r["interval_name"].replace("interval_", "Int ") for r in rows]
x = np.arange(len(rows))

best_global  = [float(r["best_global_avg_speed"])  for r in rows]
rand_global  = [float(r["random_global_avg_speed"]) for r in rows]
best_union   = [float(r["best_union_avg_speed"])   for r in rows]
rand_union   = [float(r["random_union_avg_speed"])  for r in rows]

# ---------- style ----------
plt.style.use("seaborn-v0_8-whitegrid")
COLOR_BEST   = "#0f766e"   # teal
COLOR_RANDOM = "#dc2626"   # red
COLOR_FILL   = "#14b8a6"
BG           = "#f8fafc"

fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), dpi=160)
fig.patch.set_facecolor(BG)
fig.suptitle(
    "Snowplowing: Best Strategy vs Random Strategy",
    fontsize=16, fontweight="bold", y=1.01,
)

def draw_panel(ax, best, rand, ylabel, title):
    ax.set_facecolor("#fdfdfd")

    # fill between
    ax.fill_between(x, best, rand,
                    where=[b >= r for b, r in zip(best, rand)],
                    interpolate=True,
                    color=COLOR_FILL, alpha=0.18, zorder=1,
                    label="Best ≥ Random (gap)")

    # lines
    ax.plot(x, best, marker="o", linewidth=2.5, markersize=8,
            color=COLOR_BEST,   label="Best",   zorder=3)
    ax.plot(x, rand, marker="s", linewidth=2.5, markersize=8,
            color=COLOR_RANDOM, label="Random", zorder=3,
            linestyle="--")

    # delta annotations (above the higher line)
    for xi, b, r in zip(x, best, rand):
        delta = b - r
        top   = max(b, r)
        ax.annotate(
            f"Δ={delta:+.2f}",
            xy=(xi, top),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center", fontsize=9.5, color="#334155",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white",
                  "ec": "#cbd5e1", "alpha": 0.92},
            zorder=4,
        )

    # axis
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=8)
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())

    margin = (max(max(best), max(rand)) - min(min(best), min(rand))) * 0.18
    ax.set_ylim(min(min(best), min(rand)) - margin,
                max(max(best), max(rand)) + margin + 0.4)

    ax.legend(loc="upper left", frameon=True, framealpha=0.95, fontsize=9.5)

    # summary box
    avg_delta = sum(b - r for b, r in zip(best, rand)) / len(best)
    max_delta = max(b - r for b, r in zip(best, rand))
    ax.text(
        0.98, 0.05,
        f"Avg Δ: {avg_delta:+.3f}\nMax Δ: {max_delta:+.3f}",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=9.5,
        bbox={"boxstyle": "round,pad=0.4", "fc": "#f1f5f9", "ec": "#94a3b8"},
    )

draw_panel(axes[0], best_global, rand_global,
           "global_avg_speed", "(1) Global Avg Speed")
draw_panel(axes[1], best_union,  rand_union,
           "union_avg_speed",  "(2) Union Avg Speed")

fig.tight_layout()
out_png = BASE / "best_vs_random_comparison.png"
out_svg = BASE / "best_vs_random_comparison.svg"
fig.savefig(out_png, bbox_inches="tight")
fig.savefig(out_svg, bbox_inches="tight")
print(f"Saved: {out_png}")
print(f"Saved: {out_svg}")
