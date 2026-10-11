# Snowplowing（扫雪场景）

本目录用于城市积雪场景下的扫雪路径规划与仿真验证，主要回答这个问题：

> **按什么顺序清扫道路，才能在给定时间内，让全局交通尽快恢复？**

---

## 📂 主要文件

- **`get_path_greedy.py`**
  贪心扫雪路径生成脚本（**当前实验使用的 best 策略**）。
  从 MongoDB 读取 OD 数据，统计各路段交通流量，4辆扫雪车以贪心方式优先清扫高流量路段。
  由于当前测试路网规模较小，直接使用全局贪心即可取得良好效果。

- **`get_path_random.py`**
  随机扫雪路径生成脚本。
  同样支持多车，路段选取随机，作为对照组基线。

- **`util/snowplowing_with_partition/get_path_greedy_region_partitioned.py`**
  区域划分贪心路径生成脚本（备用，面向大路网）。
  将路网按空间四象限划分，每辆扫雪车负责一个区域，在区域内执行贪心选路，避免多车互相抢占路段。
  当前测试路网较小，分区效果提升有限，暂未用于主实验。

- **`simulation_validation.py`**
  仿真验证主脚本（核心）。
  对给定扫雪路径按时间区间累积清扫道路（恢复限速），评估各区间的 `global_avg_speed` 和 `union_avg_speed`。

- **`simulation_validation_ext.py`**
  极端场景对比脚本。
  对比"全部积雪"与"全部清扫"两个极端状态，给出速度提升的理论上下界。

- **`run_snowplowing_simulation_validation.sh`**
  一键运行脚本（best vs random 对比）。

- **`run_snowplowing_simulation_ext.sh`**
  一键运行极端场景实验。

- **`plowing_path/`**
  生成好的扫雪路径文件：
  - `greedy.json`：当前实验使用的 best 策略路径
  - `random.json`：对照组随机路径
  - `greedy_region_partitioned.json`：区域划分贪心路径（备用，面向大路网）

- **`res/`**
  标准对比实验的输出结果。

- **`ext_res/`**
  极端场景实验的输出结果。

---

## 🚀 运行流程

### Step 1：生成扫雪路径（可选，路径已预生成）

**生成 best 路径（贪心）：**

```bash
python3 get_path_greedy.py
```

输出：`plowing_path/greedy.json`

**生成 random 路径：**

```bash
python3 get_path_random.py
```

输出：`plowing_path/random.json`

---

### Step 2：运行仿真验证（best vs random 对比）

```bash
bash run_snowplowing_simulation_validation.sh
```

该脚本调用 `simulation_validation.py`，以 `--compare best_random` 模式同时运行 best 和 random 两组，输出到 `res/`。

**手动运行（单组或自定义）：**

```bash
python3 simulation_validation.py \
  --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
  --map_db tsingroc \
  --map_coll map_pku_wuhan_demo_1015c_rl \
  --agent_db tsingroc \
  --agent_coll person_test_20250724_1015cc \
  --interval 30 \
  --warmup_steps 5 \
  --interval_steps 60 \
  --max_intervals 10 \
  --snowed_speed 3 \
  --normal_speed 20 \
  --exp best_comp_random \
  --compare best_random
```

---

### Step 3：生成对比可视化

```bash
python3 res/plot_best_vs_random.py
```

输出：
- `res/best_vs_random_comparison.png`
- `res/best_vs_random_comparison.svg`

---

## 🧪 极端场景实验（上下界对照）

```bash
bash run_snowplowing_simulation_ext.sh
```

对比"全部积雪"（清扫 0 条）与"全部清扫"（清扫全部 7119 条车道），确认速度提升的理论范围。

当前极端场景结果：

| 场景 | 全局平均速度 |
|------|------------|
| 全部积雪 | 3.20 |
| 全部清扫 | 17.94 |

结果保存在 `ext_res/extreme_scenarios_summary.csv`。

---

## ⚙️ 关键参数说明（simulation_validation.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--interval` | 30 | 仿真步长（秒） |
| `--warmup_steps` | 20 | 每个区间热身步数 |
| `--interval_steps` | 50 | 每个区间评估步数 |
| `--max_intervals` | 10 | 最大区间数 |
| `--snowed_speed` | 2.0 | 积雪路段限速 |
| `--normal_speed` | 20.0 | 清扫后路段限速 |
| `--compare` | `single` | `single` / `best_random` / `best_random_fair` |
| `--path_file` | `greedy_region_partitioned.json` | 单组模式下的路径文件 |
| `--exp` | `snow_lane_limit_independent` | 实验名称（影响输出目录名） |
| `--speed_sample_every` | 200 | 速度采样频率（每 N 步采样一次） |

---

## 📊 输出结果说明

`res/` 下输出结构：

```
res/
├── best/best_summary.csv         # best 策略各区间指标
├── random/random_summary.csv     # random 策略各区间指标
├── best_vs_random_comparison.csv # 两策略对比表（核心）
├── best_vs_random_comparison.json
├── plot_best_vs_random.py        # 可视化脚本
├── best_vs_random_comparison.png # 对比图
└── best_vs_random_comparison.svg
```

`best_summary.csv` / `random_summary.csv` 核心字段：

| 字段 | 说明 |
|------|------|
| `interval_name` | 时间区间编号 |
| `cleaned_lane_count` | 累计已清扫车道数 |
| `snowed_lane_count` | 仍积雪车道数 |
| `global_avg_speed` | 全局平均速度（核心指标） |

`best_vs_random_comparison.csv` 额外字段：

| 字段 | 说明 |
|------|------|
| `delta_global_avg_speed` | best 比 random 快多少（全局） |
| `union_avg_speed` | 两策略共同覆盖路段的平均速度 |
| `delta_union_avg_speed` | best 比 random 快多少（共同路段） |

---

## ✅ 当前实验结论

每个 interval 对应 **30 分钟**，每 2 个 interval 即 1 小时。

样例结果（`res/best_vs_random_comparison.csv`，只展示偶数区间）：

| 区间 | 对应时间 | best 全局速度 | random 全局速度 | Δ 全局 | Δ 共同路段 |
|------|---------|-------------|----------------|--------|-----------|
| interval_2  | 1 h | 4.94 | 4.92 | +0.01 | +0.10 |
| interval_4  | 2 h | 6.15 | 6.14 | +0.01 | +0.05 |
| interval_6  | 3 h | 7.22 | 6.86 | +0.37 | +1.08 |
| interval_8  | 4 h | 8.39 | 7.44 | +0.95 | +2.29 |
| interval_10 | 5 h | 9.14 | 8.00 | +1.14 | +2.40 |

**结论：随着扫雪推进，best 策略的优势持续扩大。在扫雪进行 5 小时后（interval_10），全局速度优势达 +1.14，共同覆盖路段速度优势达 +2.40。**

---

## 🔧 依赖与环境

- Python 3.8+
- `networkx`（路径规划图算法）
- `pymongo`（OD 数据导出）
- `matplotlib`、`numpy`（可视化）
- 仓库内 `trafficlight_rl` 引擎模块
- 可访问的 MongoDB（地图与 agent 数据）
