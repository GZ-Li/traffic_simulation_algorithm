import xml.etree.ElementTree as ET
import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import json

# ----------------------------
# 配置
# ----------------------------
net_file = 'Shaped.net.xml'
cleaned_json = 'plowing_path/cleaned_roads_by_interval_random.json'  # interval数据
output_gif = 'snowplow_animation_random.gif'           # 输出GIF

# ----------------------------
# 读取路网
# ----------------------------
tree = ET.parse(net_file)
root = tree.getroot()

G = nx.DiGraph()
edge_coords = {}

for edge in root.findall('edge'):
    eid = edge.get('id')
    if eid.startswith(':'):
        continue
    lanes = edge.findall('lane')
    if not lanes:
        continue
    shape = lanes[0].get('shape')
    if not shape:
        continue
    coords = [tuple(map(float, p.split(','))) for p in shape.split()]
    edge_coords[eid] = coords
    G.add_edge(edge.get('from'), edge.get('to'), id=eid)

# ----------------------------
# 读取扫雪数据
# ----------------------------
with open(cleaned_json, 'r', encoding='utf-8') as f:
    cleaned_data = json.load(f)

intervals = sorted(cleaned_data.keys(), key=lambda x: int(x.split('_')[1]))
vehicles = list(next(iter(cleaned_data.values())).keys())

# 为每辆车随机颜色
colors = {v: plt.cm.tab10(i) for i, v in enumerate(vehicles)}

# ----------------------------
# 画图设置
# ----------------------------
fig, ax = plt.subplots(figsize=(12,12))
ax.set_aspect('equal')
ax.set_title("Snowplow Cleaning Animation")

all_coords = [pt for coords in edge_coords.values() for pt in coords]
xs, ys = zip(*all_coords)
ax.set_xlim(min(xs)-50, max(xs)+50)
ax.set_ylim(min(ys)-50, max(ys)+50)

# ----------------------------
# 动画函数
# ----------------------------
def animate(i):
    ax.clear()
    ax.set_title(f"Snowplow Cleaning - {intervals[i]}")
    ax.set_xlim(min(xs)-50, max(xs)+50)
    ax.set_ylim(min(ys)-50, max(ys)+50)

    # 灰色画所有道路
    for coords in edge_coords.values():
        xs_c, ys_c = zip(*coords)
        ax.plot(xs_c, ys_c, color='lightgray', linewidth=2, zorder=1)

    # 每辆车清扫的道路
    interval_key = intervals[i]
    for v in vehicles:
        if v not in cleaned_data[interval_key]:
            continue
        for eid in cleaned_data[interval_key][v]:
            if eid in edge_coords:
                xs_c, ys_c = zip(*edge_coords[eid])
                ax.plot(xs_c, ys_c, color=colors[v], linewidth=4, zorder=2, alpha=0.8)
                ax.scatter(xs_c[0], ys_c[0], color=colors[v], s=20, zorder=3)

# ----------------------------
# 创建动画 GIF
# ----------------------------
ani = animation.FuncAnimation(fig, animate, frames=len(intervals), interval=1000, repeat=False)
ani.save(output_gif, writer='pillow', fps=1)
print(f"Animation saved to {output_gif}")
plt.show()