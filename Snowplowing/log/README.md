# Snowplowing（扫雪场景）

## 运行方式

使用根目录下的运行脚本：

```bash
bash /home/nkk/work/src/run_snowplowing.sh
```

或者直接运行Python脚本：

```bash
python3 /home/nkk/work/src/python/Snowplowing/simulation_validation.py \
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
- `--exp`: 实验名称（默认snow_test）

## 代码逻辑

1. **初始化阶段**：
   - 读取扫雪路径文件 `plowing_path/cleaned_roads_by_interval.json`，该文件包含不同时间间隔内已扫雪的道路列表
   - 初始化仿真环境，连接MongoDB获取地图和车辆数据
   - 将所有车辆初始速度设置为慢速（2 m/s，模拟积雪状态）

2. **前置步长**：
   - 运行20个步长，让仿真环境稳定

3. **扫雪模拟循环**：
   - 按时间间隔顺序处理扫雪任务（最多10个间隔）
   - 每个间隔内：
     - 根据扫雪路径文件，确定当前间隔已扫雪的道路集合
     - 将已扫雪道路上的车辆速度设置为正常速度（20 m/s）
     - 将未扫雪道路上的车辆速度保持为慢速（2 m/s）
     - 运行50个步长
     - 统计全局平均速度

4. **结果输出**：
   - 将每个间隔的全局平均速度保存到CSV文件
   - 日志保存在 `log/snow/{实验名称}/{时间戳}/` 目录下

## 主要文件

- `simulation_validation.py`: 主仿真脚本
- `get_path.py`: 路径规划相关工具
- `plowing_path/cleaned_roads_by_interval.json`: 扫雪路径结果
