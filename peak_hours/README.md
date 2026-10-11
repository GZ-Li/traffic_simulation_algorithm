# 增量算法模块使用说明

本模块实现了三个交通仿真优化算法，严格遵循以下三条规则：

1. **增量开发**：不修改现有代码，只新增模块
2. **参数化控制**：通过统一入口 + 参数控制算法开关
3. **只用已有接口**：仅使用 `pycitysim` 和 `trafficlight_rl.Engine` 的现有能力

---

## 目录结构

```
incremental_algos/
├── runtime/              # 公共运行时封装
│   └── sim_runner.py     # SimulationRunner - 封装Engine基础操作
├── metrics/              # 公共指标计算
│   └── evaluator.py      # MetricsEvaluator & BusUtilizationEvaluator
├── vehicle_config/       # 算法一：仿真车辆配置
│   ├── vehicle_builder.py   # 车辆集构建器（MongoDB下采样）
│   └── runner.py            # 车辆配置实验运行器
├── road_opt/             # 算法二：道路资源优化
│   ├── max_pressure.py      # MaxPressure控制器
│   ├── phase_collector.py   # 相位时长收集器
│   ├── phase_ratio.py       # 相位比例估算器
│   ├── fixed_timing.py      # 固定配时部署器
│   └── runner.py            # 道路优化实验运行器
└── bus_opt/              # 算法三：公交车利用率提升
    ├── bus_selector.py      # 公交车选择器
    ├── weighted_pressure.py # 公交加权压力控制器
    └── runner.py            # 公交优化实验运行器
```

---

## 快速开始

### 统一入口脚本

所有算法通过 **`run_incremental.py`** 运行：

```bash
python run_incremental.py --task <TASK> [参数...]
```

### 必需参数（所有任务共享）

- `--task`: 任务类型 (`vehicle_config` | `road_opt` | `bus_opt`)
- `--mongo_uri`: MongoDB连接URI
- `--map_db`: 地图数据库名
- `--map_coll`: 地图集合名
- `--agent_db`: Agent数据库名
- `--agent_coll`: Agent集合名
- `--total_steps`: 仿真总步数（默认3600）
- `--interval`: 仿真间隔/秒（默认1）

---

## 算法一：仿真车辆配置

### 目标
测试不同车辆数对仿真指标的影响。

### 实现原理
在仿真启动前，通过MongoDB下采样生成目标数量的车辆集合，利用现有的"SimulatorConfig从Mongo读agent_coll"机制。

### 命令示例

```bash
# 单一车辆数
python run_incremental.py --task vehicle_config \
  --target_vehicle_count 2000 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1 \
  --seed 42

# 批量扫描
python run_incremental.py --task vehicle_config \
  --vehicle_counts 1000,2000,3000,5000 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1 \
  --seed 42
```

### 输出指标
- `avg_speed`: 平均车速 (m/s)
- `avg_travel_time`: 平均通行时间 (s)
- `avg_queue`: 平均等待队列（拥堵指数）

---

## 算法二：道路资源优化

### 目标
通过MaxPressure优化固定配时方案，对比三种场景：
1. **Baseline**：simulet-go内置fixed_program自然运行
2. **平均分配**：总周期固定，所有相位均分
3. **MP优化后固定**：两次仿真（MP统计 → 固化配时）

### 实现原理
- **MP算法**：基于车道排队数计算相位压力（`LaneService.GetLane -> total_queuing_vehicle_cnt`），选择最大压力相位
- **相位统计**：按周期收集各相位占用时长占比
- **固化配时**：将统计比例转化为固定时长，循环执行

### 命令示例

#### Scenario 1: Baseline（固定配时自然运行）
```bash
python run_incremental.py --task road_opt \
  --scenario fixed_program_baseline \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1
```

#### Scenario 2: 平均分配
```bash
python run_incremental.py --task road_opt \
  --scenario fixed_avg_split \
  --cycle_seconds 90 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1
```

#### Scenario 3: MP优化后固定
```bash
python run_incremental.py --task road_opt \
  --scenario fixed_from_mp \
  --cycle_seconds 90 \
  --min_green_seconds 5 \
  --mp_warmup_cycles 10 \
  --mp_collect_cycles 50 \
  --min_phase_seconds 3 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1
```

### 关键参数说明
- `--cycle_seconds`: 信号灯周期（秒）
- `--min_green_seconds`: 最小绿灯时间（秒）
- `--mp_warmup_cycles`: MP热身周期数（跳过前N个周期）
- `--mp_collect_cycles`: MP统计周期数（收集后K个周期）
- `--min_phase_seconds`: 相位最小时长约束（秒）

### 输出指标
- `avg_speed`: 平均车速 (m/s)
- `avg_travel_time`: 平均通行时间 (s)
- `avg_queue`: 平均等待队列（拥堵指数）
- `queue_p95`, `queue_max`: 队列分位数/峰值

---

## 算法三：公交车利用率提升

### 目标
通过公交加权MaxPressure提升公交利用率。

### 实现原理
- **公交利用率定义**：`U = (公交速度>阈值的时间) / (公交总运行时间)`
- **加权压力**：`pressure = Σ queue(in_lane) * (1 + alpha * bus_presence(in_lane))`
- 给包含公交的车道更高权重，驱动相位更偏向公交畅通

### 命令示例

#### Scenario 1: 随机固定配时（Baseline）
```bash
python run_incremental.py --task bus_opt \
  --scenario fixed_random \
  --bus_ids 101,205,999,1234 \
  --bus_speed_threshold 5.0 \
  --cycle_seconds 90 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1 \
  --seed 42
```

#### Scenario 2: 公交加权MP
```bash
python run_incremental.py --task bus_opt \
  --scenario bus_weighted_mp \
  --bus_ids 101,205,999,1234 \
  --bus_speed_threshold 5.0 \
  --bus_weight_alpha 2.0 \
  --cycle_seconds 90 \
  --min_green_seconds 5 \
  --mongo_uri mongodb://localhost:27017 \
  --map_db map_db --map_coll map_coll \
  --agent_db agent_db --agent_coll agent_coll \
  --total_steps 3600 --interval 1
```

### 关键参数说明
- `--bus_ids`: 公交车ID列表（逗号分隔）
- `--bus_speed_threshold`: 速度阈值 (m/s)，超过此值算"高效运行"
- `--bus_weight_alpha`: 公交权重系数（建议扫描：0.5, 1, 2, 4, 8）

### 输出指标
- `bus_utilization`: 公交利用率 [0, 1]
- `time_above_threshold`: 超过阈值的时间 (s)
- `total_time`: 公交总运行时间 (s)
- 附带通用指标（速度/队列）

---

## 验证计划矩阵

| 算法 | Scenario | 必需参数 | 对比目的 |
|-----|----------|---------|---------|
| 算法一 | - | `--vehicle_counts` | 不同车流强度的影响 |
| 算法二 | `fixed_program_baseline` | - | 原始固定配时 |
| 算法二 | `fixed_avg_split` | `--cycle_seconds` | 平均分配配时 |
| 算法二 | `fixed_from_mp` | `--cycle_seconds`, `--mp_*` | MP优化配时 |
| 算法三 | `fixed_random` | `--bus_ids` | 无优化baseline |
| 算法三 | `bus_weighted_mp` | `--bus_ids`, `--bus_weight_alpha` | 公交优化 |

---

## 使用的已有接口清单

### SimulationRunner封装的接口
- `OnlyClientSidecar.step()` - 推进仿真
- `LaneService.GetLane` - 获取车道排队数
- `RoadService.GetRoad` - 获取道路平均速度
- `PersonService.GetGlobalStatistics` - 获取全局统计
- `PersonService.GetPersons` - 获取车辆速度
- `LightService.SetTrafficLightPhase` - 设置信号灯相位

### 地图/拓扑接口
- `Engine.get_map()` - 获取地图数据
- `Engine.get_junction_phase_lanes` - 获取相位-车道映射

### MongoDB机制
- `SimulatorConfig(..., agent_coll=XXX)` - 利用现有的Mongo加载机制

---

## 注意事项

1. **不改现有代码**：所有新功能都在 `incremental_algos/` 下，不修改 `pycitysim/` 或 `trafficlight_rl/`
2. **Baseline定义**：算法二的baseline完全不控灯，让simulet-go的fixed_program自然运行
3. **拥堵指数口径**：使用 `total_queuing_vehicle_cnt`（排队数）
4. **二进制路径**：`trafficlight_rl.Engine` 默认找 `/home/nkk/work/bin/simulet-go-bin/...`，请确保路径可用或修改Engine源码

---

## 输出结果

每次运行会在 `results/<task>/<timestamp>/` 下生成：
- `experiment_config.json` - 完整参数快照
- `metrics.json` - 最终指标
- 各算法特有文件：
  - 算法一：`vehicles_{count}.json`（车辆ID列表）、`summary.json`（汇总）
  - 算法二：`mp_phase_statistics.json`（MP统计）、`fixed_timing_*.json`（配时方案）
  - 算法三：`bus_ids.json`（公交ID列表）

---

## 扩展建议

- **算法一**：支持upsample（复制车辆）或从Mongo标签读车辆子集
- **算法二**：支持指定路口子集优化（junction_scope参数）
- **算法三**：支持从Mongo读公交标签、更复杂的加权策略
