import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Ensure ../..../python is on sys.path so trafficlight_rl can be imported.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = _THIS_DIR
for _ in range(5):
    _candidate = os.path.join(_SEARCH_DIR, "trafficlight_rl")
    if os.path.isdir(_candidate):
        sys.path.insert(0, _SEARCH_DIR)
        break
    _parent = os.path.dirname(_SEARCH_DIR)
    if _parent == _SEARCH_DIR:
        break
    _SEARCH_DIR = _parent

from trafficlight_rl.engine import get_engine


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

    async def step(self) -> Tuple[bool, Optional[Dict[str, float]]]:
        self.eng.next_step(self.step_size)
        self._step += 1
        done = self._step >= self.step_count
        info = None
        if done:
            info = {
                "ATT": await self.eng.get_departed_vehicle_average_traveling_time(),
                "Throughput": await self.eng.get_finished_vehicle_count(),
            }
        return done, info


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate two extreme snow states: all roads snowed vs all roads cleaned."
    )
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--warmup_steps", type=int, default=5)
    parser.add_argument("--eval_steps", type=int, default=18)
    parser.add_argument(
        "--speed_sample_every",
        type=int,
        default=5,
        help="Sample speed every N simulation steps (>=1).",
    )
    parser.add_argument("--snowed_speed", type=float, default=3.0)
    parser.add_argument("--normal_speed", type=float, default=20.0)
    parser.add_argument("--exp", type=str, default="snow_extreme_baseline")
    return parser.parse_args()


async def set_lane_speed_limit(lane_ids: Sequence[str], max_v: float, env: Env) -> None:
    for lane_id in lane_ids:
        await env.eng.client.lane_service.SetLaneMaxV({"lane_id": lane_id, "max_v": max_v})


async def collect_speed_snapshot(
    env: Env,
    roads: Sequence[str],
    lane_weights: Dict[str, int],
    fallback_speed: float,
) -> Tuple[float, float]:
    total_active_speed = 0.0
    active_count = 0
    weighted_filled_sum = 0.0
    weighted_sum = 0.0

    for road_id in roads:
        road_key = str(road_id)
        avg_v = await env.eng.get_road_avg_v(road_id)
        lane_w = max(1, int(lane_weights.get(road_key, 1)))

        if avg_v is not None:
            speed_value = float(avg_v)
            total_active_speed += speed_value
            active_count += 1
            filled_speed = speed_value
        else:
            filled_speed = float(fallback_speed)

        weighted_filled_sum += filled_speed * lane_w
        weighted_sum += lane_w

    active_avg_speed = total_active_speed / active_count if active_count > 0 else 0.0
    filled_weighted_avg_speed = weighted_filled_sum / weighted_sum if weighted_sum > 0 else 0.0
    return active_avg_speed, filled_weighted_avg_speed


async def bootstrap_network_cache(args: argparse.Namespace, log_dir: str) -> Tuple[List[str], Set[str], Dict[str, int]]:
    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=1,
        log_dir=log_dir,
    )
    try:
        roads = [str(road_id) for road_id in await env.eng.get_roads()]
        all_lanes: Set[str] = set()
        lane_weights: Dict[str, int] = {}

        for road_id in roads:
            lane_ids = await env.eng.get_lane_id_of_road(road_id)
            lane_ids_str = [str(lane_id) for lane_id in lane_ids]
            all_lanes.update(lane_ids_str)
            lane_weights[str(road_id)] = max(1, len(lane_ids_str))

        return roads, all_lanes, lane_weights
    finally:
        env.eng._stop_simulator()


async def run_extreme_scenario(
    args: argparse.Namespace,
    scenario_name: str,
    cleaned_lanes: Set[str],
    fallback_speed: float,
    roads: Sequence[str],
    all_lanes: Set[str],
    lane_weights: Dict[str, int],
    root_log_dir: str,
) -> Dict[str, object]:
    run_log_dir = os.path.join(root_log_dir, scenario_name)
    os.makedirs(run_log_dir, exist_ok=True)

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=args.warmup_steps + args.eval_steps,
        log_dir=run_log_dir,
    )

    try:
        for _ in range(args.warmup_steps):
            await env.step()

        # Set a global snow baseline first, then raise cleaned lanes.
        await set_lane_speed_limit(sorted(all_lanes), args.snowed_speed, env)
        if cleaned_lanes:
            await set_lane_speed_limit(sorted(cleaned_lanes), args.normal_speed, env)

        sampled_active_speed_values: List[float] = []
        sampled_filled_speed_values: List[float] = []

        for step_in_interval in range(1, args.eval_steps + 1):
            await env.step()
            if step_in_interval % args.speed_sample_every == 0:
                active_avg, filled_avg = await collect_speed_snapshot(
                    env=env,
                    roads=roads,
                    lane_weights=lane_weights,
                    fallback_speed=fallback_speed,
                )
                sampled_active_speed_values.append(active_avg)
                sampled_filled_speed_values.append(filled_avg)

        if not sampled_active_speed_values:
            active_avg, filled_avg = await collect_speed_snapshot(
                env=env,
                roads=roads,
                lane_weights=lane_weights,
                fallback_speed=fallback_speed,
            )
            sampled_active_speed_values.append(active_avg)
            sampled_filled_speed_values.append(filled_avg)

        active_interval_avg_speed = statistics.fmean(sampled_active_speed_values)
        filled_weighted_interval_avg_speed = statistics.fmean(sampled_filled_speed_values)
        att = await env.eng.get_departed_vehicle_average_traveling_time()
        throughput = await env.eng.get_finished_vehicle_count()

        row = {
            "scenario": scenario_name,
            "baseline_snowed_speed": args.snowed_speed,
            "raised_cleaned_speed": args.normal_speed,
            "cleaned_lane_count": len(cleaned_lanes),
            "global_avg_speed": active_interval_avg_speed,
            "filled_weighted_avg_speed": filled_weighted_interval_avg_speed,
            "speed_sample_count": len(sampled_active_speed_values),
            "ATT": att,
            "Throughput": throughput,
            "road_count": len(roads),
            "lane_count": len(all_lanes),
        }
        print(
            f"[{scenario_name}] global_avg_speed={active_interval_avg_speed:.3f} "
            f"filled_weighted_avg_speed={filled_weighted_interval_avg_speed:.3f} "
            f"ATT={att:.3f} Throughput={throughput}"
        )
        return row
    finally:
        env.eng._stop_simulator()


def write_rows_csv_json(rows: Sequence[Dict[str, object]], csv_path: str, json_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as file_obj:
        json.dump(list(rows), file_obj, ensure_ascii=False, indent=2)


async def main() -> None:
    args = parse_args()
    if args.speed_sample_every <= 0:
        raise ValueError("--speed_sample_every must be >= 1")

    # log_dir = time.strftime(f"log/snow/{args.exp}/%Y%m%d-%H%M%S")
    log_dir = f"ext_res"
    os.makedirs(log_dir, exist_ok=True)

    roads, all_lanes, lane_weights = await bootstrap_network_cache(
        args=args,
        log_dir=os.path.join(log_dir, "bootstrap"),
    )

    all_snowed_row = await run_extreme_scenario(
        args=args,
        scenario_name="all_snowed",
        cleaned_lanes=set(),
        fallback_speed=args.snowed_speed,
        roads=roads,
        all_lanes=all_lanes,
        lane_weights=lane_weights,
        root_log_dir=log_dir,
    )

    all_cleaned_row = await run_extreme_scenario(
        args=args,
        scenario_name="all_cleaned",
        cleaned_lanes=set(all_lanes),
        fallback_speed=args.normal_speed,
        roads=roads,
        all_lanes=all_lanes,
        lane_weights=lane_weights,
        root_log_dir=log_dir,
    )

    summary_rows = [all_snowed_row, all_cleaned_row]
    summary_csv = os.path.join(log_dir, "extreme_scenarios_summary.csv")
    summary_json = os.path.join(log_dir, "extreme_scenarios_summary.json")
    write_rows_csv_json(summary_rows, summary_csv, summary_json)

    delta_row = {
        "delta_global_avg_speed_clean_minus_snow": float(all_cleaned_row["global_avg_speed"]) - float(all_snowed_row["global_avg_speed"]),
        "delta_filled_weighted_avg_speed_clean_minus_snow": float(all_cleaned_row["filled_weighted_avg_speed"]) - float(all_snowed_row["filled_weighted_avg_speed"]),
        "delta_ATT_clean_minus_snow": float(all_cleaned_row["ATT"]) - float(all_snowed_row["ATT"]),
        "delta_Throughput_clean_minus_snow": float(all_cleaned_row["Throughput"]) - float(all_snowed_row["Throughput"]),
    }
    delta_csv = os.path.join(log_dir, "extreme_scenarios_delta.csv")
    delta_json = os.path.join(log_dir, "extreme_scenarios_delta.json")
    write_rows_csv_json([delta_row], delta_csv, delta_json)

    print(f"Saved summary to {summary_csv}")
    print(f"Saved summary to {summary_json}")
    print(f"Saved delta to {delta_csv}")
    print(f"Saved delta to {delta_json}")


if __name__ == "__main__":
    asyncio.run(main())
