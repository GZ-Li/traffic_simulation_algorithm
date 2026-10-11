import argparse
import json
import os
import sys
import time
import asyncio
from typing import Optional
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from trafficlight_rl.engine import get_engine
import pandas as pd

# -----------------------------
# Env 封装
# -----------------------------
class Env:
    def __init__(
        self,
        mongo_uri: str,
        map_db: str,
        map_coll: str,
        agent_db: str,
        agent_coll: str,
        start_step: int,
        step_size: int,
        step_count: int,
        log_dir: str,
        reward: str = "pressure",
    ):
        self.log_dir = log_dir
        self.step_size = step_size
        self.step_count = step_count
        self._step = 0
        self.reward = reward
        self.info = {"ATT": 1e999, "Throughput": 0}

        self.eng = get_engine(
            mongo_uri=mongo_uri,
            map_db=map_db,
            map_coll=map_coll,
            agent_db=agent_db,
            agent_coll=agent_coll,
            start_step=start_step,
            total_step=(step_count + 1) * step_size,
            use_max_pressure=True,
        )
        self.eng._start_simulator()

    async def step(self):
        self.eng.next_step(self.step_size)
        self._step += 1
        done = False
        if self._step >= self.step_count:
            self.info["ATT"] = await self.eng.get_departed_vehicle_average_traveling_time()
            self.info["Throughput"] = await self.eng.get_finished_vehicle_count()
            done = True
            self._step = 0
            with open(f"{self.log_dir}/info.log", "a") as f:
                f.write(f"{self.info['ATT']:.3f} {self.info['Throughput']} {time.time():.3f}\n")
        return done, self.info

    def stop(self):
        self.eng._stop_simulator()


# -----------------------------
# 主逻辑
# -----------------------------
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--exp", type=str, default="snow_test")
    args = parser.parse_args()

    # 日志目录
    log_dir = time.strftime(f"log/snow/{args.exp}/%Y%m%d-%H%M%S")
    os.makedirs(log_dir, exist_ok=True)

    # 读取扫雪路径文件
    with open("Snowplowing/plowing_path/cleaned_roads_by_interval.json", "r", encoding="utf-8") as f:
        snow_paths = json.load(f)

    # 参数设定
    MAX_INTERVALS = 10
    INTERVAL_STEPS = 50
    NORMAL_SPEED = 20
    SNOWED_SPEED = 2
    STARTSTEP = 20  # 前置步长

    intervals = sorted(snow_paths.keys(), key=lambda x: int(x.split("_")[1]))[:MAX_INTERVALS]

    # 初始化 Env
    total_sim_step = MAX_INTERVALS * INTERVAL_STEPS
    total_step = total_sim_step + STARTSTEP
    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=0,
        step_size=args.interval,
        step_count=total_step,
        log_dir=log_dir,
    )

    # --- 初始化车辆为未扫雪速度，一上来所有车辆速度就正确 ---
    roads = await env.eng.get_roads()
    all_vehicles = []
    for road in roads:
        all_vehicles.extend(await env.eng.get_vehicle_ids_of_road(road))
    if all_vehicles:
        await env.eng.set_max_vehicle_speed(all_vehicles, SNOWED_SPEED)

    # --- 前置步长 ---
    for _ in range(STARTSTEP):
        await env.step()

    results = []

    for idx, interval_name in enumerate(intervals, start=1):
        print(f"\n===== Interval {interval_name} =====")

        interval_data = snow_paths[interval_name]

        # 已扫雪道路集合
        cleaned_roads = set()
        for _, road_list in interval_data.items():
            cleaned_roads.update(road_list)

        # 分配车辆
        cleaned_vehicles = []
        snowed_vehicles = []
        for road in roads:
            vehs = await env.eng.get_vehicle_ids_of_road(road)
            if road in cleaned_roads:
                cleaned_vehicles.extend(vehs)
            else:
                snowed_vehicles.extend(vehs)

        if cleaned_vehicles:
            await env.eng.set_max_vehicle_speed(cleaned_vehicles, NORMAL_SPEED)
        if snowed_vehicles:
            await env.eng.set_max_vehicle_speed(snowed_vehicles, SNOWED_SPEED)

        # 跑 INTERVAL_STEPS 步
        for _ in range(INTERVAL_STEPS):
            await env.step()

        # 统计瞬时全局平均速度（只取当前 step，不累计）
        total_speed = 0
        count = 0
        for road in roads:
            v = await env.eng.get_road_avg_v(road)
            if v is not None:
                total_speed += v
                count += 1
        avg_speed = total_speed / count if count > 0 else 0
        print(f"Interval {interval_name} global avg speed: {avg_speed:.3f}")

        results.append({
            "interval_index": idx,
            "interval_name": interval_name,
            "global_avg_speed": avg_speed
        })

    # 保存 CSV
    df = pd.DataFrame(results)
    out_file = os.path.join(log_dir, "global_avg_speed_by_interval.csv")
    df.to_csv(out_file, index=False, encoding="utf-8")
    print(f"\nSimulation finished. Result saved to {out_file}")
    env.eng._stop_simulator()

if __name__ == "__main__":
    asyncio.run(main())
