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
                    # await self.eng.set_vehicle_acc(vehicle, 10)
                    await self.eng.set_max_vehicle_speed(vehicle, 50)
            #     print(f"vehicle id:{vehicle}, pos:{pos},v:{v}")
            # print(f"road id:{road}, avg_v:{avg_v}")
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
        
    # 假设第60秒发生车祸
    Acc_step = 60

    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--output_sql_dsn", type=str, default="")
    parser.add_argument("--output_job_prefix", type=str, default="traffic_light_optimization_")
    args = parser.parse_args()

    # 创建环境（完全照你原来）
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

    # 跑到 60 步
    for step in range(Acc_step):
        await env.step()
        print(f"Simulation step: {step + 1}/60", end='\r')

    print("\nReading road speeds at step 60...")

    # 所有道路平均速度
    road_speeds = {}
    roads = await env.eng.get_roads()
    for road_id in roads:
        avg_v = await env.eng.get_road_avg_v(road_id)
        road_speeds[str(road_id)] = avg_v if avg_v is not None else 35

    # 统一输出结构
    result = {}

    # 判断是不是“多事故格式”
    sample_value = next(iter(paths_data.values()))
    multi_accident = isinstance(sample_value, dict)

    if not multi_accident:
        # 原始单事故格式： hospital -> [paths]
        for hospital_id, paths_list in paths_data.items():
            hospital_result = {}
            accident_id = "accident_1"
            accident_result = {}

            for path_info in paths_list:
                path = path_info["path"]
                path_id = f"path_{path_info['rank']}"
                path_speeds = {}

                for road_id in path:
                    rid = str(road_id)
                    path_speeds[rid] = road_speeds.get(rid, 35)

                accident_result[path_id] = path_speeds

            hospital_result[accident_id] = accident_result
            result[hospital_id] = hospital_result

    else:
        # 新格式： hospital -> accident -> [paths]
        for hospital_id, accident_paths in paths_data.items():
            hospital_result = {}

            for accident_id, paths_list in accident_paths.items():
                accident_result = {}

                for path_info in paths_list:
                    path = path_info["path"]
                    path_id = f"path_{path_info['rank']}"
                    path_speeds = {}

                    for road_id in path:
                        rid = str(road_id)
                        path_speeds[rid] = road_speeds.get(rid, 35)

                    accident_result[path_id] = path_speeds

                hospital_result[accident_id] = accident_result

            result[hospital_id] = hospital_result

    # 保存
    with open("path_speeds.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("Done! Results saved to path_speeds.json")

    # 多跑几步再关
    for _ in range(10):
        await env.step()

    env.eng._stop_simulator()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())