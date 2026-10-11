import json
import pandas as pd

# 读取优化结果和路径
with open('optimization_result.json', 'r', encoding='utf-8') as f:
    opt_result = json.load(f)

with open('shortest_paths.json', 'r', encoding='utf-8') as f:
    paths_data = json.load(f)

print("="*80)
print("OPTIMIZATION RESULT ANALYSIS")
print("="*80)

print(f"\nOptimization Status: {opt_result['status']}")
print(f"Maximum Response Time (makespan): {opt_result['max_response_time']:.0f} edges")

print("\n" + "-"*80)
print("Hospital-to-Accident Assignment:")
print("-"*80)

for hospital, accident in opt_result['assignment'].items():
    response_time = opt_result['response_times'][hospital][accident]
    print(f"\n{hospital} -> {accident}")
    print(f"  Response time: {response_time} edges")
    
    # 显示最短路径信息
    if isinstance(paths_data[hospital][accident], list) and len(paths_data[hospital][accident]) > 0:
        best_path = paths_data[hospital][accident][0]
        path = best_path['path']
        print(f"  Best path: {' -> '.join(path[:3])}...{' -> '.join(path[-3:])}") if len(path) > 6 else print(f"  Best path: {' -> '.join(path)}")
        print(f"  Number of edges: {best_path['num_edges']}")

print("\n" + "-"*80)
print("Unassigned Hospitals:")
print("-"*80)
for hospital in opt_result['unassigned_hospitals']:
    print(f"  - {hospital}")

# 创建响应时间矩阵表格
print("\n" + "-"*80)
print("Response Time Matrix (all hospitals to all accidents):")
print("-"*80)

response_matrix = opt_result['response_times']
df = pd.DataFrame(response_matrix).T
print(df.to_string())

print("\n" + "="*80)
print("Summary:")
print("="*80)
print(f"Total hospitals: 6")
print(f"Total accidents: 5")
print(f"Optimal makespan: {opt_result['max_response_time']:.0f} edges")
print(f"All accidents are covered: {all(a in opt_result['assignment'].values() for a in ['200002141', '200001458', '200001481', '200001367', '200001452'])}")