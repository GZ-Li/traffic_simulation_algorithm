# Single_Accident（单事故场景）

## 运行方式

运行Python脚本：

```bash
python3 /home/nkk/work/src/python/Single_Accident/simulation_validation.py \
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
- `--start`: 起始步长（默认0）
- `--interval`: 仿真步长间隔（默认1）
- `--output_sql_dsn`: SQL输出数据源（可选）
- `--output_job_prefix`: 输出任务前缀（默认"traffic_light_optimization_"）

## 代码逻辑

1. **路径准备**：
   - 需要预先运行 `get_path.py` 生成 `shortest_paths.json` 文件
   - 该文件包含从多个医院到单个事故点的最短路径信息
   - 路径格式：`{医院ID: [路径列表]}`

2. **仿真运行**：
   - 初始化仿真环境，连接MongoDB获取地图和车辆数据
   - 运行仿真到第60步（模拟事故发生的时间点）
   - 使用MaxPressure算法进行交通信号灯优化

3. **速度数据采集**：
   - 在第60步时，读取所有道路的平均速度
   - 对于每个医院到事故点的每条路径：
     - 提取路径中每条道路的ID
     - 从道路速度数据中获取对应道路的速度信息
     - 如果道路没有速度数据，尝试从引擎实时获取，否则使用默认值35 m/s

4. **结果输出**：
   - 将路径速度数据保存到 `path_speeds.json` 文件
   - 输出格式：`{医院ID: {路径ID: {道路ID: 速度}}}`
   - 日志保存在 `logs/` 目录下

## 主要文件

- `simulation_validation.py`: 主仿真脚本，用于获取路径速度数据
- `get_path.py`: 路径规划脚本，生成从医院到事故点的最短路径
- `path_speeds.json`: 输出文件，包含每条路径上各道路的速度信息
- `calculate_time.py`: 时间计算相关
- `Shaped.net.xml`: 路网文件

## 使用流程

1. 首先运行 `get_path.py` 生成最短路径文件：
   ```python
   python3 get_path.py
   ```

2. 然后运行 `simulation_validation.py` 获取路径速度数据：
   ```bash
   bash /home/nkk/work/src/run_single_accident.sh
   ```
