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
# from Snowplowing.get_random_path import get_random_snowplow_path


DEFAULT_BEST_PATH = "/home/nkk/work/src/python/Snowplowing/plowing_path/greedy_region_partitioned.json"
DEFAULT_RANDOM_PATH = "/home/nkk/work/src/python/Snowplowing/plowing_path/random.json"


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

    async def step(self):
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
        description=(
            "Snowplowing validation using independent simulations for each interval "
            "with cumulative cleaned-road lane speed limits."
        )
    )
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--exp", type=str, default="snow_lane_limit_independent")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--warmup_steps", type=int, default=20)
    parser.add_argument("--interval_steps", type=int, default=50)
    parser.add_argument(
        "--speed_sample_every",
        type=int,
        default=200,
        help="每多少个仿真步采样一次速度用于区间均值统计（>=1）",
    )
    parser.add_argument("--max_intervals", type=int, default=10)
    parser.add_argument("--snowed_speed", type=float, default=2.0)
    parser.add_argument("--normal_speed", type=float, default=20.0)
    parser.add_argument(
        "--compare",
        type=str,
        default="single",
        choices=["single", "best_random", "best_random_fair"],
        help=(
            "single: 只跑 --path_file；best_random: 自动对比 best 与 random；"
            "best_random_fair: best 对比多次 seeded random 的统计结果"
        ),
    )
    parser.add_argument(
        "--path_file",
        type=str,
        default=DEFAULT_BEST_PATH,
        help="扫雪路径文件",
    )
    parser.add_argument("--best_path_file", type=str, default=DEFAULT_BEST_PATH)
    parser.add_argument("--random_path_file", type=str, default=DEFAULT_RANDOM_PATH)
    parser.add_argument("--random_runs", type=int, default=5, help="fair 模式下 random 重复次数")
    parser.add_argument("--random_seed", type=int, default=42, help="fair 模式下 random 起始 seed")
    parser.add_argument(
        "--random_net_file",
        type=str,
        default="/home/nkk/work/src/python/Snowplowing/Shaped.net.xml",
        help="fair 模式生成 random 路径时使用的路网文件",
    )
    parser.add_argument(
        "--random_record_window",
        type=int,
        default=0,
        help="fair 模式生成 random 路径的时间窗口（秒）；<=0 时自动用 interval*interval_steps",
    )
    return parser.parse_args()


def parse_intervals(snow_paths: Dict[str, Dict[str, List[str]]], max_intervals: int) -> List[str]:
    return sorted(snow_paths.keys(), key=lambda name: int(name.split("_")[1]))[:max_intervals]


def collect_cleaned_roads(interval_data: Dict[str, List[str]]) -> Set[str]:
    cleaned_roads: Set[str] = set()
    for road_list in interval_data.values():
        cleaned_roads.update(str(road_id) for road_id in road_list)
    return cleaned_roads


def build_cumulative_cleaned_roads(
    snow_paths: Dict[str, Dict[str, List[str]]], intervals: Sequence[str]
) -> Dict[str, Set[str]]:
    cumulative: Set[str] = set()
    cleaned_by_interval: Dict[str, Set[str]] = {}
    for interval_name in intervals:
        cumulative.update(collect_cleaned_roads(snow_paths[interval_name]))
        cleaned_by_interval[str(interval_name)] = set(cumulative)
    return cleaned_by_interval


async def build_road_lane_cache(env: Env, roads: Sequence[str]) -> Dict[str, Set[str]]:
    road_to_lanes: Dict[str, Set[str]] = {}
    for road_id in roads:
        lane_ids = await env.eng.get_lane_id_of_road(road_id)
        road_to_lanes[str(road_id)] = set(str(lane_id) for lane_id in lane_ids)
    return road_to_lanes


async def set_lane_speed_limit(lane_ids: Sequence[str], max_v: float, env: Env) -> None:
    for lane_id in lane_ids:
        await env.eng.client.lane_service.SetLaneMaxV({"lane_id": lane_id, "max_v": max_v})


async def collect_speed_snapshot(
    env: Env,
    roads: Sequence[str],
    cleaned_roads: Set[str],
    lane_weights: Dict[str, int],
    normal_speed: float,
    snowed_speed: float,
) -> Tuple[Dict[str, float], float, float]:
    total_active_speed = 0.0
    active_count = 0
    weighted_filled_sum = 0.0
    weighted_sum = 0.0
    road_speed_map: Dict[str, float] = {}

    for road_id in roads:
        road_key = str(road_id)
        avg_v = await env.eng.get_road_avg_v(road_id)
        lane_w = max(1, int(lane_weights.get(road_key, 1)))

        if avg_v is not None:
            speed_value = float(avg_v)
            road_speed_map[road_key] = speed_value
            total_active_speed += speed_value
            active_count += 1
            filled_speed = speed_value
        else:
            filled_speed = float(normal_speed if road_key in cleaned_roads else snowed_speed)

        weighted_filled_sum += filled_speed * lane_w
        weighted_sum += lane_w

    active_avg_speed = total_active_speed / active_count if active_count > 0 else 0.0
    filled_weighted_avg_speed = weighted_filled_sum / weighted_sum if weighted_sum > 0 else 0.0
    return road_speed_map, active_avg_speed, filled_weighted_avg_speed


def lane_sets_for_interval(
    roads: Sequence[str],
    road_to_lanes: Dict[str, Set[str]],
    cleaned_roads: Set[str],
) -> Tuple[Set[str], Set[str]]:
    cleaned_lanes: Set[str] = set()
    snowed_lanes: Set[str] = set()
    for road_id in roads:
        road_key = str(road_id)
        lanes = road_to_lanes.get(road_key, set())
        if road_key in cleaned_roads:
            cleaned_lanes.update(lanes)
        else:
            snowed_lanes.update(lanes)
    return cleaned_lanes, snowed_lanes


def validate_path_file(path_file: str) -> None:
    if not os.path.exists(path_file):
        raise FileNotFoundError(f"Path file not found: {path_file}")


def write_rows_csv_json(rows: Sequence[Dict[str, object]], csv_path: str, json_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as file_obj:
        json.dump(list(rows), file_obj, ensure_ascii=False, indent=2)


def build_comparison_rows(
    best_results: Sequence[Dict[str, object]],
    random_results: Sequence[Dict[str, object]],
    best_interval_aux: Dict[str, Dict[str, object]],
    random_interval_aux: Dict[str, Dict[str, object]],
) -> List[Dict[str, object]]:
    random_by_interval = {str(row["interval_name"]): row for row in random_results}
    comparison_rows: List[Dict[str, object]] = []

    for best_row in best_results:
        interval_name = str(best_row["interval_name"])
        random_row = random_by_interval.get(interval_name)
        if random_row is None:
            continue

        best_speed = float(best_row["global_avg_speed"])
        random_speed = float(random_row["global_avg_speed"])
        # best_att = float(best_row["ATT"])
        # random_att = float(random_row["ATT"])
        # best_throughput = float(best_row["Throughput"])
        # random_throughput = float(random_row["Throughput"])

        best_aux = best_interval_aux.get(interval_name, {})
        random_aux = random_interval_aux.get(interval_name, {})
        best_cleaned_roads = set(best_aux.get("cleaned_roads", set()))
        random_cleaned_roads = set(random_aux.get("cleaned_roads", set()))
        union_roads = best_cleaned_roads | random_cleaned_roads

        best_road_speeds = best_aux.get("road_speeds", {})
        random_road_speeds = random_aux.get("road_speeds", {})

        best_union_speed_values = [
            float(best_road_speeds[road_id])
            for road_id in union_roads
            if road_id in best_road_speeds and best_road_speeds[road_id] is not None
        ]
        random_union_speed_values = [
            float(random_road_speeds[road_id])
            for road_id in union_roads
            if road_id in random_road_speeds and random_road_speeds[road_id] is not None
        ]

        best_union_avg_speed = (
            statistics.fmean(best_union_speed_values) if best_union_speed_values else 0.0
        )
        random_union_avg_speed = (
            statistics.fmean(random_union_speed_values) if random_union_speed_values else 0.0
        )

        comparison_rows.append(
            {
                "interval_name": interval_name,
                "best_global_avg_speed": best_speed,
                "random_global_avg_speed": random_speed,
                "delta_global_avg_speed": best_speed - random_speed,
                "union_road_count": len(union_roads),
                "best_union_avg_speed": best_union_avg_speed,
                "random_union_avg_speed": random_union_avg_speed,
                "delta_union_avg_speed": best_union_avg_speed - random_union_avg_speed,
                # "best_ATT": best_att,
                # "random_ATT": random_att,
                # "delta_ATT": best_att - random_att,
                # "best_Throughput": best_throughput,
                # "random_Throughput": random_throughput,
                # "delta_Throughput": best_throughput - random_throughput,
            }
        )

    return comparison_rows


def build_fair_comparison_rows(
    best_results: Sequence[Dict[str, object]],
    random_runs_results: Sequence[Sequence[Dict[str, object]]],
) -> List[Dict[str, object]]:
    random_by_interval: Dict[str, List[Dict[str, object]]] = {}
    for run_rows in random_runs_results:
        for row in run_rows:
            interval_name = str(row["interval_name"])
            random_by_interval.setdefault(interval_name, []).append(row)

    comparison_rows: List[Dict[str, object]] = []
    for best_row in best_results:
        interval_name = str(best_row["interval_name"])
        peers = random_by_interval.get(interval_name, [])
        if not peers:
            continue

        random_speed_values = [float(row["global_avg_speed"]) for row in peers]
        # random_att_values = [float(row["ATT"]) for row in peers]
        # random_throughput_values = [float(row["Throughput"]) for row in peers]

        random_speed_mean = statistics.fmean(random_speed_values)
        # random_att_mean = statistics.fmean(random_att_values)
        # random_throughput_mean = statistics.fmean(random_throughput_values)

        comparison_rows.append(
            {
                "interval_name": interval_name,
                "best_global_avg_speed": float(best_row["global_avg_speed"]),
                "random_mean_global_avg_speed": random_speed_mean,
                "random_std_global_avg_speed": statistics.pstdev(random_speed_values),
                "delta_global_avg_speed": float(best_row["global_avg_speed"]) - random_speed_mean,
                # "best_ATT": float(best_row["ATT"]),
                # "random_mean_ATT": random_att_mean,
                # "random_std_ATT": statistics.pstdev(random_att_values),
                # "delta_ATT": float(best_row["ATT"]) - random_att_mean,
                # "best_Throughput": float(best_row["Throughput"]),
                # "random_mean_Throughput": random_throughput_mean,
                # "random_std_Throughput": statistics.pstdev(random_throughput_values),
                # "delta_Throughput": float(best_row["Throughput"]) - random_throughput_mean,
                # "random_runs": len(peers),
                "speed_win_rate_best": sum(float(best_row["global_avg_speed"]) > v for v in random_speed_values)
                / len(random_speed_values),
            }
        )

    return comparison_rows


async def bootstrap_network_cache(
    args: argparse.Namespace,
    log_dir: str,
) -> Tuple[List[str], Dict[str, Set[str]], Dict[str, int]]:
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
        road_to_lanes = await build_road_lane_cache(env, roads)
        lane_weights = {
            str(road_id): max(1, len(lane_ids)) for road_id, lane_ids in road_to_lanes.items()
        }
        return roads, road_to_lanes, lane_weights
    finally:
        env.eng._stop_simulator()


async def run_interval_evaluation(
    args: argparse.Namespace,
    strategy_name: str,
    interval_index: int,
    interval_name: str,
    cleaned_roads: Set[str],
    roads: Sequence[str],
    road_to_lanes: Dict[str, Set[str]],
    lane_weights: Dict[str, int],
    root_log_dir: str,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    run_log_dir = os.path.join(root_log_dir, strategy_name, interval_name)
    os.makedirs(run_log_dir, exist_ok=True)

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=args.warmup_steps + args.interval_steps,
        log_dir=run_log_dir,
    )

    try:
        for _ in range(args.warmup_steps):
            await env.step()

        cleaned_lanes, snowed_lanes = lane_sets_for_interval(roads, road_to_lanes, cleaned_roads)
        await set_lane_speed_limit(sorted(cleaned_lanes), args.normal_speed, env)
        await set_lane_speed_limit(sorted(snowed_lanes), args.snowed_speed, env)

        sampled_active_speed_values: List[float] = []
        sampled_filled_speed_values: List[float] = []
        road_speed_map: Dict[str, float] = {}

        for step_in_interval in range(1, args.interval_steps + 1):
            await env.step()
            if step_in_interval % args.speed_sample_every == 0:
                snapshot_map, active_avg, filled_avg = await collect_speed_snapshot(
                    env=env,
                    roads=roads,
                    cleaned_roads=cleaned_roads,
                    lane_weights=lane_weights,
                    normal_speed=args.normal_speed,
                    snowed_speed=args.snowed_speed,
                )
                road_speed_map = snapshot_map
                sampled_active_speed_values.append(active_avg)
                sampled_filled_speed_values.append(filled_avg)

        if not sampled_active_speed_values:
            snapshot_map, active_avg, filled_avg = await collect_speed_snapshot(
                env=env,
                roads=roads,
                cleaned_roads=cleaned_roads,
                lane_weights=lane_weights,
                normal_speed=args.normal_speed,
                snowed_speed=args.snowed_speed,
            )
            road_speed_map = snapshot_map
            sampled_active_speed_values.append(active_avg)
            sampled_filled_speed_values.append(filled_avg)

        interval_active_avg_speed = statistics.fmean(sampled_active_speed_values)
        interval_filled_weighted_avg_speed = statistics.fmean(sampled_filled_speed_values)
        global_avg_speed = interval_active_avg_speed

        att = await env.eng.get_departed_vehicle_average_traveling_time()
        throughput = await env.eng.get_finished_vehicle_count()

        row = {
            "strategy": strategy_name,
            "evaluation_mode": "independent_interval",
            "interval_index": interval_index,
            "interval_name": interval_name,
            # "cleaned_road_count": len(cleaned_roads),
            "cleaned_lane_count": len(cleaned_lanes),
            "snowed_lane_count": len(snowed_lanes),
            "global_avg_speed": global_avg_speed,
            # "active_interval_avg_speed": interval_active_avg_speed,
            # "filled_weighted_interval_avg_speed": interval_filled_weighted_avg_speed,
            # "speed_sample_count": len(sampled_active_speed_values),
            # "ATT": att,
            # "Throughput": throughput,
        }
        aux = {
            "cleaned_roads": set(cleaned_roads),
            "road_speeds": road_speed_map,
        }
        # print(
        #     f"[{strategy_name}][{interval_name}] mode=independent_interval "
        #     f"cleaned_road={len(cleaned_roads)} cleaned_lane={len(cleaned_lanes)} "
        #     f"active_avg_speed={interval_active_avg_speed:.3f} "
        #     f"filled_weighted_avg_speed={interval_filled_weighted_avg_speed:.3f} "
        #     f"ATT={att:.3f} Throughput={throughput}"
        # )
        return row, aux
    finally:
        env.eng._stop_simulator()


async def run_single_strategy(
    args: argparse.Namespace,
    strategy_name: str,
    path_file: str,
    root_log_dir: str,
    roads: Sequence[str],
    road_to_lanes: Dict[str, Set[str]],
    lane_weights: Dict[str, int],
) -> Dict[str, object]:
    validate_path_file(path_file)

    with open(path_file, "r", encoding="utf-8") as file_obj:
        snow_paths = json.load(file_obj)

    if args.speed_sample_every <= 0:
        raise ValueError("--speed_sample_every must be >= 1")

    intervals = parse_intervals(snow_paths, args.max_intervals)
    if not intervals:
        raise ValueError(f"No intervals found in path file: {path_file}")

    cumulative_cleaned_by_interval = build_cumulative_cleaned_roads(snow_paths, intervals)

    results: List[Dict[str, object]] = []
    interval_aux: Dict[str, Dict[str, object]] = {}

    for idx, interval_name in enumerate(intervals, start=1):
        row, aux = await run_interval_evaluation(
            args=args,
            strategy_name=strategy_name,
            interval_index=idx,
            interval_name=interval_name,
            cleaned_roads=cumulative_cleaned_by_interval[interval_name],
            roads=roads,
            road_to_lanes=road_to_lanes,
            lane_weights=lane_weights,
            root_log_dir=root_log_dir,
        )
        results.append(row)
        interval_aux[str(interval_name)] = aux

    strategy_log_dir = os.path.join(root_log_dir, strategy_name)
    os.makedirs(strategy_log_dir, exist_ok=True)
    csv_path = os.path.join(strategy_log_dir, f"{strategy_name}_summary.csv")
    json_path = os.path.join(strategy_log_dir, f"{strategy_name}_summary.json")
    write_rows_csv_json(results, csv_path, json_path)
    print(f"Saved {strategy_name} results to {csv_path}")
    return {
        "rows": results,
        "interval_aux": interval_aux,
    }


async def main():
    args = parse_args()
    # log_dir = time.strftime(f"log/snow/{args.exp}/%Y%m%d-%H%M%S")
    log_dir = f"res"
    os.makedirs(log_dir, exist_ok=True)

    roads, road_to_lanes, lane_weights = await bootstrap_network_cache(
        args=args,
        log_dir=os.path.join(log_dir, "bootstrap"),
    )

    if args.compare == "single":
        single_result = await run_single_strategy(
            args,
            "single",
            args.path_file,
            log_dir,
            roads,
            road_to_lanes,
            lane_weights,
        )
        results = single_result["rows"]
        csv_path = os.path.join(log_dir, "single", "single_summary.csv")
        json_path = os.path.join(log_dir, "single", "single_summary.json")
        print(f"Saved results to {csv_path}")
        print(f"Saved results to {json_path}")
        if not results:
            raise RuntimeError("No results generated")
        return

    # if args.compare == "best_random_fair":
    #     if args.random_runs <= 0:
    #         raise ValueError("--random_runs must be > 0")

    #     best_result = await run_single_strategy(
    #         args,
    #         "best",
    #         args.best_path_file,
    #         log_dir,
    #         roads,
    #         road_to_lanes,
    #         lane_weights,
    #     )
    #     best_results = best_result["rows"]

    #     random_path_dir = os.path.join(log_dir, "random_paths")
    #     os.makedirs(random_path_dir, exist_ok=True)

    #     random_runs_results: List[List[Dict[str, object]]] = []
    #     for run_idx in range(args.random_runs):
    #         seed = args.random_seed + run_idx
    #         random_path_file = os.path.join(
    #             random_path_dir,
    #             f"cleaned_roads_by_interval_random_seed_{seed}.json",
    #         )
    #         random_record_window = (
    #             args.random_record_window
    #             if args.random_record_window > 0
    #             else args.interval * args.interval_steps
    #         )

    #         get_random_snowplow_path(
    #             net_path=args.random_net_file,
    #             clean_speed=3.0,
    #             pass_speed=10.0,
    #             route_save_path=random_path_file,
    #             record_window=random_record_window,
    #             seed=seed,
    #         )

    #         run_name = f"random_seed_{seed}"
    #         run_result = await run_single_strategy(
    #             args,
    #             run_name,
    #             random_path_file,
    #             log_dir,
    #             roads,
    #             road_to_lanes,
    #             lane_weights,
    #         )
    #         random_runs_results.append(run_result["rows"])

    #     comparison_rows = build_fair_comparison_rows(best_results, random_runs_results)
    #     if not comparison_rows:
    #         raise RuntimeError("Failed to build fair comparison rows")

    #     comparison_csv = os.path.join(log_dir, "best_vs_random_fair_comparison.csv")
    #     comparison_json = os.path.join(log_dir, "best_vs_random_fair_comparison.json")
    #     write_rows_csv_json(comparison_rows, comparison_csv, comparison_json)

    #     print(f"Saved fair comparison to {comparison_csv}")
    #     print(f"Saved fair comparison to {comparison_json}")
    #     return

    best_result = await run_single_strategy(
        args,
        "best",
        args.best_path_file,
        log_dir,
        roads,
        road_to_lanes,
        lane_weights,
    )
    random_result = await run_single_strategy(
        args,
        "random",
        args.random_path_file,
        log_dir,
        roads,
        road_to_lanes,
        lane_weights,
    )
    comparison_rows = build_comparison_rows(
        best_result["rows"],
        random_result["rows"],
        best_result["interval_aux"],
        random_result["interval_aux"],
    )
    if not comparison_rows:
        raise RuntimeError("Failed to build best vs random comparison rows")

    comparison_csv = os.path.join(log_dir, "best_vs_random_comparison.csv")
    comparison_json = os.path.join(log_dir, "best_vs_random_comparison.json")
    write_rows_csv_json(comparison_rows, comparison_csv, comparison_json)

    print(f"Saved comparison to {comparison_csv}")
    print(f"Saved comparison to {comparison_json}")


if __name__ == "__main__":
    asyncio.run(main())