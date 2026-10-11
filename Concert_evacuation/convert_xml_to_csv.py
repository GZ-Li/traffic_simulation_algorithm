import xml.etree.ElementTree as ET
import json
import math
import pandas as pd

# ================= 配置 =================
XML_FILE = '/home/nkk/work/src/python/Waterlogging/Shaped.net.xml'
JSON_FILE = '/home/nkk/work/src/python/Waterlogging/current_od_file.json'
OUTPUT_CSV = '/home/nkk/work/src/python/concert_evacuation/human_flow_simulation_wuhan_realod.csv'  # 输出文件名

# ================= 工具函数 =================
def parse_shape_str(shape_str):
    """将 'x1,y1 x2,y2 ...' 字符串转换为 [(x1,y1), (x2,y2), ...]"""
    return [tuple(map(float, pair.split(','))) for pair in shape_str.split(' ')]


def get_coord_on_polyline(polyline, distance):
    """在折线上根据距离起点的长度计算坐标"""
    if not polyline: return None
    current_dist = 0.0

    for i in range(len(polyline) - 1):
        p1, p2 = polyline[i], polyline[i + 1]
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        seg_len = math.sqrt(dx ** 2 + dy ** 2)

        if current_dist + seg_len >= distance:
            # 目标点在当前线段上
            remain = distance - current_dist
            ratio = remain / seg_len if seg_len > 0 else 0
            return (p1[0] + dx * ratio, p1[1] + dy * ratio)
        current_dist += seg_len

    return polyline[-1]  # 超过长度则返回终点


# ================= 主逻辑 =================
def extract_od_coordinates_to_csv():
    print(f">>> 正在解析路网文件: {XML_FILE} ...")

    # 1. 获取坐标转换参数 (Boundaries)
    bounds = {}
    context = ET.iterparse(XML_FILE, events=('start', 'end'))
    context_iter = iter(context)

    for event, elem in context_iter:
        if event == 'end' and elem.tag == 'location':
            orig = list(map(float, elem.get('origBoundary').split(',')))
            conv = list(map(float, elem.get('convBoundary').split(',')))
            # 存储边界
            bounds['geo'] = {'min_x': orig[0], 'min_y': orig[1], 'max_x': orig[2], 'max_y': orig[3]}
            bounds['net'] = {'min_x': conv[0], 'min_y': conv[1], 'max_x': conv[2], 'max_y': conv[3]}
            elem.clear()
            break

    # 坐标转换函数
    def net_xy_to_geo(x, y):
        x_r = (x - bounds['net']['min_x']) / (bounds['net']['max_x'] - bounds['net']['min_x'])
        y_r = (y - bounds['net']['min_y']) / (bounds['net']['max_y'] - bounds['net']['min_y'])
        lon = bounds['geo']['min_x'] + x_r * (bounds['geo']['max_x'] - bounds['geo']['min_x'])
        lat = bounds['geo']['min_y'] + y_r * (bounds['geo']['max_y'] - bounds['geo']['min_y'])
        return lon, lat

    # 2. 构建 EdgeID -> Shape 映射
    print(">>> 正在构建 Edge 几何索引...")
    edge_shapes = {}
    context = ET.iterparse(XML_FILE, events=('end',))
    for event, elem in context:
        if elem.tag == 'edge':
            eid = elem.get('id')
            first_lane = elem.find('lane')
            if first_lane is not None and eid:
                edge_shapes[eid] = parse_shape_str(first_lane.get('shape'))
            elem.clear()

    # 3. 解析 OD JSON 并提取数据
    print(f">>> 正在提取 OD 坐标并生成 DataFrame: {JSON_FILE} ...")
    results = []

    with open(JSON_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for person in data:
        try:
            # 路径提取
            trip = person['data']['schedules'][0]['trips'][0]
            road_ids = trip['routes'][0]['driving']['road_ids']
            last_edge_id = str(road_ids[-1])
            s_offset = trip['end']['lane_position']['s']

            if last_edge_id in edge_shapes:
                shape = edge_shapes[last_edge_id]
                net_x, net_y = get_coord_on_polyline(shape, s_offset)
                lon, lat = net_xy_to_geo(net_x, net_y)

                # 【关键修改】列名适配下游聚类代码
                results.append({
                    'id': person['_id'],      # 重命名 person_id -> id
                    'end_lon': round(lon, 6), # 重命名 lon -> end_lon
                    'end_lat': round(lat, 6)  # 重命名 lat -> end_lat
                })
        except (KeyError, IndexError, TypeError):
            continue

    # 4. 保存为 CSV
    if results:
        df = pd.DataFrame(results)
        # 按照 id, end_lat, end_lon 的顺序整理，虽然 read_csv 不在乎顺序，但为了规范
        df = df[['id', 'end_lat', 'end_lon']]
        df.to_csv(OUTPUT_CSV, index=False)
        print(f"\n成功生成 CSV 文件: '{OUTPUT_CSV}' (包含 {len(df)} 条记录)")
        print("前 5 行数据预览:")
        print(df.head())
        return df
    else:
        print("未提取到有效数据，未生成 CSV。")
        return pd.DataFrame()

if __name__ == "__main__":
    df_flow = extract_od_coordinates_to_csv()
