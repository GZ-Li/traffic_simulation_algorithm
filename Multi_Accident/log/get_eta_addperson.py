"""
Multi_Accident — ETA measurement via AddPerson with explicit road_ids.

Instead of reading road average speeds at step 60 (get_speed.py), this script
injects a probe vehicle along each candidate path in shortest_paths.json and
measures the actual travel time inside the simulator.

shortest_paths.json format:
  {hospital_id: {accident_id: [{rank, path, num_edges}]}}

Output (in log/multi_accident/<exp>/<timestamp>/):
  eta_by_addperson.csv
  eta_by_addperson.json
"""

import argparse
import asyncio
import copy
import csv
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pymongo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
            output_sql_dsn="",
            output_job_prefix="multi_accident_addperson_",
            output_bbox=None,
        )
        self.step_size = step_size

    async def step_once(self):
        self.eng.next_step(self.step_size)


# ---------------------------------------------------------------------------
# Person template helpers
# ---------------------------------------------------------------------------

def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    field_map = {
        "vehicle_attribute": "vehicleAttribute",
        "bus_attribute": "busAttribute",
        "pedestrian_attribute": "pedestrianAttribute",
        "bike_attribute": "bikeAttribute",
        "output_when_sleep": "outputWhenSleep",
    }
    normalized: Dict[str, Any] = {}
    for key, value in person.items():
        normalized[field_map.get(key, key)] = value

    normalized.pop("id", None)
    normalized.pop("base", None)

    allowed = {
        "attribute",
        "home",
        "schedules",
        "vehicleAttribute",
        "busAttribute",
        "pedestrianAttribute",
        "bikeAttribute",
        "labels",
        "profile",
        "work",
        "outputWhenSleep",
        "type",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def load_person_template_from_mongo(
    mongo_uri: str,
    agent_db: str,
    agent_coll: str,
) -> Dict[str, Any]:
    client = pymongo.MongoClient(mongo_uri)
    try:
        doc = client[agent_db][agent_coll].find_one({}, {"_id": 0})
        if not doc or not isinstance(doc.get("data"), dict):
            raise RuntimeError("Cannot find valid person template in Mongo collection")
        template = normalize_person_for_add(copy.deepcopy(doc["data"]))
        if not template:
            raise RuntimeError("Template becomes empty after normalization")
        return template
    finally:
        client.close()


def build_probe_person(
    template: Dict[str, Any],
    road_path: List[str],
    start_lane_id: int,
    end_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    person = copy.deepcopy(template)
    int_path = [int(r) for r in road_path]

    # Keep template type value from Mongo to match backend enum expectations.
    person["home"] = {
        "lane_position": {
            "lane_id": int(start_lane_id),
            "s": 1.0,
        }
    }
    person["schedules"] = [
        {
            "departure_time": int(departure_time),
            "loop_count": 1,
            "trips": [
                {
                    "mode": "TRIP_MODE_DRIVE_ONLY",
                    "end": {
                        "lane_position": {
                            "lane_id": int(end_lane_id),
                            "s": 1.0,
                        }
                    },
                    "routes": [
                        {
                            "driving": {
                                "road_ids": int_path,
                            }
                        }
                    ],
                }
            ],
        }
    ]
    return person


async def resolve_start_end_lanes(env: Env, road_path: List[str]) -> Tuple[int, int]:
    start_lanes = await env.eng.get_lane_id_of_road(road_path[0])
    end_lanes = await env.eng.get_lane_id_of_road(road_path[-1])
    if not start_lanes or not end_lanes:
        raise RuntimeError(
            f"Cannot resolve lane ids for path endpoints: {road_path[0]} -> {road_path[-1]}"
        )
    return int(start_lanes[0]), int(end_lanes[0])


# ---------------------------------------------------------------------------
# Core probe measurement
# ---------------------------------------------------------------------------

async def run_probe_and_measure_eta(
    env: Env,
    person_template: Dict[str, Any],
    road_path: List[str],
    departure_delay_steps: int,
    max_probe_steps: int,
) -> Dict[str, Any]:
    start_lane_id, end_lane_id = await resolve_start_end_lanes(env, road_path)

    now = await env.eng.get_current_time()
    departure_time = int(now["t"]) + departure_delay_steps * env.step_size

    person_payload = build_probe_person(
        template=person_template,
        road_path=road_path,
        start_lane_id=start_lane_id,
        end_lane_id=end_lane_id,
        departure_time=departure_time,
    )

    add_resp = await env.eng.client.person_service.AddPerson({"person": person_payload})
    person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
    if not isinstance(person_id, int):
        raise RuntimeError(f"AddPerson failed or no person_id returned: {add_resp}")

    started = False
    started_time: Optional[int] = None
    arrived_time: Optional[int] = None
    arrived_reason = "not_arrived"
    destination_road = str(road_path[-1])
    last_seen_road: Optional[str] = None

    for _ in range(max_probe_steps):
        await env.step_once()
        sim_clock = await env.eng.get_current_time()
        now_t = int(sim_clock["t"])

        current_road = await env.eng.get_road_id_with_vehicle(person_id)
        current_road_str = str(current_road) if current_road is not None else None

        if not started and current_road_str is not None:
            started = True
            started_time = now_t

        if started and current_road_str == destination_road:
            arrived_time = now_t
            arrived_reason = "reach_destination_road"
            break

        if started and current_road_str is None and last_seen_road is not None:
            arrived_time = now_t
            arrived_reason = "disappeared_after_departure"
            break

        last_seen_road = current_road_str

    eta_seconds: Optional[int] = None
    if started_time is not None and arrived_time is not None:
        eta_seconds = max(0, arrived_time - started_time)

    return {
        "person_id": person_id,
        "departure_time": departure_time,
        "started_time": started_time,
        "arrived_time": arrived_time,
        "eta_seconds": eta_seconds,
        "arrival_reason": arrived_reason,
        "destination_road": destination_road,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_results(log_dir: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(log_dir, exist_ok=True)
    csv_path = os.path.join(log_dir, "eta_by_addperson.csv")
    json_path = os.path.join(log_dir, "eta_by_addperson.json")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(rows, json_file, ensure_ascii=False, indent=2)

    print(f"Saved CSV: {csv_path}")
    print(f"Saved JSON: {json_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(
        description=(
            "Multi_Accident: measure ETA for each candidate path by injecting "
            "a probe vehicle with explicit road_ids via AddPerson."
        )
    )
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument(
        "--paths_file", type=str, default="/home/nkk/work/src/python/Multi_Accident/shortest_paths.json",
        help="shortest_paths.json: {hospital_id: {accident_id: [{rank, path, num_edges}]}}"
    )
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument(
        "--warmup_steps", type=int, default=60,
        help="Steps to run before injecting probe (simulates the moment accident happens)"
    )
    parser.add_argument("--departure_delay_steps", type=int, default=3)
    parser.add_argument("--max_probe_steps", type=int, default=1800)
    parser.add_argument(
        "--max_paths_per_od", type=int, default=3,
        help="How many ranked paths to test per (hospital, accident) pair"
    )
    parser.add_argument("--exp", type=str, default="multi_accident_addperson")
    args = parser.parse_args()

    if not os.path.exists(args.paths_file):
        raise FileNotFoundError(f"paths file not found: {args.paths_file}")

    with open(args.paths_file, "r", encoding="utf-8") as f:
        paths_data = json.load(f)

    person_template = load_person_template_from_mongo(
        args.mongo_uri, args.agent_db, args.agent_coll
    )

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_dir = os.path.join("log", "multi_accident", args.exp, timestamp)
    os.makedirs(log_dir, exist_ok=True)

    rows: List[Dict[str, Any]] = []

    def _error_row(hospital_id, accident_id, rank, num_edges, path, reason):
        return {
            "hospital_id": str(hospital_id),
            "accident_id": str(accident_id),
            "path_rank": rank,
            "num_edges": num_edges,
            "person_id": None,
            "departure_time": None,
            "started_time": None,
            "arrived_time": None,
            "eta_seconds": None,
            "arrival_reason": reason,
            "destination_road": path[-1] if path else None,
        }

    for hospital_id, accident_dict in paths_data.items():
        if not isinstance(accident_dict, dict):
            print(f"Skipping hospital {hospital_id}: unexpected format")
            continue

        for accident_id, path_candidates in accident_dict.items():
            if not isinstance(path_candidates, list):
                rows.append(_error_row(hospital_id, accident_id, "-", 0, [], "invalid path list"))
                continue

            selected = path_candidates[: args.max_paths_per_od]

            for candidate in selected:
                rank = candidate.get("rank")
                path = [str(r) for r in candidate.get("path", [])]
                num_edges = int(candidate.get("num_edges", max(0, len(path) - 1)))

                if len(path) < 2:
                    rows.append(
                        _error_row(hospital_id, accident_id, rank, num_edges, path, "invalid_path_too_short")
                    )
                    continue

                step_count = (
                    args.warmup_steps
                    + args.max_probe_steps
                    + args.departure_delay_steps
                    + 10
                )
                env = Env(
                    mongo_uri=args.mongo_uri,
                    map_db=args.map_db,
                    map_coll=args.map_coll,
                    agent_db=args.agent_db,
                    agent_coll=args.agent_coll,
                    start_step=args.start,
                    step_size=args.interval,
                    step_count=step_count,
                    log_dir=log_dir,
                )

                try:
                    for _ in range(args.warmup_steps):
                        await env.step_once()

                    probe_result = await run_probe_and_measure_eta(
                        env=env,
                        person_template=person_template,
                        road_path=path,
                        departure_delay_steps=args.departure_delay_steps,
                        max_probe_steps=args.max_probe_steps,
                    )

                    row = {
                        "hospital_id": str(hospital_id),
                        "accident_id": str(accident_id),
                        "path_rank": rank,
                        "num_edges": num_edges,
                        **probe_result,
                    }
                    rows.append(row)

                    print(
                        f"hospital={hospital_id} accident={accident_id} "
                        f"rank={rank} eta={row['eta_seconds']} reason={row['arrival_reason']}"
                    )
                except Exception as exc:
                    rows.append(
                        _error_row(hospital_id, accident_id, rank, num_edges, path, f"exception: {exc}")
                    )
                    print(f"hospital={hospital_id} accident={accident_id} rank={rank} failed: {exc}")
                finally:
                    env.eng._stop_simulator()

    if not rows:
        raise RuntimeError("No results produced")

    write_results(log_dir, rows)


if __name__ == "__main__":
    asyncio.run(main())
