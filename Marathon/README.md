# RoutePlanner 智能路线规划器

基于 OR-Tools 和遗传算法的多约束路线规划系统，支持 OSM 和 SUMO Net 两种路网格式。

## 核心特性

- **双算法优化**: OR-Tools 求解最优途经点顺序 + 遗传算法局部优化
- **多路网格式支持**: OSM XML 和 SUMO .net.xml
- **多约束优化**: 途经点、距离、拥堵系数约束
- **评估指标**: 距离满足度、途经点满足度

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      RoutePlanner                            │
├─────────────────────────────────────────────────────────────┤
│  route_planner.py                                           │
│  ├── RoutePlanner        - 主入口类                          │
│  │   ├── plan_route()    - 路线规划主流程                    │
│  │   ├── save_results()  - 保存结果                          │
│  │   └── generate_summary_report() - 生成报告                │
│  └── GeneticOptimizer    - 遗传算法优化器                    │
│      ├── create_population()  - 创建种群                     │
│      ├── evaluate_fitness()   - 适应度计算                   │
│      └── optimize()           - 优化迭代                     │
├─────────────────────────────────────────────────────────────┤
│  run_wuhan_net.py                                           │
│  ├── run_single_test()     - 运行单个测试用例                │
│  ├── calculate_baseline()  - 计算最短路径基准                │
│  └── generate_comparison_visualization() - 对比可视化        │
├─────────────────────────────────────────────────────────────┤
│  utils.py                                                   │
│  ├── OSMDataProcessor  - OSM数据处理                        │
│  ├── NetDataProcessor  - Net路网数据处理                    │
│  └── 路径评估函数                                           │
│      ├── calculate_via_satisfaction()    - 途经点满足度      │
│      ├── calculate_distance_satisfaction() - 距离满足度      │
│      └── calculate_route_metrics()       - 路径统计指标      │
└─────────────────────────────────────────────────────────────┘
```

## 支持的路网格式

| 格式 | 说明 | 处理器 |
|------|------|--------|
| OSM XML | OpenStreetMap XML 格式 | OSMDataProcessor |
| Net .net.xml | SUMO 路网格式 | NetDataProcessor |

## 快速开始

### 环境安装

```bash
# 安装 uv (如果未安装)
curl -LsSf https://astral.sh/uv/install.sh | sh
# 本地安装文件见目录下的压缩文件夹
# tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz
# mkdir -p ~/.local/bin
# mv uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx ~/.local/bin/
# echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
# source ~/.bashrc

# 同步依赖
uv sync
```

### 运行测试

```bash
# 运行预设测试用例
# case3 为小范围路网测试，case1 为大范围路网测试
uv run python run_wuhan_net.py \
    --net-file data/wuhan_core.net.xml \
    --output-dir results \
    --cases case3 \
    --force-recompute \
    --generations 200

uv run python run_wuhan_net.py \
    --net-file data/wuhan.net.xml \
    --output-dir results \
    --cases case1 \
    --force-recompute \
    --generations 200
```

## 输入参数

### run_wuhan_net.py 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--net-file` | Net 路网文件路径 | `data/wuhan_core.net.xml` |
| `--output-dir` | 输出目录 | `results/` |
| `--cases` | 测试用例列表 | case3 |
| `--generations` | 遗传算法迭代次数 | 100 |
| `--force-recompute` | 强制重新计算 | False |

### route_planner.py 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--start-lat`, `--start-lon` | 起点经纬度 | 必填 |
| `--end-lat`, `--end-lon` | 终点经纬度 | 必填 |
| `--intermediate-lats`, `--intermediate-lons` | 途经点经纬度 | 可选 |
| `--distance` | 目标距离（公里） | 可选 |
| `--generations` | 迭代次数 | 100 |
| `--net-file` | Net 路网文件 | 可选 |
| `--local-map` | 本地 OSM 文件 | 可选 |

## 输出格式

### JSON 结果文件

```json
{
  "input": {
    "start_location": null,
    "end_location": null,
    "intermediate_locations": [],
    "target_distance": 20,
    "city": "Net Network",
    "start_lat": 30.4907,
    "start_lon": 114.5452,
    "end_lat": 30.4374,
    "end_lon": 114.4171,
    "intermediate_lats": [30.4732, 30.4179],
    "intermediate_lons": [114.4758, 114.449]
  },
  "route": {
    "nodes": ["node1", "node2", ...],
    "edge_ids": ["edge1", "edge2", ...],
    "edge_count": 142,
    "total_distance": 18500.5,
    "total_distance_km": 18.5,
    "congestion_percentage": 28.5,
    "avg_congestion_score": 0.285
  },
  "optimization": {
    "algorithm": "OR-Tools + Genetic Algorithm",
    "generations": 100,
    "final_fitness": 0.85,
    "history": [...]
  },
  "details": [...],
  "timestamp": "2026-01-16T10:30:00.000000"
}
```

### 测试结果总结

```
============================================================================================
测试结果总结
============================================================================================
Case     方法         距离(m)        边数           拥堵程度      距离满足度    途经点满足度
--------------------------------------------------------------------------------------------
case3    baseline      4673.01        57        50.0%       93.5%        0.0%
case3    GA            5346.67        69        47.2%       93.1%      100.0%
--------------------------------------------------------------------------------------------
总计: 2 条记录 (1 个测试用例)
```

### 评估指标说明

| 指标 | 说明 | 计算方式 |
|------|------|----------|
| 距离满足度 | 实际距离与目标距离的接近程度，100%为最佳 | `100 - |实际-目标|/目标×100` |
| 途经点满足度 | 路径包含的途经点比例，100%为全部满足 | `满足数量/总数×100` |
| 拥堵程度 | 路径拥堵路段占比，越低越畅通 | 加权平均计算 |

### 输出文件说明

运行 `run_wuhan_net.py` 后，输出目录包含以下文件：

| 文件 | 说明 |
|------|------|
| `route_planning.json` | 规划结果（JSON格式），Net模式下包含 `edge_ids` 字段存储对应SUMO Edge ID序列 |
| `summary.txt` | 测试结果总结表格 |
| `route_visualization.png` | GA优化路径可视化 |
| `route_comparison.png` | Baseline vs GA 对比图 |

## 运行示例

### 示例 1: 运行预设测试用例

```bash
uv run python run_wuhan_net.py \
    --net-file data/wuhan_core.net.xml \
    --cases case1 case3 \
    --generations 200 \
    --output-dir results/wuhan_tests/net
```

### 示例 2: 自定义坐标测试

```bash
uv run python run_wuhan_net.py \
    --net-file data/wuhan_core.net.xml \
    --start-lat 30.4907 --start-lon 114.5452 \
    --end-lat 30.4374 --end-lon 114.4171 \
    --generations 100 \
    --output-dir results/custom
```

### 示例 3: 使用 route_planner.py

```bash
uv run python route_planner.py \
    --start-lat 30.4907 --start-lon 114.5452 \
    --end-lat 30.4374 --end-lon 114.4171 \
    --intermediate-lats 30.4732 30.4179 \
    --intermediate-lons 114.4758 114.449 \
    --distance 20 \
    --generations 100 \
    --net-file data/wuhan_core.net.xml \
    --output results/route_planning.json
```

## 项目结构

```
├── run_wuhan_net.py           # 测试脚本 - Net路网测试用例运行
├── route_planner.py           # 主程序 - OR-Tools + 遗传算法路线规划
├── utils.py                   # 工具模块 - 数据处理、评估函数
├── pyproject.toml             # 项目配置和依赖
├── data/                      # 数据目录
│   ├── wuhan_core.net.xml     # 测试核心区路网
│   ├── wuhan.net.xml          # 测试全区域路网
│   └── THU-PKU.net.xml        # 清华北大路网
├── results/                   # 输出目录
│   ├── case1/                 # case1 测试结果
│   └── case3/                 # case3 测试结果
├── scripts/                   # 工具脚本
└── legacy/                    # 遗留代码（已不推荐使用）
```

## 依赖说明

核心依赖：

- `ortools` - OR-Tools 约束求解器（路径规划）
- `osmnx` - OpenStreetMap 数据处理
- `networkx` - 图算法（路网表示）
- `numpy` - 数值计算
- `matplotlib` - 可视化
- `sumolib` - SUMO 工具库（路网解析）
- `pyproj` - 坐标投影转换

完整依赖见 `pyproject.toml`。

## 与交通仿真器对接

本系统可与 SUMO 等交通仿真器对接，用于评估不同路线方案对日常交通的影响。

### 对接流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        与模拟器对接流程                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. 基础仿真                                                                  │
│     └─> 使用标准 OD（起终点矩阵）在原始地图上运行仿真                          │
│     └─> 收集各路段的拥堵数据（速度、流量等）                                   │
│                                                                              │
│  2. 拥堵系数计算                                                              │
│     └─> 基于仿真结果计算每条边的拥堵系数                                       │
│     └─> 逻辑：congestion_score = f(实际速度, 自由流速度, 道路等级)            │
│     └─> 实现在 `utils.py` 的 `calculate_congestion_score()` 中                │
│                                                                              │
│  3. 地图格式转换                                                              │
│     └─> 将原始地图转换为 SUMO Net 格式                                        │
│     └─> 转换过程中保持 Edge ID 不变，确保与仿真器对应                          │
│     └─> 参考 `scripts/mosspb_to_sumonet_v6.py`                                │
│                                                                              │
│  4. 路径优化                                                                  │
│     └─> 加载拥堵系数，运行遗传算法进行路径优化                                 │
│     └─> 输出：规划路径的 Edge ID 序列                                         │
│                                                                              │
│  5. 评估验证                                                                  │
│     └─> 根据优化结果的 Edge ID 封锁对应道路                                   │
│     └─> 使用标准 OD 在封锁后的路网上运行仿真                                   │
│     └─> 对比评估不同路线方案的交通影响                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 关键实现

| 组件 | 说明 |
|------|------|
| `calculate_congestion_score()` | 基于道路属性和速度计算拥堵系数 |
| `NetDataProcessor.nodes_to_edge_ids()` | 将路径节点转换为 Edge ID 序列 |
| `route_planning.json` | 输出中包含 `edge_ids` 字段，直接对应 SUMO Edge ID |

### 输出格式

优化结果中的 `edge_ids` 字段可直接用于仿真器道路封锁：

```json
{
  "route": {
    "nodes": ["n1", "n2", "n3", ...],
    "edge_ids": [":2374980", ":2374981", "2374982", ...],
    "edge_count": 142,
    "total_distance": 18500.5
  }
}
```

**注意**：Edge ID 需与仿真器中的路网保持一致，建议使用 SUMO Net 格式进行优化。

## 算法说明

### 双阶段优化流程

1. **OR-Tools 全局优化**: 使用 CP-SAT 求解器计算最优途经点访问顺序
2. **遗传算法局部优化**: 基于 OR-Tools 结果进行局部优化，提升整体适应度

### 适应度函数

```
fitness = (1 - distance_penalty) × w1 + (1 - congestion) × w2
```

其中：
- `distance_penalty`: 距离偏差惩罚
- `congestion`: 拥堵系数
- `w1, w2`: 权重系数
