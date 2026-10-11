import argparse
import os
import sys
import time
from typing import Optional, Tuple
from collections import defaultdict, deque
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
        }

    async def step(self):
        self.eng.next_step(self.step_size)
        self._step += 1
        done = False
        if self._step >= self.step_count:
            self.info["ATT"] = await self.eng.get_departed_vehicle_average_traveling_time()
            self.info["Throughput"] = await self.eng.get_finished_vehicle_count()
            self._step = 0
            done = True
            with open(f"{self.log_dir}/info.log", "a") as f:
                f.write(f"{self.info['ATT']:.3f} {self.info['Throughput']} {time.time():.3f}\n")
        return done, self.info


async def main():
    
    max_clean_at_once = 2 #排涝车数量
    steps_to_clean_one = 90 #涝点清理成本
    flooded_speed = 2 #车辆驶过涝点的速度
    normal_speed = 30 #车辆正常行驶速度
    start_step = 90

    flood_points = {
        '300000545': ['200000162', '200000184', '200000201', '200000157', '200000143', '200000202', '200000135', '200000180', '200000161', '200000131', '200000132', '200000134', '200012001', '200000133', '200000218', '200000213', '200000142', '200000122', '200000124', '200000166', '200000121', '200007002', '200000136', '200002565', '200000186', '200000185', '200000225', '200013435', '200002466'], 
        '300001682': ['200011001', '200002015', '200001676', '200001467', '200001571', '200013003', '200000021', '200001988', '200013004', '200001989', '200001709', '200002014', '200002013', '200002018', '200001990', '200002137', '200002016', '200001987', '200001572', '200001455', '200002186', '200007062', '200001476', '200007063', '200001781', '200001708', '200001568', '200002019', '200001462', '200001992', '200001693', '200001994', '200001465', '200002010', '200001696', '200001457', '200001570', '200001993', '200001454', '200001575', '200001694', '200002011'], 
        '300001249': ['200001512', '200001503', '200001500', '200001513', '200001498', '200001490','200001516', '200001497', '200001473', '200001491'],
        '300001252': ['200001250', '200001253', '200001248', '200001243']
    }

    parser = argparse.ArgumentParser()
    parser.add_argument("--exp", type=str, help="实验名称")
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--order", type=str, default='best')
    args = parser.parse_args()
    
    # 改顺序做对比实验
    if args.order == 'best':
        sorted_points = ["300000545", "300001682", "300001249", "300001252"]
    else:
        sorted_points = ["300001252", "300001249", "300001682", "300000545"]

    # 根据排涝车数量给涝点分批次
    batches = [sorted_points[i:i+max_clean_at_once] for i in range(0, len(sorted_points), max_clean_at_once)]

    path = time.strftime(f"log/maxpressure/{args.exp}/%Y%m%d-%H%M%S") if args.exp else time.strftime("log/maxpressure/%Y%m%d-%H%M%S")
    os.makedirs(path, exist_ok=True)

    total_sim_step = (len(batches) + 1) * steps_to_clean_one
    total_step = start_step + total_sim_step

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=total_step,
        log_dir=path,
        reward="pressure",
    )

    # 初始化车速
    roads = await env.eng.get_roads()
    all_vehicles = []
    for road_id in roads:
        all_vehicles.extend(await env.eng.get_vehicle_ids_of_road(road_id))
    if all_vehicles:
        await env.eng.set_max_vehicle_speed(all_vehicles, normal_speed)

    for _ in range(start_step):
        await env.step()

    # -------------------------------
    # throughput 统计结构
    # -------------------------------
    short_window = 5       # 真正算 throughput 的时间尺度
    output_interval = 30   # 每 30 步输出一次

    # 每个涝点一个滑动窗口
    fp_windows = {fp: deque(maxlen=short_window) for fp in flood_points}
    all_window = deque(maxlen=short_window)

    # 历史记录
    fp_ts = defaultdict(list)
    all_ts = []

    # 用于追踪车辆消失
    road_vehicles_last = {road: set() for fp in flood_points for road in flood_points[fp]}

    for step in range(total_sim_step):
        await env.step()
        current_batch = step // steps_to_clean_one

        flooded_vehicles = []
        normal_vehicles = []
        for i, batch in enumerate(batches):
            for group in batch:
                group_vehicles = []
                for road_id in flood_points[group]:
                    group_vehicles.extend(await env.eng.get_vehicle_ids_of_road(road_id))
                if current_batch <= i:
                    flooded_vehicles.extend(group_vehicles)
                else:
                    normal_vehicles.extend(group_vehicles)

        if flooded_vehicles:
            await env.eng.set_max_vehicle_speed(flooded_vehicles, flooded_speed)
        if normal_vehicles:
            await env.eng.set_max_vehicle_speed(normal_vehicles, normal_speed)

        # 本 step 瞬时 throughput
        fp_instant = {}
        all_instant = 0

        for fp, roads in flood_points.items():
            tp = 0
            for road_id in roads:
                current = set(await env.eng.get_vehicle_ids_of_road(road_id))
                disappeared = road_vehicles_last[road_id] - current
                tp += len(disappeared)
                road_vehicles_last[road_id] = current
            fp_instant[fp] = tp
            all_instant += tp

        # 加入 5 步滑动窗
        for fp in flood_points:
            fp_windows[fp].append(fp_instant[fp])
        all_window.append(all_instant)

        # 每 30 步输出一次最近 5 步 throughput
        if (step + 1) % output_interval == 0:
            for fp in flood_points:
                fp_ts[fp].append({
                    "step": step,
                    "throughput_5step": sum(fp_windows[fp])
                })
            all_ts.append({
                "step": step,
                "throughput_5step": sum(all_window)
            })

    env.eng._stop_simulator()

    # 保存 CSV
    rows = []
    for fp, records in fp_ts.items():
        for r in records:
            rows.append({
                "step": r["step"],
                "flood_point": fp,
                "throughput_5step": r["throughput_5step"]
            })
    for r in all_ts:
        rows.append({
            "step": r["step"],
            "flood_point": "all",
            "throughput_5step": r["throughput_5step"]
        })

    df = pd.DataFrame(rows)
    df.to_csv("flood_point_throughput_5step_every30_random.csv", index=False)
    print("Saved to flood_point_throughput_5step_every30.csv")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())