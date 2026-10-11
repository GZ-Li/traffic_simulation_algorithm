import argparse
import asyncio
import csv
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

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

with open('waterlogging_points.json', 'r', encoding='utf-8') as f:
    Waterlogging_Points = json.load(f)
with open('waterlogging_strategy.json', 'r', encoding='utf-8') as f:
    Waterlogging_Strategy = json.load(f)

FLOOD_POINTS: Dict[str, List[str]] = Waterlogging_Points
DEFAULT_ORDER = list(Waterlogging_Strategy.keys())

# FLOOD_POINTS: Dict[str, List[str]] = {
#     "300000545": [
#         "200000162", "200000184", "200000201", "200000157", "200000143",
#         "200000202", "200000135", "200000180", "200000161", "200000131",
#         "200000132", "200000134", "200012001", "200000133", "200000218",
#         "200000213", "200000142", "200000122", "200000124", "200000166",
#         "200000121", "200007002", "200000136", "200002565", "200000186",
#         "200000185", "200000225", "200013435", "200002466",
#     ],
#     "300001682": [
#         "200011001", "200002015", "200001676", "200001467", "200001571",
#         "200013003", "200000021", "200001988", "200013004", "200001989",
#         "200001709", "200002014", "200002013", "200002018", "200001990",
#         "200002137", "200002016", "200001987", "200001572", "200001455",
#         "200002186", "200007062", "200001476", "200007063", "200001781",
#         "200001708", "200001568", "200002019", "200001462", "200001992",
#         "200001693", "200001994", "200001465", "200002010", "200001696",
#         "200001457", "200001570", "200001993", "200001454", "200001575",
#         "200001694", "200002011",
#     ],
#     "300001249": [
#         "200001512", "200001503", "200001500", "200001513", "200001498",
#         "200001490", "200001516", "200001497", "200001473", "200001491",
#     ],
#     "300001252": ["200001250", "200001253", "200001248", "200001243"],
# }

# DEFAULT_ORDER = ["300000545", "300001682", "300001249", "300001252"]


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
        description="Run multi-stage waterlogging validation with lane-level speed limits."
    )
    parser.add_argument("--exp", type=str, help="实验名称")
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--warmup_steps", type=int, default=90)
    parser.add_argument("--eval_steps", type=int, default=90)
    parser.add_argument("--max_clean_at_once", type=int, default=2)
    parser.add_argument("--flooded_speed", type=float, default=2.0)
    parser.add_argument("--normal_speed", type=float, default=20.0)
    parser.add_argument(
        "--order",
        type=str,
        default="best",
        choices=["best", "worst", "custom"],
        help="best/worst 按策略分数排序，custom 使用 --custom_order",
    )
    parser.add_argument(
        "--strategy_file",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "waterlogging_strategy.json"),
        help="策略分数字典 JSON 文件路径",
    )
    parser.add_argument(
        "--custom_order",
        type=str,
        help="custom 模式下的涝点顺序，逗号分隔，如 300000545,300001682",
    )
    return parser.parse_args()


def load_point_order(args: argparse.Namespace) -> List[str]:
    if args.order == "custom":
        if not args.custom_order:
            raise ValueError("--order custom 时必须提供 --custom_order")
        order = [item.strip() for item in args.custom_order.split(",") if item.strip()]
    else:
        scores = None
        if args.strategy_file and os.path.exists(args.strategy_file):
            with open(args.strategy_file, "r", encoding="utf-8") as file_obj:
                raw_scores = json.load(file_obj)
            scores = {str(key): float(value) for key, value in raw_scores.items()}

        if scores:
            reverse = args.order == "best"
            ranked_ids = [point_id for point_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=reverse)]
            order = [point_id for point_id in ranked_ids if point_id in FLOOD_POINTS]
        elif args.order == "best":
            order = list(DEFAULT_ORDER)
        else:
            order = list(reversed(DEFAULT_ORDER))

    missing = [point_id for point_id in order if point_id not in FLOOD_POINTS]
    if missing:
        raise ValueError(f"Unknown flood point ids in order: {missing}")

    unseen = [point_id for point_id in FLOOD_POINTS if point_id not in order]
    order.extend(unseen)
    return order


def build_batches(point_order: Sequence[str], max_clean_at_once: int) -> List[List[str]]:
    return [
        list(point_order[index:index + max_clean_at_once])
        for index in range(0, len(point_order), max_clean_at_once)
    ]


def flatten_roads(point_ids: Sequence[str]) -> List[str]:
    roads: List[str] = []
    for point_id in point_ids:
        roads.extend(FLOOD_POINTS[point_id])
    return sorted(set(roads))


async def get_vehicle_ids_of_roads(env: Env, road_ids: Sequence[str]) -> set:
    """Return the set of vehicle IDs currently on any of the given roads."""
    ids: set = set()
    for road_id in road_ids:
        ids.update(await env.eng.get_vehicle_ids_of_road(road_id))
    return ids


async def get_avg_speed_of_vehicles(env: Env, vehicle_ids: Sequence[str]) -> Optional[float]:
    """Return average speed of the given vehicles, or None when the set is empty."""
    if not vehicle_ids:
        return None

    speed_sum = 0.0
    count = 0
    for vehicle_id in vehicle_ids:
        speed_sum += await env.eng.get_vehicle_speed(vehicle_id)
        count += 1
    return speed_sum / count if count > 0 else None


async def set_lane_speed_limit_for_roads(env: Env, road_ids: Sequence[str], max_v: float) -> int:
    lane_ids = set()
    for road_id in road_ids:
        lane_ids.update(await env.eng.get_lane_id_of_road(road_id))

    for lane_id in lane_ids:
        await env.eng.client.lane_service.SetLaneMaxV({"lane_id": lane_id, "max_v": max_v})

    return len(lane_ids)


async def run_single_stage(
    args: argparse.Namespace,
    root_log_dir: str,
    stage_index: int,
    batches: Sequence[Sequence[str]],
    point_order: Sequence[str],
    baseline_flooded_roads: Sequence[str],
) -> Dict[str, object]:
    cleaned_points = [point_id for batch in batches[:stage_index] for point_id in batch]
    flooded_points = [point_id for point_id in point_order if point_id not in cleaned_points]

    cleaned_roads = flatten_roads(cleaned_points)
    flooded_roads = flatten_roads(flooded_points)
    stage_name = f"stage_{stage_index}"
    stage_dir = os.path.join(root_log_dir, stage_name)
    os.makedirs(stage_dir, exist_ok=True)

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=args.start,
        step_size=args.interval,
        step_count=args.warmup_steps + args.eval_steps,
        log_dir=stage_dir,
    )

    final_info = None
    try:
        for _ in range(args.warmup_steps):
            await env.step()

        cleaned_lane_count = await set_lane_speed_limit_for_roads(env, cleaned_roads, args.normal_speed)
        flooded_lane_count = await set_lane_speed_limit_for_roads(env, flooded_roads, args.flooded_speed)

        # Track throughput on baseline flooded roads (fixed reference set from stage 0).
        # This allows us to track the improvement across stages against a consistent road set.
        prev_on_baseline_flooded = await get_vehicle_ids_of_roads(env, baseline_flooded_roads)
        flooded_throughput = 0

        # Track avg speed on baseline flooded roads across the whole evaluation phase.
        flooded_speed_sum = 0.0
        flooded_speed_obs_count = 0

        for _ in range(args.eval_steps):
            done, info = await env.step()
            curr_on_baseline_flooded = await get_vehicle_ids_of_roads(env, baseline_flooded_roads)
            flooded_throughput += len(prev_on_baseline_flooded - curr_on_baseline_flooded)

            curr_avg_speed = await get_avg_speed_of_vehicles(env, list(curr_on_baseline_flooded))
            if curr_avg_speed is not None:
                flooded_speed_sum += curr_avg_speed * len(curr_on_baseline_flooded)
                flooded_speed_obs_count += len(curr_on_baseline_flooded)

            prev_on_baseline_flooded = curr_on_baseline_flooded
            if done:
                final_info = info

        if final_info is None:
            final_info = {
                "ATT": await env.eng.get_departed_vehicle_average_traveling_time(),
                "Throughput": await env.eng.get_finished_vehicle_count(),
            }

        flooded_avg_speed = (
            flooded_speed_sum / flooded_speed_obs_count
            if flooded_speed_obs_count > 0
            else 0.0
        )

        result = {
            "stage_index": stage_index,
            "stage_name": stage_name,
            "cleaned_points": ",".join(cleaned_points),
            "flooded_points": ",".join(flooded_points),
            # "cleaned_point_count": len(cleaned_points),
            # "flooded_point_count": len(flooded_points),
            # "cleaned_road_count": len(cleaned_roads),
            # "flooded_road_count": len(flooded_roads),
            # "cleaned_lane_count": cleaned_lane_count,
            # "flooded_lane_count": flooded_lane_count,
            # "ATT": final_info["ATT"],
            # "Throughput": final_info["Throughput"],
            # "flooded_throughput": flooded_throughput,
            "flooded_avg_speed": flooded_avg_speed,
        }

        with open(os.path.join(stage_dir, "result.json"), "w", encoding="utf-8") as file_obj:
            json.dump(result, file_obj, ensure_ascii=False, indent=2)
        return result
    finally:
        env.eng._stop_simulator()


def write_summary(root_log_dir: str, args: argparse.Namespace, point_order: Sequence[str], results: Sequence[Dict[str, object]]):
    summary_csv = os.path.join(root_log_dir, "stage_summary.csv")
    summary_json = os.path.join(root_log_dir, "stage_summary.json")
    config_json = os.path.join(root_log_dir, "run_config.json")

    with open(summary_csv, "w", newline="", encoding="utf-8-sig") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    with open(summary_json, "w", encoding="utf-8") as file_obj:
        json.dump(list(results), file_obj, ensure_ascii=False, indent=2)

    run_config = {
        "order": list(point_order),
        "interval": args.interval,
        "warmup_steps": args.warmup_steps,
        "eval_steps": args.eval_steps,
        "max_clean_at_once": args.max_clean_at_once,
        "flooded_speed": args.flooded_speed,
        "normal_speed": args.normal_speed,
        "strategy_file": args.strategy_file,
    }
    with open(config_json, "w", encoding="utf-8") as file_obj:
        json.dump(run_config, file_obj, ensure_ascii=False, indent=2)


async def main():
    args = parse_args()
    point_order = load_point_order(args)
    batches = build_batches(point_order, args.max_clean_at_once)
    
    # Compute baseline flooded roads (from all flood points, stage 0 state)
    baseline_flooded_roads = flatten_roads(point_order)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    exp_name = args.exp if args.exp else "lane_limit_validation"
    root_log_dir = os.path.join("res", exp_name, timestamp)
    os.makedirs(root_log_dir, exist_ok=True)

    results = []
    for stage_index in range(len(batches) + 1):
        result = await run_single_stage(args, root_log_dir, stage_index, batches, point_order, baseline_flooded_roads)
        print(
            f"[{result['stage_name']}] cleaned={result['cleaned_points'] or 'none'} "
            # f"ATT={result['ATT']:.3f} Throughput={result['Throughput']} "
            # f"flooded_throughput={result['flooded_throughput']} "
            f"flooded_avg_speed={result['flooded_avg_speed']:.3f}"
        )
        results.append(result)

    write_summary(root_log_dir, args, point_order, results)
    print(f"Saved summary to {root_log_dir}")


if __name__ == "__main__":
    asyncio.run(main())