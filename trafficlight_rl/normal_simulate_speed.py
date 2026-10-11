import argparse
import json
import os
import sys
import time
from typing import Optional, Tuple

from engine import get_engine


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
        output_job_prefix: str = "traffic_light_optimization_",
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
        await self.test()
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
    
    road_lst = []
    with open("/home/nkk/work/src/python/rain_setting_adjustment/mapping_dict.json", 'r', encoding='utf-8') as fcc_file:
        mapping_dict = json.load(fcc_file)
    for element in mapping_dict:
        print(element)
        for road in element['roads']:
            road_lst.append(str(road))
    
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp", type=str, help="实验名称")
    parser.add_argument("--mongo_uri", type=str, required=True, help="MongoDB连接URI")
    parser.add_argument("--map_db", type=str, required=True, help="地图数据库名")
    parser.add_argument("--map_coll", type=str, required=True, help="地图集合名")
    parser.add_argument("--agent_db", type=str, required=True, help="车辆数据库名")
    parser.add_argument("--agent_coll", type=str, required=True, help="车辆集合名")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--output_sql_dsn", type=str, default="")
    parser.add_argument(
        "--output_job_prefix", type=str, default="traffic_light_optimization_"
    )
    parser.add_argument(
        "--output_bbox",
        type=str,
        default=None,
        help="输出范围，格式为: min_longitude,min_latitude,max_longitude,max_latitude",
    )
    args = parser.parse_args()

    if args.exp is None:
        path = time.strftime("log/maxpressure/%Y%m%d-%H%M%S")
    else:
        path = time.strftime(f"log/maxpressure/{args.exp}/%Y%m%d-%H%M%S")
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    with open(f"{path}/cmd.sh", "w") as f:
        f.write(" ".join(sys.argv))
    with open(f"{path}/args.json", "w") as f:
        json.dump(vars(args), f)

    if args.output_bbox is not None:
        args.output_bbox = tuple(map(float, args.output_bbox.split(",")))
        if len(args.output_bbox) != 4:
            raise ValueError("output_bbox must be a tuple of 4 floats")

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=args.steps // args.interval,
        log_dir=path,
        reward="pressure",
        output_sql_dsn=args.output_sql_dsn,
        output_job_prefix=args.output_job_prefix,
        output_bbox=args.output_bbox,
    )

    print(f"开始运行模拟器，总步数: {args.steps//args.interval}")
    print(f"日志目录: {path}")

    total_dict = {}
    # 运行模拟器
    for step in range(args.steps // args.interval):
        
        done, info = await env.step()
        if step % 1 == 0:
            speed_dict = {}
            roads = await env.eng.get_roads()
            for road in road_lst:
                avg_v = await env.eng.get_road_avg_v(road)
                speed_dict[road] = avg_v
            total_dict[step] = speed_dict
    with open(f"/home/nkk/work/src/python/rain_setting_adjustment/output/normal_speed_dict.json", "w") as f:
        json.dump(total_dict, f)
        
if __name__ == "__main__":
    import asyncio

    asyncio.run(main())