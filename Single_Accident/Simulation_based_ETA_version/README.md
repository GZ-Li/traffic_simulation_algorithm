# Simulation-based ETA Version

基于仿真探针车的多OD行程时间（ETA）估算工具。通过在单次仿真中同时注入多辆探针车，并行追踪每对OD的到达情况，从而高效获取各起终点对的预计行程时间。

## 文件结构

```
Simulation_based_ETA_version/
├── get_od_eta.py          # 核心脚本：多OD探针注入与ETA采集
├── run_get_od_eta.sh      # 运行脚本
├── eta_res.json           # 最新运行的输出结果（JSON格式）
├── log/                   # 历史运行日志
└── case1/                 # 场景配置目录
    ├── location_input.json      # 场景输入：医院列表与事故地点
    ├── Shaped.net.xml           # 路网文件
    ├── get_od_eta_good.py       # 已验证可用的脚本版本
    ├── get_od_eta_bad.py        # 存在问题的脚本版本（供对比参考）
    ├── run_get_od_eta_good.sh   # 对应的运行脚本
    ├── run_get_od_eta_bad.sh
    └── addperson_direct_od_result_*.txt  # 历史运行的原始输出
```

## 工作原理

1. **读取场景配置**：从 `case1/location_input.json` 读取医院列表（`hospitals`）与事故地点（`accident`），生成 OD 对列表（每家医院 → 事故地点）。
2. **连接仿真引擎**：通过 `trafficlight_rl.engine` 模块与 MongoDB 中的地图和 Agent 数据建立仿真环境。
3. **热身步骤**：在注入探针前先推进若干仿真步骤（`--warmup_steps`）。
4. **注入探针车**：为每个 OD 对调用 `AddPerson` API，设置自动路由（`routes=[]`），由仿真引擎自动规划路径。
5. **并行追踪**：每个仿真步骤查询所有探针车的当前道路，直到到达终点或超时。
6. **计算并输出 ETA**：记录每辆探针车的出发时刻与到达时刻，差值即为 ETA（秒），保存为 JSON 并打印摘要。

## 快速开始

```bash
cd /home/nkk/work/src/python/Single_Accident/Simulation_based_ETA_version
bash run_get_od_eta.sh
```

或手动运行：

```bash
python get_od_eta.py \
  --warmup_steps 10 \
  --max_probe_steps 60 \
  --departure_stagger_steps 0
```

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mongo_uri` | `mongodb://...@172.16.40.166:27017` | MongoDB 连接地址 |
| `--map_db` | `tsingroc` | 地图数据库名 |
| `--map_coll` | `map_pku_wuhan_demo_1015c_rl` | 地图集合名 |
| `--agent_db` | `tsingroc` | Agent 数据库名 |
| `--agent_coll` | `person_test_20250724_1015cc` | Person 模板集合名 |
| `--start` | `0` | 仿真起始步编号 |
| `--interval` | `30` | 每步对应的仿真时间（秒） |
| `--warmup_steps` | `10` | 热身步骤数 |
| `--departure_delay_steps` | `1` | 热身结束后延迟几步再出发 |
| `--departure_stagger_steps` | `0` | 相邻OD探针出发的步间隔（0表示同时出发） |
| `--max_probe_steps` | `60` | 探针追踪的最大步骤数 |

## 场景配置

编辑 `case1/location_input.json` 以修改场景：

```json
{
    "hospitals": ["200000033", "200000569"],
    "accident": "200007001"
}
```

- `hospitals`：出发地道路ID列表（医院所在道路）
- `accident`：目的地道路ID（事故发生道路）

脚本会自动为每家医院生成一个 `医院 → 事故地点` 的OD对。

## 当前结果示例

以 `case1` 场景为例（2家医院 → 1处事故地点，共2条OD）：

**场景配置**

| 角色 | 道路ID |
|------|--------|
| 医院1 | `200000033` |
| 医院2 | `200000569` |
| 事故地点 | `200007001` |

**运行摘要（控制台输出）**

```
=== Multi-OD AddPerson Result Summary ===
total_ods:        2
injected_ok:      2
eta_available:    2
timeout_no_eta:   0
inject_failures:  0
```

**`eta_res.json` 输出**

```json
[
  {
    "od_index": 1,
    "start_road": "200000033",
    "end_road": "200007001",
    "person_id": 180499,
    "departure_time": 331,
    "started_time": 421,
    "arrived_time": 451,
    "eta_seconds": 30,
    "arrival_reason": "disappeared_after_departure"
  },
  {
    "od_index": 2,
    "start_road": "200000569",
    "end_road": "200007001",
    "person_id": 180500,
    "departure_time": 331,
    "started_time": 391,
    "arrived_time": 511,
    "eta_seconds": 120,
    "arrival_reason": "disappeared_after_departure"
  }
]
```

**结果解读**

| OD | 出发地 | 目的地 | ETA（秒） | ETA（分钟） |
|----|--------|--------|-----------|------------|
| 1  | 200000033（医院1） | 200007001（事故地点） | 30  | 0.5 |
| 2  | 200000569（医院2） | 200007001（事故地点） | 120 | 2.0 |

**路网距离分析（基于 `Shaped.net.xml`）**

基于路网拓扑使用 Dijkstra 算法计算最短路径距离与自由流行程时间：

| 医院 | 道路ID | 路网最短距离 | 自由流行程时间 | 仿真实测 ETA |
|------|--------|-------------|--------------|-------------|
| 医院1 | `200000033` | 984.9 m | 72.8 s | **30 s** |
| 医院2 | `200000569` | 783.5 m | 62.0 s | **120 s** |

> **注意**：路网距离显示医院2物理上更近（783.5 m vs 984.9 m），自由流时间也更短（62 s vs 72.8 s）；但仿真实测 ETA 却是医院1更快（30 s vs 120 s）。
>
> 可能原因：
> - 仿真在信号控制下，两条路线经过的交叉口等待时间不同；
> 说明该算法是有意义的。

---

## 输出格式

结果保存至 `eta_res.json`，同时打印摘要到标准输出。

### JSON结果字段说明

| 字段 | 说明 |
|------|------|
| `od_index` | OD对编号（从1开始） |
| `start_road` | 出发道路ID |
| `end_road` | 目的地道路ID |
| `person_id` | 注入后仿真引擎分配的Person ID |
| `departure_time` | 计划出发的仿真时刻（秒） |
| `started_time` | 探针车首次出现在道路上的时刻（秒） |
| `arrived_time` | 探针车到达终点的时刻（秒） |
| `eta_seconds` | 行程时间 = `arrived_time - started_time`（秒） |
| `arrival_reason` | 到达原因（见下表） |

### `arrival_reason` 取值

| 值 | 含义 |
|----|------|
| `reach_destination_road` | 探针车到达目的地道路 |
| `disappeared_after_departure` | 出发后消失（仿真引擎回收，视为到达） |
| `max_probe_steps_reached_no_eta` | 超出最大追踪步数，未获得ETA |
| `inject_exception: ...` | 注入时发生异常 |
| `not_injected` | 未尝试注入 |
| `not_arrived` | 未到达（中间状态，正常结束后不应出现） |

## 依赖

- Python 3.8+
- `pymongo`
- `trafficlight_rl`（本仓库内部模块，需确保路径正确）
- 可访问的 MongoDB 实例（存储地图数据与Agent模板）
