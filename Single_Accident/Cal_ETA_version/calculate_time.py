import json

# ===== 改成你自己的文件路径 =====
PATH_SPEED_FILE = "path_speeds.json"

Max_SPEED = 20.0  # m/s

# ===== 读取数据 =====
with open(PATH_SPEED_FILE, "r") as f:
    path_speeds = json.load(f)

roads_data = []
lanes_data = []
with open("current_map_file.json", "r") as f:
    road_info = json.load(f)
    
for temp_road in road_info:
    if temp_road['class'] == 'lane':
        lanes_data.append(temp_road)
    if temp_road['class'] == 'road':
        roads_data.append(temp_road)

# ===== 建立 road -> lanes 映射 =====
road_to_lanes = {}
for r in roads_data:
    road_id = int(r["data"]["id"])
    lane_ids = r["data"].get("lane_ids", [])
    road_to_lanes[road_id] = lane_ids

# ===== 建立 lane -> length 映射 =====
lane_to_length = {}
for l in lanes_data:
    lane_id = int(l["data"]["id"])
    length = float(l["data"].get("length", 0.0))
    lane_to_length[lane_id] = length

# ===== 计算并直接输出 =====
for hospital_id, paths in path_speeds.items():
    print(f"\nHospital {hospital_id}:")
    for path_id, road_speeds in paths.items():
        total_length = 0.0
        total_time = 0.0

        for road_id_str, speed in road_speeds.items():
            road_id = int(road_id_str)
            v = speed if speed and speed > 0 else Max_SPEED

            lane_ids = road_to_lanes.get(road_id, [])
            road_length = 0.0
            for lid in lane_ids:
                road_length += lane_to_length.get(lid, 0.0)

            total_length += road_length
            if v > 0:
                total_time += road_length / v

        print(
            f"  {path_id}: "
            f"length = {total_length:.2f} m, "
            f"ETA = {total_time:.2f} s"
        )
