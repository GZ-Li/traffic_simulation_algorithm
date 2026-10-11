import argparse
import json
import os
import sys
import time
from typing import Optional, Tuple
from collections import defaultdict, OrderedDict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from trafficlight_rl.engine import get_engine
import pandas as pd


class Env:
    def __init__(
        self,
        mongo_uri: str,
        map_db: str,
        map_coll: str,
        agent_db: str,
        agent_coll: str,
        start_step,
        step_size,
        step_count,
        log_dir,
        reward,
        output_sql_dsn: str = "",
        output_job_prefix: str = "single_accident_",
        output_bbox: Optional[Tuple[float, float, float, float]] = None,
    ):
        self.log_dir = log_dir
        self.eng = get_engine(
            mongo_uri=mongo_uri,
            map_db=map_db,
            map_coll=map_coll,
            agent_db=agent_db,
            agent_coll=agent_coll,
            start_step=start_step,
            total_step=(step_count + 1) * step_size,
            use_max_pressure=True,
            output_sql_dsn=output_sql_dsn,
            output_job_prefix=output_job_prefix,
            output_bbox=output_bbox,
        )

        self.step_size = step_size
        self.step_count = step_count
        self._step = 0
        self.reward = reward
        self.info = {
            "ATT": 1e999,
            "Throughput": 0,
            "reward": 0,
            "ATT_inside": 1e999,
            "ATT_finished": 1e999,
            "Throughput_inside": 0,
        }

    async def test(self):
        lanes = await self.eng.get_lanes()

        # for lane in lanes:
        #     print(f"lane id:{lane}")

        roads = await self.eng.get_roads()

        for road in roads:
            avg_v = await self.eng.get_road_avg_v(road)
            lanes_id = await self.eng.get_lane_id_of_road(road)
            vehicles = await self.eng.get_vehicle_ids_of_road(road)
            for vehicle in vehicles:
                pos = await self.eng.get_vehicle_pos(vehicle)
                v = await self.eng.get_vehicle_speed(vehicle)
                road_id = await self.eng.get_road_id_with_vehicle(vehicle)
                lane_id = await self.eng.get_lane_id_with_vehicle(vehicle)
                if lane_id != None:
                    await self.eng.set_max_vehicle_speed(vehicle, 50)
        await self.eng.set_vehicle_acc(vehicles, 10)

    def reset(self):
        # 重启环境
        self.eng._stop_simulator()
        self.eng._start_simulator()

    async def step(self):
        # await self.test()
        self.eng.next_step(self.step_size)

        self._step += 1
        done = False
        if self._step >= self.step_count:
            self.info["ATT"] = (
                await self.eng.get_departed_vehicle_average_traveling_time()
            )
            self.info["ATT_finished"] = (
                await self.eng.get_finished_vehicle_average_traveling_time()
            )
            self.info["Throughput"] = await self.eng.get_finished_vehicle_count()
            self._step = 0
            done = True
            with open(f"{self.log_dir}/info.log", "a") as f:
                f.write(
                    f"{self.info['ATT']:.3f} {self.info['Throughput']} {time.time():.3f}\n"
                )
        return done, self.info
    
    
async def main():
    # 读取最短路径
    with open('shortest_paths.json', 'r', encoding='utf-8') as f:
        paths_data = json.load(f)
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo_uri", type=str, required=True, help="MongoDB连接URI")
    parser.add_argument("--map_db", type=str, required=True, help="地图数据库名")
    parser.add_argument("--map_coll", type=str, required=True, help="地图集合名")
    parser.add_argument("--agent_db", type=str, required=True, help="车辆数据库名")
    parser.add_argument("--agent_coll", type=str, required=True, help="车辆集合名")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--output_sql_dsn", type=str, default="")
    parser.add_argument("--output_job_prefix", type=str, default="traffic_light_optimization_")
    args = parser.parse_args()

    # 创建环境
    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=70,
        log_dir="logs",
        reward="pressure",
        output_sql_dsn=args.output_sql_dsn,
        output_job_prefix=args.output_job_prefix,
    )

    # 运行模拟到第 60 步
    for step in range(60):
        await env.step()
        print(f"Simulation step: {step + 1}/60", end='\r')

    print("\nReading road speeds at step 60...")
    
    # 在第 60 步读取每条路上的平均速度
    road_speeds = {}
    result = {}
    # print(paths_data)
    
    # 收集所有道路的速度
    roads = await env.eng.get_roads()
    for road_id in roads:
        avg_v = await env.eng.get_road_avg_v(road_id)
        road_speeds[road_id] = avg_v if avg_v is not None else 35
    
    # 为每个医院的路径读取速度数据
    for hospital_id, paths_list in paths_data.items():
        # if isinstance(paths_list, dict) and 'error' in paths_list:
        #     result[hospital_id] = {'error': paths_list['error']}
        #     continue
        hospital_result = {}
        for path_info in paths_list:
            path = path_info['path']
            path_id = f"{path_info['rank']}"
            
            # 为这条路径的每个 road_id 读取速度
            path_speeds = {}
            for road_id in path:
                if road_id in road_speeds:
                    path_speeds[road_id] = road_speeds[road_id]
                else:
                    # 如果道路不在 road_speeds 中，尝试从 engine 获取
                    try:
                        temp_vehicles = await env.eng.get_vehicle_ids_of_road(road_id)
                        if len(temp_vehicles) > 0:
                            avg_v = await env.eng.get_road_avg_v(road_id)
                            path_speeds[road_id] = avg_v if avg_v is not None else 35
                        else:
                            path_speeds[road_id] = 35
                    except:
                        path_speeds[road_id] = 35
            
            hospital_result[path_id] = path_speeds
        
        result[hospital_id] = hospital_result
    
    # 保存结果
    with open('path_speeds.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print("Done! Results saved to path_speeds.json")
            
    for step in range(10):
        await env.step()
    env.eng._stop_simulator()
            
if __name__ == "__main__":
    
    import asyncio
    asyncio.run(main())
    
    