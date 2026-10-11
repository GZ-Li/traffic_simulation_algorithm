# Waterlogging（内涝场景）

本目录用于城市内涝场景下的三件事：
1. 识别内涝高风险点（可选，离线分析）。
2. 生成排涝优先级策略。
3. 在仿真中验证不同策略，并重点对比 `flooded_avg_speed`。

## 📂 主要文件

- `waterlogging_point_identification/`
  内涝点识别相关脚本与原始数据目录（AHP 分析）。

- `Waterlogging_Priority.py`
  排涝策略生成脚本。
  - 功能：从 MongoDB 导出 OD/地图数据，统计各内涝点影响规模，生成 `waterlogging_strategy.json`。

- `simulation_validation.py`
  仿真验证脚本（核心）。
  - 功能：按 `best/worst/custom` 顺序分阶段清理内涝点，输出各阶段 `flooded_avg_speed`。

- `run_waterlogging.sh`
  一键运行脚本。
  - 功能：自动跑两组实验并落盘到 `res/`。

- `waterlogging_points.json`
  内涝点 ID 到道路 ID 列表的映射。

- `waterlogging_strategy.json`
  内涝点策略分值（由 `Waterlogging_Priority.py` 生成或更新）。

- `res/`
  实验输出目录（按实验名和时间戳分子目录）。

## 🚀 运行流程（推荐）

### 1. 生成/更新排涝策略

```bash
python3 Waterlogging_Priority.py
```

运行后会更新：
- `current_od_file.json`
- `current_map_file.json`
- `waterlogging_strategy.json`

### 2. 运行仿真验证（best vs random）

```bash
bash run_waterlogging.sh
```

该脚本会执行两组：
- `best` 组：`--order best --exp best`
- `random` 组：`--order worst --exp random`

### 3. 生成对比可视化（关注 flooded_avg_speed）

```bash
python3 res/plot_flooded_avg_speed_comparison.py
```

输出：
- `res/best_vs_random_flooded_avg_speed.png`
- `res/best_vs_random_flooded_avg_speed.svg`

## 🧪 手动运行 simulation_validation.py

```bash
bash -x run_waterlogging.sh
```

### 常用参数

- `--order`: `best` / `worst` / `custom`
- `--custom_order`: `custom` 模式下手动顺序（逗号分隔）
- `--eval_steps`: 每个阶段统计步数
- `--flooded_speed`: 内涝路段限速
- `--normal_speed`: 清理后路段限速

## 📊 结果文件说明

每次运行会生成目录：`res/<exp>/<timestamp>/`

其中最关键文件：
- `stage_summary.csv`
- `stage_summary.json`
- `run_config.json`
- `stage_0/result.json`、`stage_1/result.json` ...

`stage_summary.csv` 核心字段：
- `stage_index`
- `cleaned_points`
- `flooded_points`
- `flooded_avg_speed`（核心指标）

## ✅ 当前样例结论（flooded_avg_speed）

样例：
- `res/best/20260423-130611/stage_summary.csv`
- `res/random/20260423-130654/stage_summary.csv`

对比：
- `stage_0`: best = 1.3318, random = 1.3318
- `stage_1`: best = 3.9069, random = 1.4586（best 显著更优）
- `stage_2`: best = 4.9947, random = 4.9947

结论：在关键中间阶段，best 策略明显优于对照组。

## 🔧 依赖

- Python 3.8+
- `pymongo`
- `matplotlib`（画图脚本）
- 仓库内 `trafficlight_rl` 引擎模块
- 可访问的 MongoDB（地图与 agent 数据）
