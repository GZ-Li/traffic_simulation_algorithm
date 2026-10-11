"""
Multi_Accident — ETA measurement via AddPerson with automatic routing (routes=[]).

Instead of specifying explicit road_ids, this script lets the simulator plan
the route automatically for each (hospital, accident) OD pair. Multiple probe
vehicles are injected sequentially to observe ETA variation under changing
traffic conditions, and the actual road trace is recorded.

shortest_paths.json is used only to extract OD pairs (start road, end road).
Format: {hospital_id: {accident_id: [{rank, path, num_edges}]}}

Output (in log/multi_accident/<exp>/<timestamp>/):
  eta_by_addperson_autoroute.csv
  eta_by_addperson_autoroute.json
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
    ):
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
            output_job_prefix="multi_accident_autoroute_",
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


def load_person_template_from_mongo(mongo_uri: str, agent_db: str, agent_coll: str) -> Dict[str, Any]:
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


async def resolve_start_end_lanes(env: Env, start_road: str, end_road: str) -> Tuple[int, int]:
    start_lanes = await env.eng.get_lane_id_of_road(start_road)
    end_lanes = await env.eng.get_lane_id_of_road(end_road)
    if not start_lanes or not end_lanes:
        raise RuntimeError(
            f"Cannot resolve lane ids for OD: {start_road} -> {end_road}"
        )
    return int(start_lanes[0]), int(end_lanes[0])


def build_probe_person_auto_route(
    template: Dict[str, Any],
    start_lane_id: int,
    end_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    person = copy.deepcopy(template)
    # Keep template type from Mongo to avoid enum mismatch in some deployments.
    person["home"] = {
        "lane_position": {
            "lane_id": int(start_lane_id),
            "s": 1.0,
        }
    }
    # routes=[] lets the simulator plan the route automatically
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
                    "routes": [],
                }
            ],
        }
    ]
    return person


# ---------------------------------------------------------------------------
# Core probe measurement
# ---------------------------------------------------------------------------

async def run_probe_and_measure_eta(
    env: Env,
    person_template: Dict[str, Any],
    start_road: str,
    destination_road: str,
    departure_delay_steps: int,
    max_probe_steps: int,
) -> Dict[str, Any]:
    start_lane_id, end_lane_id = await resolve_start_end_lanes(env, start_road, destination_road)

    now = await env.eng.get_current_time()
    departure_time = int(now["t"]) + departure_delay_steps * env.step_size

    person_payload = build_probe_person_auto_route(
        template=person_template,
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

    route_trace: List[str] = []
    last_road: Optional[str] = None

    for _ in range(max_probe_steps):
        await env.step_once()
        sim_clock = await env.eng.get_current_time()
        now_t = int(sim_clock["t"])

        current_road = await env.eng.get_road_id_with_vehicle(person_id)
        current_road_str = str(current_road) if current_road is not None else None

        if not started and current_road_str is not None:
            started = True
            started_time = now_t

        if current_road_str is not None and current_road_str != last_road:
            route_trace.append(current_road_str)

        if started and current_road_str == destination_road:
            arrived_time = now_t
            arrived_reason = "reach_destination_road"
            break

        if started and current_road_str is None and last_road is not None:
            arrived_time = now_t
            arrived_reason = "disappeared_after_departure"
            break

        last_road = current_road_str

    eta_seconds: Optional[int] = None
    if started_time is not None and arrived_time is not None:
        eta_seconds = max(0, arrived_time - started_time)
    elif arrived_reason == "not_arrived":
        arrived_reason = "max_probe_steps_reached_no_eta"

    return {
        "person_id": person_id,
        "departure_time": departure_time,
        "started_time": started_time,
        "arrived_time": arrived_time,
        "eta_seconds": eta_seconds,
        "arrival_reason": arrived_reason,
        "traced_road_count": len(route_trace),
        "traced_roads": ",".join(route_trace),
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_results(log_dir: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(log_dir, exist_ok=True)
    csv_path = os.path.join(log_dir, "eta_by_addperson_autoroute.csv")
    json_path = os.path.join(log_dir, "eta_by_addperson_autoroute.json")

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
            "Multi_Accident: measure ETA for each (hospital, accident) OD pair "
            "by injecting probe vehicles with automatic routing (routes=[])."
        )
    )
    parser.add_argument("--mongo_uri", type=str, required=True)
    parser.add_argument("--map_db", type=str, required=True)
    parser.add_argument("--map_coll", type=str, required=True)
    parser.add_argument("--agent_db", type=str, required=True)
    parser.add_argument("--agent_coll", type=str, required=True)
    parser.add_argument(
        "--paths_file", type=str, default="shortest_paths.json",
        help="shortest_paths.json: used only for OD pairs (start/end roads)"
    )
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument(
        "--warmup_steps", type=int, default=60,
        help="Steps to run before injecting probes (simulates moment accident happens)"
    )
    parser.add_argument("--departure_delay_steps", type=int, default=3)
    parser.add_argument("--max_probe_steps", type=int, default=1800)
    parser.add_argument(
        "--probes_per_od", type=int, default=3,
        help="Number of probe vehicles injected per OD pair to capture ETA variation"
    )
    parser.add_argument(
        "--probe_stagger_steps", type=int, default=5,
        help="Simulation steps between successive probe injections for the same OD"
    )
    parser.add_argument("--exp", type=str, default="multi_accident_autoroute")
    parser.add_argument(
        "--print_results_json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print full result rows as JSON to stdout (enabled by default)",
    )
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

    def _error_row(hospital_id, accident_id, probe_index, start_road, destination_road, reason):
        return {
            "hospital_id": str(hospital_id),
            "accident_id": str(accident_id),
            "probe_index": probe_index,
            "start_road": start_road,
            "destination_road": destination_road,
            "person_id": None,
            "departure_time": None,
            "started_time": None,
            "arrived_time": None,
            "eta_seconds": None,
            "arrival_reason": reason,
            "traced_road_count": 0,
            "traced_roads": "",
        }

    for hospital_id, accident_dict in paths_data.items():
        if not isinstance(accident_dict, dict):
            print(f"Skipping hospital {hospital_id}: unexpected format")
            continue

        for accident_id, path_candidates in accident_dict.items():
            if not isinstance(path_candidates, list) or not path_candidates:
                rows.append(
                    _error_row(hospital_id, accident_id, 0, None, None, "invalid or empty path list")
                )
                continue

            # Use rank-1 path only to extract OD endpoints; routing is handled by simulator
            first_candidate = path_candidates[0]
            path = [str(r) for r in first_candidate.get("path", [])]

            if len(path) < 2:
                rows.append(
                    _error_row(hospital_id, accident_id, 0, path[0] if path else None,
                               path[-1] if path else None, "invalid_path_too_short")
                )
                continue

            start_road = path[0]
            destination_road = path[-1]

            step_count = (
                args.warmup_steps
                + args.probes_per_od * (args.max_probe_steps + args.probe_stagger_steps)
                + args.departure_delay_steps
                + 30
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
            )

            try:
                for _ in range(args.warmup_steps):
                    await env.step_once()

                for probe_index in range(1, args.probes_per_od + 1):
                    probe_result = await run_probe_and_measure_eta(
                        env=env,
                        person_template=person_template,
                        start_road=start_road,
                        destination_road=destination_road,
                        departure_delay_steps=args.departure_delay_steps,
                        max_probe_steps=args.max_probe_steps,
                    )

                    row = {
                        "hospital_id": str(hospital_id),
                        "accident_id": str(accident_id),
                        "probe_index": probe_index,
                        "start_road": start_road,
                        "destination_road": destination_road,
                        **probe_result,
                    }
                    rows.append(row)

                    print(
                        f"hospital={hospital_id} accident={accident_id} "
                        f"probe={probe_index} eta={row['eta_seconds']} "
                        f"reason={row['arrival_reason']} trace_len={row['traced_road_count']}"
                    )

                    for _ in range(args.probe_stagger_steps):
                        await env.step_once()

            except Exception as exc:
                rows.append(
                    _error_row(hospital_id, accident_id, 0, start_road, destination_road, f"exception: {exc}")
                )
                print(f"hospital={hospital_id} accident={accident_id} failed: {exc}")
            finally:
                env.eng._stop_simulator()

    if not rows:
        print("No results produced")
        return

    total_rows = len(rows)
    eta_ok = sum(1 for row in rows if row.get("eta_seconds") is not None)
    timeout_no_eta = sum(1 for row in rows if row.get("arrival_reason") == "max_probe_steps_reached_no_eta")
    exception_rows = sum(1 for row in rows if str(row.get("arrival_reason", "")).startswith("exception:"))

    print(
        f"Summary: total={total_rows} eta_ok={eta_ok} "
        f"timeout_no_eta={timeout_no_eta} exceptions={exception_rows}"
    )

    write_results(log_dir, rows)

    if args.print_results_json:
        print("===RESULT_ROWS_JSON===")
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
