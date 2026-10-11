import json
from pulp import LpProblem, LpMinimize, LpVariable, lpSum, LpStatus, PULP_CBC_CMD

DATA_FILE = "case1/time_matrix copy.json"  # 直接的时间矩阵 JSON 文件

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

# 构造 response_times[h][a] - 直接从时间矩阵提取
response_times = {}
for h in hospitals:
    response_times[h] = {}
    for a in accidents:
        if a not in data[h]:
            response_times[h][a] = float("inf")
        else:
            response_times[h][a] = data[h][a]

print(f"Hospitals: {hospitals}")
print(f"Accidents: {accidents}\n")

# 打印响应时间矩阵
print("Response Time Matrix (seconds):")
print(f"{'Hospital':<15}", end="")
for a in accidents:
    print(f"{a:<15}", end="")
print()
for h in hospitals:
    print(f"{h:<15}", end="")
    for a in accidents:
        t = response_times[h][a]
        if t == float("inf"):
            print(f"{'inf':<15}", end="")
        else:
            print(f"{t:<15.0f}", end="")
    print()
print()

# -----------------------------
# LP 优化求解最小最大响应时间
# -----------------------------
print("Solving LP optimization...")
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

prob.solve(PULP_CBC_CMD(msg=0))
print(f"LP Status: {LpStatus[prob.status]}")
print(f"Min-max response time (LP): {T.varValue:.2f}s\n")

# 构造优化分配
opt_assign = {}
for h in hospitals:
    for a in accidents:
        if x[h, a].varValue == 1:
            if h not in opt_assign:
                opt_assign[h] = []
            opt_assign[h].append(a)

# 打印 LP 分配结果
print("LP Optimized Assignment:")
for h in hospitals:
    if h in opt_assign:
        print(f"  {h}: {opt_assign[h]}")
    else:
        print(f"  {h}: []")
print()

# -----------------------------
# 贪心最短时间分配（一个事故选最快医院）
# -----------------------------
greedy_assign = {}
for a in accidents:
    best_h = min(hospitals, key=lambda h: response_times[h][a])
    if best_h not in greedy_assign:
        greedy_assign[best_h] = []
    greedy_assign[best_h].append(a)

print("Greedy Assignment (each accident -> fastest hospital):")
for h in hospitals:
    if h in greedy_assign:
        print(f"  {h}: {greedy_assign[h]}")
    else:
        print(f"  {h}: []")
print()

# -----------------------------
# 调度模拟函数
# -----------------------------
def simulate_schedule(assignments, response_times):
    """
    模拟串行调度：每个医院依次处理分配给它的事故
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
    
    if finish_times:
        makespan = max(finish_times.values())
    else:
        makespan = 0
    
    return finish_times, makespan

# 计算两种方案的完成时间
greedy_finish, greedy_makespan = simulate_schedule(greedy_assign, response_times)
opt_finish, opt_makespan = simulate_schedule(opt_assign, response_times)

# -----------------------------
# 输出对比结果
# -----------------------------
print("=" * 60)
print("COMPARISON: Greedy vs Optimized")
print("=" * 60)
print(f"Greedy makespan (max completion time):   {greedy_makespan:.2f} s")
print(f"Optimized makespan (max completion time):{opt_makespan:.2f} s")
improvement = greedy_makespan - opt_makespan
improvement_pct = (improvement / greedy_makespan * 100) if greedy_makespan > 0 else 0
print(f"Improvement:                              {improvement:.2f} s ({improvement_pct:.1f}%)\n")

print("Greedy assignment completion times:")
for a in sorted(greedy_finish.keys()):
    print(f"  {a}: {greedy_finish[a]:.2f} s")

print("\nOptimized assignment completion times:")
for a in sorted(opt_finish.keys()):
    print(f"  {a}: {opt_finish[a]:.2f} s")

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
    "optimized_makespan": opt_makespan,
    "improvement": improvement,
    "improvement_percentage": improvement_pct
}

output_file = "case1/optimization_result.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(optimization_result, f, indent=2, ensure_ascii=False)

print(f"\nResults saved to {output_file}")
