# Multi_Accident（多事故场景）

## 运行方式

运行Python脚本：

```bash
python3 /home/nkk/work/src/python/Multi_Accident/get_speed.py \
  --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
  --map_db tsingroc \
  --map_coll map_pku_wuhan_demo_1015c_rl \
  --agent_db tsingroc \
  --agent_coll person_test_20250724_1015cc \
  --interval 30
```

### 参数说明

- `--mongo_uri`: MongoDB连接URI
- `--map_db`: 地图数据库名
- `--map_coll`: 地图集合名
- `--agent_db`: 车辆数据库名
- `--agent_coll`: 车辆集合名
- `--interval`: 仿真步长间隔（默认30）

## 代码逻辑

1. **路线规划**：
   - 找到每个医院到达每个事故点的路径

2. **仿真运行**：
   - 初始化仿真环境，连接MongoDB获取地图和车辆数据
   - 运行仿真到第60步（模拟事故发生的时间点）

3. **速度数据采集**：
   - 在第60步时，读取所有道路的平均速度
   - 对于每个医院到每个事故点的每条路径：
     - 提取路径中每条道路的ID
     - 从道路速度数据中获取对应道路的速度信息
     - 如果道路没有速度数据，使用默认值35 m/s

4. **结果输出**：
   - 将路径速度数据保存到 `path_speeds.json` 文件
   - 输出格式：`{医院ID: {事故ID: {路径ID: {道路ID: 速度}}}}`
   - 根据每个road的长度与平均速度计算预计到达时间

## 主要文件

- `get_speed.py`: 主脚本，用于获取路径速度数据
- `get_path.py`: 路径规划脚本，生成从医院到事故点的最短路径
- `path_speeds.json`: 输出文件，包含每条路径上各道路的速度信息
- `optimization.py`: 优化算法相关
- `calculate_time.py`: 时间计算相关

## 使用流程

1. 首先运行 `get_path.py` 生成最短路径文件：
   ```python
   python3 get_path.py
   ```

2. 然后运行 `get_speed.py` 获取路径速度数据：
   ```bash
   bash /home/nkk/work/src/run_multi_accident.sh
   ```
3. 然后使用`calculate_time.py`和`optimization.py`分别对预计到达时间进行计算和对任务分配进行优化；
   ```python
   python3 calculate_time.py
   python3 optimization.py
   ```

## 结果展示（当前示例）

以下结果来自本目录现有输出：

- `optimization_vs_greedy.json`
- `optimized_assignment.png`
- `greedy_assignment.png`

### 关键指标

- LP Status: `Optimal`
- Greedy makespan: `4531.53 s`
- Optimized makespan: `2014.48 s`
- 绝对改进: `2517.06 s`
- 相对改进: `55.55%`

### 分配结果对比

Optimized assignment（并行程度更高）：

- `200002126 -> 200001367`
- `200001486 -> 200001481`
- `200001174 -> 200001458`
- `200000268 -> 200001458`
- `200000593 -> 200001452`
- `200002565 -> 200002141`

Greedy assignment（任务集中在一个医院，串行瓶颈明显）：

- `200001486 -> 200001367, 200001458, 200002141, 200001481, 200001452`

### 可视化结果

优化分配图：

![optimized_assignment](optimized_assignment.png)

Greedy 分配图：

![greedy_assignment](greedy_assignment.png)

说明：该样例中优化方案把任务分散到多家医院并行执行，因此最大完成时间（makespan）显著下降。