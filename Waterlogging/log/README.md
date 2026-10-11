# Waterlogging（内涝场景）

本项目主要用于城市内涝场景下的积水点识别、排涝策略的优先级生成以及策略的仿真验证。

## 📂 主要文件

- **`waterlogging_point_identification/`**
  内涝点识别相关数据文件目录。

- **`Waterlogging_Priority.py`**
  排涝策略生成脚本。
  - **功能**：输入涝点 ID 或经纬度，输出建议的涝点处理优先级排序。

- **`simulation_validation.py`**
  模拟验证脚本。
  - **功能**：连接数据库读取地图与 Agent 信息，在仿真环境中验证排涝策略的有效性。

## 🚀 运行方式

### 1. 生成排涝策略

直接运行 Python 脚本，根据提示输入数据：

```bash
python Waterlogging_Priority.py
```

### 2. 模拟验证
运行 simulation_validation.py 需要指定 MongoDB 数据库连接以及地图和 Agent 的配置信息。

完整运行命令示例：

```
python3 simulation_validation.py \
  --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
  --map_db tsingroc \
  --map_coll map_pku_wuhan_demo_1015c_rl \
  --agent_db tsingroc \
  --agent_coll person_test_20250724_1015cc \
  --interval 30 \
  --order best
```

