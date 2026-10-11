import json
import sumolib
import matplotlib.pyplot as plt
import os

# -----------------------------
# 读取优化结果
# -----------------------------
with open("optimization_vs_greedy.json", "r", encoding="utf-8") as f:
    data = json.load(f)

opt_assign = data["optimized_assignment"]
greedy_assign = data["greedy_assignment"]

# -----------------------------
# 读取 SUMO 路网
# -----------------------------
net = sumolib.net.readNet("Shaped.net.xml")
road_coords = {edge.getID(): edge.getFromNode().getCoord() for edge in net.getEdges()}

# -----------------------------
# 可视化函数（美化版，保存为图片）
# -----------------------------
def plot_assignment(assignments, title, filename):
    plt.figure(figsize=(12, 8))
    
    # 画路网背景
    for edge in net.getEdges():
        xs, ys = zip(*[edge.getFromNode().getCoord(), edge.getToNode().getCoord()])
        plt.plot(xs, ys, color='lightgrey', linewidth=0.7, zorder=0)

    # 颜色列表
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f']

    for idx, (h, accident_list) in enumerate(assignments.items()):
        color = colors[idx % len(colors)]
        
        # 医院标记
        if h not in road_coords:
            print(f"Warning: hospital road {h} not in net edges, skipping")
            continue
        hx, hy = road_coords[h]
        plt.scatter(hx, hy, c=color, s=180, marker='s', edgecolors='black', linewidths=1.5, label=f'Hospital {h}')
        
        # 事故标记及连线，颜色深浅按顺序渐变
        n = len(accident_list)
        for i, a in enumerate(accident_list):
            if a not in road_coords:
                print(f"Warning: accident road {a} not in net edges, skipping")
                continue
            ax, ay = road_coords[a]
            alpha = 0.3 + 0.7 * (i+1)/n  # 越晚的事故颜色越深
            plt.scatter(ax, ay, c=color, s=80, marker='x', alpha=alpha)
            plt.plot([hx, ax], [hy, ay], color=color, linewidth=1.2, alpha=0.5)

    plt.title(title, fontsize=16)
    plt.axis('equal')
    plt.xticks([])
    plt.yticks([])
    plt.legend(frameon=True, loc='upper right')
    plt.tight_layout()
    plt.savefig(filename, dpi=300)
    plt.close()
    print(f"{filename} saved.")

# -----------------------------
# 创建保存目录
# -----------------------------
os.makedirs("visualization", exist_ok=True)

# -----------------------------
# 保存两种分配方案
# -----------------------------
plot_assignment(opt_assign, "Optimized Assignment", "visualization/optimized_assignment.png")
plot_assignment(greedy_assign, "Greedy Assignment", "visualization/greedy_assignment.png")
