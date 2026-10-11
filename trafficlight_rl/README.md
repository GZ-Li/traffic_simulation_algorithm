# 交通信号灯控制算法

本项目实现了三种交通信号灯控制算法：MaxPressure（基于压力的传统算法）、PPO（基于深度强化学习的策略梯度算法）和MPLight（基于深度强化学习的算法）。

## 算法概述

### MaxPressure算法
- 基于车道压力的自适应控制算法
- 无需训练，实时计算最优相位
- 适合作为强化学习算法的基线方法

### PPO算法
- 基于Proximal Policy Optimization的深度强化学习算法
- 采用策略梯度方法，支持连续优化
- 使用Actor-Critic架构，同时优化策略和价值函数
- 支持多路口协同控制，具有良好的训练稳定性

### MPLight算法
- 基于深度强化学习的交通信号灯控制算法
- 通过Q-learning方法优化交通信号灯的相位选择
- 支持多路口协同优化，考虑路口间的空间关系
- 多种奖励函数设计（压力、排队长度等）

## 环境准备

### 依赖安装

使用poetry安装依赖

```bash
cd ..
poetry install
. <(poetry env activate)
```

### 模拟器二进制准备

需要将simulet-go和syncer二进制文件复制到`~/.local/bin`目录下，路径分别为：
- `~/.local/bin/simulet-go`
- `~/.local/bin/syncer`

### 数据库准备

确保MongoDB数据库中有相应的地图和车辆数据：
- 地图数据存储在指定的数据库和集合中
- 车辆数据存储在指定的数据库和集合中
- 数据格式需要与simulet-go模拟器兼容

## MaxPressure算法

MaxPressure是一种基于车道压力的自适应交通信号灯控制算法，通过动态计算车道压力来选择最优相位，无需训练即可实现智能信控。

### 参数说明

#### 必需参数

- `--mongo_uri`: MongoDB连接URI（必需）
- `--map_db`: 地图数据库名（必需）
- `--map_coll`: 地图集合名（必需）
- `--agent_db`: 车辆数据库名（必需）
- `--agent_coll`: 车辆集合名（必需）

#### 仿真参数

- `--start`: 仿真开始步数（默认：0）
- `--steps`: 仿真总步数（默认：3600）
- `--interval`: 信号灯控制间隔（默认：30秒）

#### 其他参数

- `--exp`: 实验名称（可选）
- `--output_sql_dsn`: 输出SQL DSN（可选），如果为空，则不输出到SQL
- `--output_job_prefix`: 输出Job名前缀（可选），示例：`maxpressure_`，最终Job名称为`maxpressure_0`、`maxpressure_1`等
- `--output_bbox`: 输出范围，格式为: min_longitude,min_latitude,max_longitude,max_latitude（可选）

### 算法原理

MaxPressure算法的核心是车道压力计算：

```
压力 = 前驱车道密度 - 后继车道密度
密度 = 车辆数量 / 车道长度
```

算法会：
1. 计算所有车道的压力值
2. 为每个相位计算总压力（绿灯车道压力之和）
3. 选择压力最大的相位作为下一个相位
4. 如果最大压力相位未变化且未达到最大重复次数，则延长当前相位

### 运行示例

#### 基本运行

```bash
python run_maxpressure.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection"
```

#### 自定义参数运行

```bash
python run_maxpressure.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --exp my_maxpressure_experiment \
    --steps 7200 \
    --interval 60 \
    --output_sql_dsn "postgres://user:password@host:port/database" \
    --output_job_prefix "maxpressure_" \
    --output_bbox "114,30.3,115,30.7"
```

#### 快速调试运行

```bash
python run_maxpressure.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --steps 1800 \
    --interval 30
```

### 输出结果

程序运行后会在`log/maxpressure/`目录下生成以下文件：

- `cmd.sh`: 运行命令记录
- `args.json`: 参数配置文件
- `info.log`: 每轮仿真的统计信息，每一行格式为`ATT,Throughput,Timestamp`

#### 监控指标

- **ATT (Average Travel Time)**: 平均行驶时间
- **Throughput**: 吞吐量（完成车辆数）
- **ATT_finished**: 已完成车辆的平均行驶时间

### 算法特点

1. 无需训练，适用于实时控制
2. 右转车道不参与压力计算，避免影响算法判断
3. 算法会自动处理相位过渡，确保交通安全
4. 适合作为深度强化学习算法的基线方法进行对比

## MPLight算法

MPLight是基于深度强化学习的交通信号灯控制算法，通过DQN方法学习最优的信号灯控制策略。

### 参数说明

#### 必需参数

- `--mongo_uri`: MongoDB连接URI（必需）
- `--map_db`: 地图数据库名（必需）
- `--map_coll`: 地图集合名（必需）
- `--agent_db`: 车辆数据库名（必需）
- `--agent_coll`: 车辆集合名（必需）

#### 仿真参数

- `--start`: 仿真开始步数（默认：0）
- `--steps`: 仿真总步数（默认：3600）
- `--interval`: 信号灯控制间隔（默认：30秒）

#### 训练参数

- `--training_step`: 训练总步数（默认：10000000）
- `--training_start`: 开始训练的步数（默认：2000）
- `--training_freq`: 训练频率，每多少步训练一次（默认：10）
- `--target_freq`: 目标网络更新频率（默认：100）
- `--explore_steps`: 探索步数，用于epsilon衰减（默认：100000）

#### 网络参数

- `--lr`: 学习率（默认：3e-4）
- `--mlp`: MLP网络结构，用逗号分隔（默认："256,256"）
- `--gamma`: 折扣因子（默认：0.99）

#### 经验回放参数

- `--batchsize`: 批量大小（默认：256）
- `--buffer_size`: 经验回放缓冲区大小（默认：2048）
- `--mini_batch_size`: 小批量大小（默认：1024）

#### 其他参数

- `--exp`: 实验名称（可选）
- `--alpha`: 邻居奖励的平衡系数（默认：0.2）
- `--output_sql_dsn`: 输出SQL DSN（可选），如果为空，则不输出到SQL
- `--output_job_prefix`: 输出Job名前缀（可选），示例：`mplight_`，最终Job名称为`mplight_0`、`mplight_1`等
- `--output_bbox`: 输出范围，格式为: min_longitude,min_latitude,max_longitude,max_latitude（可选）

### 运行示例

#### 基本运行

```bash
python run_mplight.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection"
```

#### 自定义参数运行

```bash
python run_mplight.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --exp my_experiment \
    --steps 7200 \
    --interval 60 \
    --lr 1e-4 \
    --batchsize 512 \
    --training_step 5000000 \
    --alpha 0.3 \
    --output_sql_dsn "postgres://user:password@host:port/database" \
    --output_job_prefix "mplight_" \
    --output_bbox "114,30.3,115,30.7"
```

#### 快速调试运行

```bash
python run_mplight.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection"
```

### 输出结果

程序运行后会在`log/mplight/`目录下生成以下文件：

- `cmd.sh`: 运行命令记录
- `args.json`: 参数配置文件
- `info.log`: 每轮仿真的统计信息，每一行格式为`ATT,Throughput,Timestamp`
- TensorBoard日志文件

#### 监控指标

- **ATT (Average Travel Time)**: 平均行驶时间
- **Throughput**: 吞吐量（完成车辆数）
- **AQL (Average Queue Length)**: 平均排队长度
- **Reward**: 奖励值

#### 使用TensorBoard查看结果

```bash
tensorboard --port 8888 --logdir log/mplight/
```

## PPO算法

PPO (Proximal Policy Optimization) 是一种基于策略梯度的深度强化学习算法，通过限制策略更新幅度来保证训练稳定性。该算法采用Actor-Critic架构，能够有效处理多路口交通信号灯控制问题。

### 参数说明

#### 必需参数

- `--mongo_uri`: MongoDB连接URI（必需）
- `--map_db`: 地图数据库名（必需）
- `--map_coll`: 地图集合名（必需）
- `--agent_db`: 车辆数据库名（必需）
- `--agent_coll`: 车辆集合名（必需）

#### 仿真参数

- `--start`: 仿真开始步数（默认：0）
- `--steps`: 仿真总步数（默认：3600）
- `--interval`: 信号灯控制间隔（默认：30秒）

#### 训练参数

- `--total-timesteps`: 总训练步数（默认：100000000）
- `--num-steps`: 环境rollout步数（默认：128）
- `--lr`: 学习率（默认：3e-4）
- `--lr-scale`: 学习率缩放因子（默认：0）
- `--lr-anneal`: 学习率退火参数（默认：0）
- `--gamma`: 折扣因子（默认：0.99）
- `--gae-lambda`: GAE λ参数（默认：0.9）
- `--update-epochs`: 策略更新轮数（默认：16）
- `--num-minibatches`: 小批量数量（默认：8）

#### 网络参数

- `--mlp`: MLP网络结构，用逗号分隔（默认："256,256"）

#### PPO特定参数

- `--gae`: 是否使用GAE进行优势估计（默认：True）
- `--clip-coef`: 策略剪切系数（默认：0.25）
- `--ent-coef`: 熵正则化系数（默认：0）
- `--vf-coef`: 价值函数损失系数（默认：0.5）
- `--max-grad-norm`: 梯度裁剪最大范数（默认：0.5）
- `--norm-adv`: 是否标准化优势（默认：False）

#### 其他参数

- `--exp`: 实验名称（可选）
- `--suffix`: 实验后缀（可选）
- `--seed`: 随机种子（默认：43）
- `--load`: 模型检查点路径（默认：""）
- `--load-step`: 加载检查点的步数（默认：-1，表示最新）
- `--save-interval`: 检查点保存间隔（默认：1000）
- `--debug`: 调试模式（默认：False）
- `--eval`: 评估模式（默认：False）
- `--sample`: 评估时是否采样（默认：False）
- `--output_sql_dsn`: 输出SQL DSN（可选），如果为空，则不输出到SQL
- `--output_job_prefix`: 输出Job名前缀（可选），示例：`ppo_`，最终Job名称为`ppo_0`、`ppo_1`等
- `--output_bbox`: 输出范围，格式为: min_longitude,min_latitude,max_longitude,max_latitude（可选）

### 算法原理

PPO算法的核心思想是：

1. **策略网络（Actor）**: 根据当前状态选择动作
2. **价值网络（Critic）**: 估计状态价值函数
3. **剪切目标函数**: 防止策略更新过大，保证训练稳定性
4. **GAE优势估计**: 减少方差，提高训练效率

奖励函数设计：
```
奖励 = -平均等待车辆数
```

观测空间包括：
- 车道车辆数量
- 车道等待车辆数量
- 车道密度
- 车道等待密度

### 运行示例

#### 基本训练

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection"
```

#### 自定义参数训练

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --exp my_ppo_experiment \
    --steps 7200 \
    --interval 60 \
    --lr 1e-4 \
    --total-timesteps 50000000 \
    --num-steps 256 \
    --update-epochs 10 \
    --clip-coef 0.2 \
    --output_sql_dsn "postgres://user:password@host:port/database" \
    --output_job_prefix "ppo_" \
    --output_bbox "114,30.3,115,30.7"
```

#### 快速调试训练

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --debug \
    --steps 1800 \
    --total-timesteps 100000
```

#### 加载检查点继续训练

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --load log/ppo/my_experiment/20240101-120000 \
    --load-step 50000
```

#### 评估模式

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --eval \
    --load log/ppo/my_experiment/20240101-120000
```

#### 采样评估模式

```bash
python run_ppo.py \
    --mongo_uri "mongodb://user:password@host:port" \
    --map_db "map_db" \
    --map_coll "map_collection" \
    --agent_db "agent_db" \
    --agent_coll "agent_collection" \
    --eval \
    --sample \
    --load log/ppo/my_experiment/20240101-120000
```

### 输出结果

程序运行后会在`log/ppo/`目录下生成以下文件：

- `cmd.sh`: 运行命令记录
- `args.json`: 参数配置文件
- `info.log`: 每轮仿真的统计信息，每一行格式为`ATT,Throughput,Timestamp`
- `msg.log`: 当前训练状态信息
- `code/`: 代码备份目录
- `ckpts/`: 模型检查点目录
- `load.pt`: 加载的检查点文件（如果使用了--load）
- TensorBoard日志文件

#### 监控指标

- **ATT-d (Average Travel Time - Departed)**: 出发车辆平均行驶时间
- **ATT-f (Average Travel Time - Finished)**: 完成车辆平均行驶时间
- **Throughput**: 吞吐量（完成车辆数）
- **Reward**: 奖励值
- **Value Loss**: 价值函数损失
- **Policy Loss**: 策略损失
- **Entropy**: 策略熵
- **Explained Variance**: 解释方差

#### 使用TensorBoard查看结果

```bash
tensorboard --port 8888 --logdir log/ppo/
```

或者使用输出的命令：
```bash
# 程序会输出类似以下命令
tensorboard --port 8888 --logdir /absolute/path/to/log/ppo/experiment/20240101-120000
```

#### 动态调整学习率

在训练过程中，可以通过在日志目录下创建`lr.txt`文件来动态调整学习率：

```bash
echo "1e-5" > log/ppo/my_experiment/20240101-120000/lr.txt
```

### 算法特点

1. **稳定性**: PPO通过剪切目标函数保证训练稳定性，避免策略崩溃
2. **效率**: 使用GAE减少方差，提高样本效率
3. **可扩展性**: 支持多路口协同控制，适合大规模路网
4. **灵活性**: 支持多种网络架构和超参数配置
5. **实时性**: 训练完成后可以实时控制交通信号灯

### 与其他算法的比较

| 特性 | MaxPressure | PPO | MPLight |
|------|-------------|-----|---------|
| 训练需求 | 无需训练 | 需要训练 | 需要训练 |
| 算法类型 | 基于规则 | 策略梯度 | 价值函数 |
| 稳定性 | 高 | 中高 | 中 |
| 适应性 | 中 | 高 | 高 |
| 计算复杂度 | 低 | 中 | 中 |

## 注意事项

### 通用要求

1. 确保MongoDB数据库中有相应的地图和车辆数据
2. 大规模路网训练可能需要较长时间，建议先用小规模数据测试
3. 确保MongoDB连接URI正确且有相应的访问权限

### MaxPressure特定要求

- 适用于实时控制，无需GPU支持
- 右转车道不参与压力计算

### PPO特定要求

- 训练需要GPU支持，确保CUDA环境正确配置
- 长时间训练建议使用TensorBoard监控训练过程
- 可以通过动态调整学习率来优化训练效果
- 建议先在小规模数据上验证超参数设置

### MPLight特定要求

- 训练需要GPU支持，确保CUDA环境正确配置
- 可以通过调整`--alpha`参数来平衡局部和全局奖励
