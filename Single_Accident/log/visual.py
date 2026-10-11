import sumolib
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# -----------------------------
# SUMO 路网文件
# -----------------------------
net_file = "Shaped.net.xml"

# -----------------------------
# 两条路径（road ID 列表）
# -----------------------------
path1 = [
    "200001174","200001175","200001180","200001214","200001215","200001189",
    "200000808","200000815","200000910","200000850","200000852","200000861",
    "200000886","200000053","200000085","200000115","200001661","200001664",
    "200001666","200001667","200001801","200001703","200001705","200001706",
    "200001708","200001709","200001467","200001465","200001463","200001442",
    "200001441","200001439","200001436","200001424","200001397","200001396",
    "200001525","200001415","200001427","200001433","200001437","200001452",
    "200001457","200002013","200011001","200002137","200001992","200002139",
    "200002141"
]

path2 = [
    "200000268","200000266","200000255","200000254","200000251","200000252",
    "200000253","200001672","200001673","200001699","200001701","200001630",
    "200001631","200001692","200001675","200001781","200001676","200000021",
    "200001476","200001468","200001461","200001456","200001445","200001435",
    "200001429","200001411","200001397","200001396","200001525","200001415",
    "200001427","200001433","200001437","200001452","200001457","200002013",
    "200011001","200002137","200001992","200002139","200002141"
]

hospital_starts = {
    "Hospital 200001174": path1[0],
    "Hospital 200000268": path2[0]
}

accident_end = path1[-1]  # 两条路径最终到同一事故点

# -----------------------------
# 读取路网
# -----------------------------
net = sumolib.net.readNet(net_file)
road_coords = {edge.getID(): edge.getFromNode().getCoord() for edge in net.getEdges()}

# -----------------------------
# 可视化
# -----------------------------
plt.figure(figsize=(12, 8))
ax = plt.gca()

# 画路网
for edge in net.getEdges():
    xs, ys = zip(*[edge.getFromNode().getCoord(), edge.getToNode().getCoord()])
    plt.plot(xs, ys, color='lightgrey', linewidth=0.5, zorder=0)

# 标记医院
for label, rid in hospital_starts.items():
    if rid in road_coords:
        hx, hy = road_coords[rid]
        rect = patches.Rectangle((hx-2, hy-1), 4, 2, linewidth=1.5,
                                 edgecolor='black', facecolor='gold', zorder=5)
        ax.add_patch(rect)
        plt.text(hx, hy+2, label, fontsize=10, ha='center', color='black')

# 标记事故点
if accident_end in road_coords:
    axx, ay = road_coords[accident_end]
    plt.scatter(axx, ay, c='red', s=150, marker='*', label='Accident', zorder=10)

# 辅助函数：画带箭头路径
def plot_path(path, color, label):
    coords = [road_coords[rid] for rid in path if rid in road_coords]
    if len(coords) < 2:
        return
    xs, ys = zip(*coords)
    plt.plot(xs, ys, color=color, linewidth=3, alpha=0.7, label=label, zorder=8)
    # 在路径中间加箭头显示方向
    arrow_idx = max(1, len(xs)//2)
    plt.arrow(xs[arrow_idx-1], ys[arrow_idx-1], xs[arrow_idx]-xs[arrow_idx-1],
              ys[arrow_idx]-ys[arrow_idx-1], shape='full', lw=0,
              length_includes_head=True, head_width=5, head_length=5, color=color, zorder=9)

# 绘制路径
plot_path(path1, color='darkblue', label='Path from Hospital 200001174')
plot_path(path2, color='darkred', label='Path from Hospital 200000268')

plt.title("Single Accident Response on SUMO Network", fontsize=16)
plt.axis('equal')
plt.xticks([])
plt.yticks([])
plt.legend(fontsize=10)
plt.tight_layout()
plt.savefig("single_accident_paths_pretty.png", dpi=300)
plt.show()
plt.close()
print("Saved single_accident_paths_pretty.png")
