import argparse
import asyncio
import os
import statistics
import sys
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trafficlight_rl.engine import get_engine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Minimal probe: set all lanes max speed to a target and compare network speed before/after."
    )
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument("--target_speed", type=float, default=1.0, help="Lane max speed to set (same unit as simulator API).")
    parser.add_argument("--warmup_steps", type=int, default=5)
    parser.add_argument("--probe_steps", type=int, default=20)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--sample_road_count", type=int, default=400)
    return parser.parse_args()


async def sample_mean_road_speed(eng, roads: List[str], sample_count: int) -> float:
    picked = roads[: min(sample_count, len(roads))]
    values = []
    for road_id in picked:
        avg_v = await eng.get_road_avg_v(road_id)
        if avg_v is not None:
            values.append(float(avg_v))
    return statistics.fmean(values) if values else 0.0


async def main() -> None:
    args = parse_args()

    total_steps = args.warmup_steps + args.probe_steps + 5
    eng = get_engine(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=0,
        total_step=total_steps,
        use_max_pressure=True,
    )

    try:
        roads = [str(road_id) for road_id in await eng.get_roads()]

        lane_states = await eng.client.lane_service.GetLane({"lane_ids": [], "exclude_person": False})
        lane_ids = [str(state["id"]) for state in lane_states["states"]]

        print(f"total_roads={len(roads)} total_lanes={len(lane_ids)} target_speed={args.target_speed}")

        for _ in range(args.warmup_steps):
            eng.next_step(args.interval)

        before = await sample_mean_road_speed(eng, roads, args.sample_road_count)
        print(f"before_mean_road_speed={before:.6f}")

        set_ok = 0
        set_fail = 0
        for lane_id in lane_ids:
            try:
                await eng.client.lane_service.SetLaneMaxV({"lane_id": lane_id, "max_v": args.target_speed})
                set_ok += 1
            except Exception:
                # Some lanes are not accepted by SetLaneMaxV in this simulator backend.
                set_fail += 1

        print(f"set_lane_maxv_ok={set_ok} set_lane_maxv_fail={set_fail}")

        # Sample multiple times after setting limits.
        after_samples = []
        for _ in range(args.probe_steps):
            eng.next_step(args.interval)
            after_samples.append(await sample_mean_road_speed(eng, roads, args.sample_road_count))

        after_mean = statistics.fmean(after_samples) if after_samples else 0.0
        after_min = min(after_samples) if after_samples else 0.0
        after_max = max(after_samples) if after_samples else 0.0

        print(f"after_mean_road_speed={after_mean:.6f}")
        print(f"after_min_road_speed={after_min:.6f}")
        print(f"after_max_road_speed={after_max:.6f}")
        print(f"delta_after_minus_before={after_mean - before:.6f}")

    finally:
        eng._stop_simulator()


if __name__ == "__main__":
    asyncio.run(main())
