import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
from math import radians, cos, sin, asin, sqrt
from nltk.corpus.reader import PrettyLazyIteratorList
from sklearn.cluster import KMeans
from math import radians, cos, sin, asin, sqrt, ceil
import heapq
from math import ceil
from sklearn.mixture import GaussianMixture
# ================= 配置参数 =================
XML_FILE_PATH = '/home/nkk/work/src/python/Waterlogging/Shaped.net.xml'
CENTER_LAT = 30.481075
CENTER_LON = 114.491183
RADIUS_KM = 10
TARGET_COUNT = 50  # 【新需求】我们需要30个接驳点
K_CLUSTERS = 4

# ================= 工具函数 =================
def haversine(lon1, lat1, lon2, lat2):
    """ 计算两点间球面距离 (km) """
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371
    return c * r



def generate_random_shuttle_stops(xml_path, center_lat, center_lon, radius_km, target_n=30):
    """
    解析 SUMO .net.xml，先找出所有合法路网节点，然后随机采样 target_n 个。
    """
    print(f">>> 正在解析 SUMO 网络文件: {xml_path} ...")

    try:
        context = ET.iterparse(xml_path, events=('start', 'end'))
        context = iter(context)
        event, root = next(context)
    except Exception as e:
        print(f"解析错误: {e}")
        return pd.DataFrame()

    # --- 1. 获取坐标转换参数 ---
    location_info = {}
    for event, elem in context:
        if event == 'end' and elem.tag == 'location':
            location_info = elem.attrib
            root.clear()
            break

    if not location_info:
        raise ValueError("未在 XML 中找到 <location> 标签！")

    orig_bounds = [float(x) for x in location_info['origBoundary'].split(',')]
    conv_bounds = [float(x) for x in location_info['convBoundary'].split(',')]
    min_lon, min_lat, max_lon, max_lat = orig_bounds
    min_x, min_y, max_x, max_y = conv_bounds

    print(f"    地图范围: 经度 {min_lon:.4f}~{max_lon:.4f}, 纬度 {min_lat:.4f}~{max_lat:.4f}")

    def sumo_xy_to_lonlat(x, y):
        x_ratio = (x - min_x) / (max_x - min_x) if (max_x - min_x) != 0 else 0
        y_ratio = (y - min_y) / (max_y - min_y) if (max_y - min_y) != 0 else 0
        lon = min_lon + x_ratio * (max_lon - min_lon)
        lat = min_lat + y_ratio * (max_lat - min_lat)
        return lat, lon

    # --- 2. 遍历并建立“合法候选池” ---
    valid_pool = []

    for event, elem in context:
        if event == 'end' and elem.tag == 'junction':
            j_id = elem.get('id')
            j_type = elem.get('type', '')
            inc_lanes = elem.get('incLanes', '')

            # [关键] 严苛的过滤条件：确保一定在路径上
            # 1. 排除内部虚拟节点 (internal)
            # 2. 排除孤立节点 (没有 incLanes 表示车开不进去)
            if j_id.startswith(':') or j_type == 'internal' or not inc_lanes:
                elem.clear()
                continue

            try:
                x = float(elem.get('x'))
                y = float(elem.get('y'))
                lat, lon = sumo_xy_to_lonlat(x, y)
                dist = haversine(center_lon, center_lat, lon, lat)

                # 3. 距离筛选
                if dist <= radius_km:
                    valid_pool.append({
                        'Cand_ID': j_id,
                        'Lat': lat,
                        'Lon': lon,
                        'Dist_Venue': round(dist, 4),
                        'Max_Capacity': np.random.randint(1000, 5000),  # 随机赋予容量
                        'Current_Used': 0
                    })
            except (ValueError, TypeError):
                pass
            elem.clear()

    # --- 3. 随机采样 (核心修改) ---
    total_found = len(valid_pool)
    print(f"    初步筛选：在 5km 范围内且在路网上的有效节点共有 {total_found} 个。")

    if total_found == 0:
        print("    [错误] 未找到任何符合条件的节点！请检查中心点坐标是否在地图范围内。")
        return pd.DataFrame()

    df_pool = pd.DataFrame(valid_pool)

    # 如果找到的点多于需要的点，进行随机采样
    if total_found > target_n:
        print(f"    >>> 资源池充足，正在随机抽取 {target_n} 个候补点...")
        # replace=False 表示不重复抽取
        df_final = df_pool.sample(n=target_n, random_state=2026, replace=False)
    else:
        print(f"    [警告] 有效节点总数 ({total_found}) 不足 {target_n} 个，返回所有可用节点。")
        df_final = df_pool

    # 按距离排序一下，看起来整齐
    df_final = df_final.sort_values('Dist_Venue').reset_index(drop=True)

    return df_final


# ================= 执行 =================
df_candidates = generate_random_shuttle_stops(
    XML_FILE_PATH,
    CENTER_LAT,
    CENTER_LON,
    RADIUS_KM,
    TARGET_COUNT
)

print(f"\n=== 最终生成的 {len(df_candidates)} 个接驳点候补集 ===")
pd.set_option('display.max_columns', None)
print(df_candidates.head(10))  # 打印前10个看看

# 验证：确保没有重复的ID
if df_candidates['Cand_ID'].nunique() != len(df_candidates):
    print("警告：存在重复ID！")
else:

    print("验证通过：所有接驳点ID唯一。")



df_flow = pd.read_csv('/home/nkk/work/src/python/concert_evacuation/human_flow_simulation_wuhan_realod.csv')

# ================= 步骤2：人群聚类 (优化版：GMM) =================
print("正在聚类人群方向 (使用高斯混合模型)...")
X = df_flow[['end_lat', 'end_lon']]

# 使用 GaussianMixture 替代 KMeans
# n_components 对应原来的 n_clusters
# covariance_type='full' 允许每个簇有自己独立的形状（椭圆），这是比 KMeans 灵活的关键
gmm = GaussianMixture(n_components=K_CLUSTERS,
                      random_state=42,
                      covariance_type='full',
                      max_iter=100)

# GMM 的 fit_predict 接口与 KMeans 一致
df_flow['Cluster_ID'] = gmm.fit_predict(X)

# 获取聚类中心
# 在 GMM 中，means_ 属性代表了每个高斯分布的中心点，等同于 KMeans 的 cluster_centers_
cluster_centers = gmm.means_

# ================= 步骤3：动态激活与分配 (核心逻辑修改) =================

def run_hub_selection_simulation(df_flow_data, df_cands_pool, cluster_centers_list):
    """
    执行接驳点选址与人员分配仿真 (Smart-Weighted 策略)

    Args:
        df_flow_data (pd.DataFrame): 人流数据
        df_cands_pool (pd.DataFrame): 候选接驳点池 (函数内会复制，不修改原数据)
        cluster_centers_list (list): 聚类中心坐标列表

    Returns:
        df_assignments (pd.DataFrame): 每个人员的详细分配表 (用于步骤4输入)
        df_active_hubs (pd.DataFrame): 最终激活的接驳点状态表 (用于可视化和分析)
    """
    print(f"\n>>> 开始执行接驳点选址与分配 (Smart-Weighted 策略)...")

    # 1. 复制候选池，确保状态隔离 (关键！方便后续多次对比不同策略)
    df_working_cands = df_cands_pool.copy()
    # 确保初始占用为0
    df_working_cands['Current_Used'] = 0

    final_assignments = []
    active_hubs_set = set()

    # 2. 遍历每个聚类方向
    # 使用 len(cluster_centers_list) 替代全局变量 K_CLUSTERS，提高通用性
    for k in range(len(cluster_centers_list)):
        # A. 提取该方向人群
        cluster_df = df_flow_data[df_flow_data['Cluster_ID'] == k]
        people_ids = cluster_df['id'].tolist()
        remaining_people = len(people_ids)

        if remaining_people == 0:
            continue

        # B. 计算优先级 (Cost = Dist_Venue + Dist_Cluster)
        c_lat, c_lon = cluster_centers_list[k]

        # 创建临时视图用于排序
        df_temp = df_working_cands.copy()

        # 计算接驳点到该聚类重心的距离
        # (此处复用全局 haversine 函数)
        df_temp['Dist_Cluster'] = df_temp.apply(
            lambda row: haversine(c_lon, c_lat, row['Lon'], row['Lat']), axis=1
        )

        df_temp['Cost'] = df_temp['Dist_Venue'] + df_temp['Dist_Cluster']

        # 实时计算剩余容量 (映射回 working_cands 的最新状态)
        df_temp['Current_Used'] = df_temp['Cand_ID'].map(
            df_working_cands.set_index('Cand_ID')['Current_Used']
        )
        df_temp['Real_Remaining'] = df_temp['Max_Capacity'] - df_temp['Current_Used']

        # 筛选并排序 (Smart策略：按综合成本升序)
        valid_candidates = df_temp[df_temp['Real_Remaining'] > 0].sort_values('Cost')

        # C. 贪心填充
        p_pointer = 0
        candidate_iter = valid_candidates.iterrows()

        while remaining_people > 0:
            try:
                _, best_cand = next(candidate_iter)
            except StopIteration:
                print(f"  [警告] 方向 {k} 人流溢出，所有候选点已满！")
                break

            cand_id = best_cand['Cand_ID']

            # 锁定在工作表中的索引
            cand_idx = df_working_cands[df_working_cands['Cand_ID'] == cand_id].index[0]

            # 获取容量快照
            capacity = df_working_cands.at[cand_idx, 'Max_Capacity']
            current_used = df_working_cands.at[cand_idx, 'Current_Used']
            space_left = capacity - current_used

            if space_left <= 0:
                continue

                # 激活接驳点
            active_hubs_set.add(cand_id)

            # 计算分配数量
            needed_cars = ceil(remaining_people / 1)
            actual_cars = min(space_left, needed_cars)
            actual_people = min(remaining_people, int(actual_cars * 1))

            # 更新状态 (写入 working_cands)
            df_working_cands.at[cand_idx, 'Current_Used'] += actual_cars

            # 记录分配详情
            batch_ids = people_ids[p_pointer: p_pointer + actual_people]
            for pid in batch_ids:
                p_info = df_flow_data[df_flow_data['id'] == pid].iloc[0]
                dist_home = haversine(best_cand['Lon'], best_cand['Lat'], p_info['end_lon'], p_info['end_lat'])

                final_assignments.append({
                    'Person_ID': pid,
                    'Assigned_Hub_ID': cand_id,
                    'Hub_Lat': best_cand['Lat'],
                    'Hub_Lon': best_cand['Lon'],
                    'Home_Lat': p_info['end_lat'],
                    'Home_Lon': p_info['end_lon'],
                    'Dist_Hub_Home_km': dist_home,
                    'Dist_Venue_Hub_km': best_cand['Dist_Venue']
                })

            remaining_people -= actual_people
            p_pointer += actual_people

    # 3. 整理输出结果
    df_assignments = pd.DataFrame(final_assignments)

    # 提取激活接驳点详情
    df_active_hubs = df_working_cands[df_working_cands['Cand_ID'].isin(active_hubs_set)].copy()
    target_cols = ['Cand_ID', 'Lat', 'Lon', 'Max_Capacity', 'Current_Used', 'Dist_Venue']
    df_active_hubs = df_active_hubs[target_cols]

    return df_assignments, df_active_hubs

# 直接调用函数
df_result_assign, df_active_hubs_output = run_hub_selection_simulation(
    df_flow_data=df_flow,
    df_cands_pool=df_candidates,
    cluster_centers_list=cluster_centers
)

# ================= 接驳点验证输出 =================
active_count = len(df_active_hubs_output)
print(df_result_assign)
print(f"\n=== 算法结果统计 ===")
print(f"初始候选接驳点池大小: {TARGET_COUNT}")
print(f"最终激活接驳点数量: {active_count}")
print("\n=== 最终选定的接驳点详情表 ===")
print(df_active_hubs_output.head())


import matplotlib.pyplot as plt
def visualize_allocation(flow_df, cand_df, active_set, center_lat, center_lon):
    """
    可视化聚类结果与接驳点选址分布
    """
    plt.figure(figsize=(12, 10))

    # 1. 绘制人流散点 (按聚类 Cluster_ID 上色)
    # 使用 cmap='viridis' 自动区分不同簇，alpha=0.3 让重叠区域更明显
    scatter = plt.scatter(
        flow_df['end_lon'],
        flow_df['end_lat'],
        c=flow_df['Cluster_ID'],
        cmap='tab10',
        s=10,
        alpha=0.4,
        label='Evacuation Points (People)'
    )

    # 2. 绘制 "未被选中" 的候选点 (灰色，作为背景参考)
    # 找出不在 active_set 中的点
    inactive_df = cand_df[~cand_df['Cand_ID'].isin(active_set)]
    plt.scatter(
        inactive_df['Lon'],
        inactive_df['Lat'],
        c='gray',
        marker='x',
        s=30,
        label='Unused Candidates'
    )

    # 3. 绘制 "被激活" 的接驳点 (红色，醒目显示)
    active_df = cand_df[cand_df['Cand_ID'].isin(active_set)]
    plt.scatter(
        active_df['Lon'],
        active_df['Lat'],
        c='red',
        marker='^',
        s=120,
        edgecolors='black',  # 加个黑边更显眼
        label=f'Active Hubs ({len(active_set)})'
    )

    # 4. 绘制场馆中心 (黑色五角星)
    plt.scatter(
        center_lon,
        center_lat,
        c='black',
        marker='*',
        s=300,
        label='Venue Center'
    )

    # 图表装饰
    plt.title('Dynamic Hub Allocation & Crowd Clustering', fontsize=16)
    plt.xlabel('Longitude')
    plt.ylabel('Latitude')
    plt.axis('equal')  # 保证地图比例尺一致，不会变形
    plt.legend(loc='upper right')
    plt.grid(True, linestyle='--', alpha=0.5)

    # 显示颜色条 (对应 Cluster ID)
    cbar = plt.colorbar(scatter)
    cbar.set_label('Cluster ID')

    print("正在生成可视化图表...")
    plt.savefig('/home/nkk/work/src/python/concert_evacuation/hub_allocation_visualization.png', dpi=300)

# ================= 调用可视化函数 =================
# 直接传入你现有环境中的变量
visualize_allocation(
    df_flow,
    df_candidates,
    df_active_hubs_output['Cand_ID'],  # <--- 修改点：只传 'Cand_ID' 这一列,用于匹配
    CENTER_LAT,
    CENTER_LON
)
