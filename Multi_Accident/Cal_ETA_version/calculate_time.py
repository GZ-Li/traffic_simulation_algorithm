import json

PATH_SPEED_FILE = "path_speeds.json"
MAP_FILE = "current_map_file.json"
DEFAULT_SPEED = 35.0  # m/s

# ===== 读取速度文件 =====
with open(PATH_SPEED_FILE, "r", encoding="utf-8") as f:
    path_speeds = json.load(f)

# ===== 读取地图文件 =====
with open(MAP_FILE, "r", encoding="utf-8") as f:
    road_info = json.load(f)

roads_data = []
lanes_data = []
for item in road_info:
    if item["class"] == "lane":
        lanes_data.append(item)
    elif item["class"] == "road":
        roads_data.append(item)

# ===== 建立 road -> lanes 映射 =====
road_to_lanes = {}
for r in roads_data:
    road_id = int(r["data"]["id"])
    lane_ids = r["data"].get("lane_ids", [])
    road_to_lanes[road_id] = [int(x) for x in lane_ids]

# ===== 建立 lane -> length 映射 =====
lane_to_length = {}
for l in lanes_data:
    lane_id = int(l["data"]["id"])
    length = float(l["data"].get("length", 0.0))
    lane_to_length[lane_id] = length

# ===== 开始计算 =====
result = {}

for hospital_id, accidents in path_speeds.items():
    hospital_result = {}
    for accident_id, paths in accidents.items():
        accident_result = {}
        for path_id, road_speeds in paths.items():
            total_length = 0.0
            total_time = 0.0

            for road_id_str, speed in road_speeds.items():
                road_id = int(road_id_str)
                v = speed if speed and speed > 0 else DEFAULT_SPEED

                # 这条 road 的长度 = 所有 lane 长度之和
                road_length = 0.0
                for lane_id in road_to_lanes.get(road_id, []):
                    road_length += lane_to_length.get(lane_id, 0.0)

                total_length += road_length
                if v > 0:
                    total_time += road_length / v

            accident_result[path_id] = {
                "total_length": total_length,
                "total_time": total_time
            }

            # 同时直接打印
            print(
                f"Hospital {hospital_id}, Accident {accident_id}, {path_id}: "
                f"length = {total_length:.2f} m, "
                f"ETA = {total_time:.2f} s"
            )

        hospital_result[accident_id] = accident_result
    result[hospital_id] = hospital_result

# ===== 保存结果 =====
with open("path_time_length.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print("\nAll done. Results saved to path_time_length.json")