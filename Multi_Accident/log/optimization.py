import json
from pulp import LpProblem, LpMinimize, LpVariable, lpSum, LpStatus, PULP_CBC_CMD

DATA_FILE = "path_time_length.json"  # 你的 JSON 数据文件

# -----------------------------
# 读取数据
# -----------------------------
with open(DATA_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

hospitals = list(data.keys())

# 取所有事故点并集
accidents = set()
for h in hospitals:
    accidents.update(data[h].keys())
accidents = list(accidents)

# -----------------------------
# 构造 response_times[h][a] = 最短路径时间
# -----------------------------
response_times = {}
for h in hospitals:
    response_times[h] = {}
    for a in accidents:
        if a not in data[h]:
            response_times[h][a] = float("inf")
            continue
        paths = data[h][a]
        min_time = min(info["total_time"] for info in paths.values())
        response_times[h][a] = min_time

# -----------------------------
# LP 优化求解最小最大响应时间
# -----------------------------
prob = LpProblem("Hospital_Accident_Assignment", LpMinimize)

x = LpVariable.dicts(
    "assign",
    ((h, a) for h in hospitals for a in accidents),
    cat="Binary"
)
T = LpVariable("max_time", lowBound=0)

prob += T, "minimize_max_response_time"

# 每个医院最多负责 1 个事故
for h in hospitals:
    prob += lpSum(x[h, a] for a in accidents) <= 1, f"hospital_{h}_max_one"

# 每个事故至少有 1 个医院负责
for a in accidents:
    prob += lpSum(x[h, a] for h in hospitals) >= 1, f"accident_{a}_covered"

# 响应时间约束
for h in hospitals:
    for a in accidents:
        if response_times[h][a] != float("inf"):
            prob += T >= response_times[h][a] * x[h, a], f"time_{h}_{a}"

print("Solving LP optimization...")
prob.solve(PULP_CBC_CMD(msg=0))
print(f"LP Status: {LpStatus[prob.status]}, Min-max response time: {T.varValue:.2f}s")

# -----------------------------
# 构造优化分配
# -----------------------------
opt_assign = {}
for h in hospitals:
    for a in accidents:
        if x[h, a].varValue == 1:
            if h not in opt_assign:
                opt_assign[h] = []
            opt_assign[h].append(a)

# -----------------------------
# 贪心最短时间分配（一个事故选最快医院）
# -----------------------------
greedy_assign = {}
for a in accidents:
    best_h = min(hospitals, key=lambda h: response_times[h][a])
    if best_h not in greedy_assign:
        greedy_assign[best_h] = []
    greedy_assign[best_h].append(a)

# -----------------------------
# 调度模拟函数
# -----------------------------
def simulate_schedule(assignments, response_times):
    """
    assignments: dict[hospital] = [accident1, accident2, ...]
    response_times[h][a]: 行驶时间
    返回：
        finish_times[a] = 事故完成时间
        makespan = 所有事故完成时间最大值
    """
    finish_times = {}
    for h, accidents_list in assignments.items():
        t = 0
        for a in accidents_list:
            t += response_times[h][a]  # 串行累加
            finish_times[a] = t
    makespan = max(finish_times.values())
    return finish_times, makespan

# -----------------------------
# 计算两种方案的完成时间
# -----------------------------
greedy_finish, greedy_makespan = simulate_schedule(greedy_assign, response_times)
opt_finish, opt_makespan = simulate_schedule(opt_assign, response_times)

# -----------------------------
# 输出对比结果
# -----------------------------
print("\n=== Comparison ===")
print(f"Greedy makespan:   {greedy_makespan:.2f} s")
print(f"Optimized makespan:{opt_makespan:.2f} s")
print(f"Improvement:       {greedy_makespan - opt_makespan:.2f} s\n")

print("Greedy assignment completion times:")
for a, t in greedy_finish.items():
    print(f"  Accident {a}: {t:.2f} s")

print("\nOptimized assignment completion times:")
for a, t in opt_finish.items():
    print(f"  Accident {a}: {t:.2f} s")

# -----------------------------
# 保存优化结果
# -----------------------------
optimization_result = {
    "LP_status": LpStatus[prob.status],
    "max_response_time": T.varValue,
    "optimized_assignment": opt_assign,
    "greedy_assignment": greedy_assign,
    "response_times": response_times,
    "greedy_finish_times": greedy_finish,
    "optimized_finish_times": opt_finish,
    "greedy_makespan": greedy_makespan,
    "optimized_makespan": opt_makespan
}

with open("optimization_vs_greedy.json", "w", encoding="utf-8") as f:
    json.dump(optimization_result, f, indent=2, ensure_ascii=False)

print("\nResults saved to optimization_vs_greedy.json")
