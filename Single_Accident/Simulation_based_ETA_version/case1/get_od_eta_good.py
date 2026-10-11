#!/usr/bin/env python3
"""Direct OD smoke test for AddPerson + auto routing (no JSON input).

This script is intentionally minimal and educational:
1. Connect simulator engine.
2. Load one person template from Mongo.
3. Inject one vehicle-like person using AddPerson with routes=[].
4. Poll vehicle road position and compute ETA.

Use this to quickly verify whether "AddPerson + auto route" can run end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import pymongo

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


# Minimal built-in OD for direct smoke test.
# Update these two road IDs directly when you want to test another OD pair.

DEFAULT_START_ROAD = "200000033"
DEFAULT_END_ROAD = "200007001"


@dataclass
class ProbeResult:
    person_id: int
    departure_time: int
    started_time: Optional[int]
    arrived_time: Optional[int]
    eta_seconds: Optional[int]
    arrival_reason: str


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
    ) -> None:
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
            output_job_prefix="addperson_direct_od_smoke_",
            output_bbox=None,
        )
        self.step_size = step_size

    async def step_once(self) -> None:
        self.eng.next_step(self.step_size)


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
        raise RuntimeError(f"Cannot resolve lane ids for OD: {start_road} -> {end_road}")
    return int(start_lanes[0]), int(end_lanes[0])


def build_probe_person_auto_route(
    template: Dict[str, Any],
    start_lane_id: int,
    end_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    person = copy.deepcopy(template)
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
                    # routes=[] means simulator plans the route automatically.
                    "routes": [],
                }
            ],
        }
    ]
    return person


async def run_probe_and_measure_eta(
    env: Env,
    person_template: Dict[str, Any],
    start_road: str,
    end_road: str,
    departure_delay_steps: int,
    max_probe_steps: int,
) -> ProbeResult:
    start_lane_id, end_lane_id = await resolve_start_end_lanes(env, start_road, end_road)

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
    arrival_reason = "not_arrived"
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

        if started and current_road_str == end_road:
            arrived_time = now_t
            arrival_reason = "reach_destination_road"
            break

        if started and current_road_str is None and last_road is not None:
            arrived_time = now_t
            arrival_reason = "disappeared_after_departure"
            break

        last_road = current_road_str

    eta_seconds: Optional[int] = None
    if started_time is not None and arrived_time is not None:
        eta_seconds = max(0, arrived_time - started_time)

    return ProbeResult(
        person_id=person_id,
        departure_time=departure_time,
        started_time=started_time,
        arrived_time=arrived_time,
        eta_seconds=eta_seconds,
        arrival_reason=arrival_reason,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test AddPerson auto-routing with one direct OD (no JSON)."
    )
    parser.add_argument("--mongo_uri", default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017")
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")

    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument("--max_probe_steps", type=int, default=300)
    parser.add_argument(
        "--result_file",
        default="",
        help="Optional path to save printed result. If empty, auto-generate a timestamped txt file.",
    )
    return parser.parse_args()


def format_result_lines(result: ProbeResult) -> list[str]:
    return [
        "=== AddPerson direct-OD smoke test result ===",
        f"start_road:    {DEFAULT_START_ROAD}",
        f"end_road:      {DEFAULT_END_ROAD}",
        f"person_id:     {result.person_id}",
        f"departure_time:{result.departure_time}",
        f"started_time:  {result.started_time}",
        f"arrived_time:  {result.arrived_time}",
        f"arrival_reason:{result.arrival_reason}",
        f"eta_seconds:   {result.eta_seconds}",
    ]


def persist_result_text(text: str, result_file: str) -> str:
    if result_file.strip():
        output_path = result_file
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"addperson_direct_od_result_{ts}.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    return output_path


async def amain(args: argparse.Namespace) -> int:
    step_count = args.warmup_steps + args.departure_delay_steps + args.max_probe_steps + 30
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
        person_template = load_person_template_from_mongo(
            args.mongo_uri, args.agent_db, args.agent_coll
        )

        for _ in range(args.warmup_steps):
            await env.step_once()

        result = await run_probe_and_measure_eta(
            env=env,
            person_template=person_template,
            start_road=DEFAULT_START_ROAD,
            end_road=DEFAULT_END_ROAD,
            departure_delay_steps=args.departure_delay_steps,
            max_probe_steps=args.max_probe_steps,
        )

        lines = format_result_lines(result)
        result_text = "\n".join(lines)
        print(result_text)
        saved_path = persist_result_text(result_text, args.result_file)
        print(f"result_file:   {saved_path}")

        if result.eta_seconds is None:
            print("[WARN] Vehicle was added, but ETA not confirmed within max_probe_steps.")
            return 1

        print("[OK] AddPerson succeeded and ETA was measured.")
        return 0

    finally:
        env.eng._stop_simulator()


def main() -> int:
    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
