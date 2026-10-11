#!/usr/bin/env python3
"""Multi-OD smoke test: inject multiple AddPerson vehicles in one simulation.

What this script does:
1. Connect simulator engine once.
2. Inject one probe vehicle per OD pair (routes=[] => auto routing).
3. Step simulation once per tick and track all probes in parallel.
4. Output ETA results for all OD pairs as JSON.

This is faster than running one-OD scripts repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

with open('/home/nkk/work/src/python/Single_Accident/Simulation_based_ETA_version/case1/location_input.json', 'r', encoding='utf-8') as f:
    location_input = json.load(f)
    
hospitals = location_input['hospitals']
accident = location_input['accident']

od_lst = []
for h in hospitals:
    od_lst.append((h, accident))

# Built-in OD list for quick test. Edit this list directly as needed.
DEFAULT_OD_PAIRS: List[Tuple[str, str]] = od_lst
    # [("200000480", "200000160"),
    # ("200000154", "200000160"),
    # ("200000033", "200000160"),
    # ("200000480", "200000549"),
    # ("200000154", "200000549"),
    # ("200000033", "200000549"),
    # ("200000480", "200000387"),
    # ("200000154", "200000387"),
    # ("200000033", "200000387"),
    # ("200000480", "200000601"),
    # ("200000154", "200000601"),
    # ("200000033", "200000601"),
    # ("200000480", "200007001"),
    # ("200000154", "200007001"),
    # ("200000033", "200007001"),
    # ("200000480", "200000823"),
    # ("200000154", "200000823"),
    # ("200000033", "200000823"),
    # ("200000480", "200000869"),
    # ("200000154", "200000869"),
    # ("200000033", "200000869"),
    # ("200000480", "200000575"),
    # ("200000154", "200000575"),
    # ("200000033", "200000575"),
    # ("200000480", "200000567"),
    # ("200000154", "200000567"),
    # ("200000033", "200000567"),
    # ("200000480", "200000396"),
    # ("200000154", "200000396"),
    # ("200000033", "200000396"),
    # # ============================
    # ("200000160", "200000480"),
    # ("200000160", "200000154"),
    # ("200000160", "200000033"),
    # ("200000549", "200000480"),
    # ("200000549", "200000154"),
    # ("200000549", "200000033"),
    # ("200000387", "200000480"),
    # ("200000387", "200000154"),
    # ("200000387", "200000033"),
    # ("200000601", "200000480"),
    # ("200000601", "200000154"),
    # ("200000601", "200000033"),
    # ("200007001", "200000480"),
    # ("200007001", "200000154"),
    # ("200007001", "200000033"),
    # ("200000823", "200000480"),
    # ("200000823", "200000154"),
    # ("200000823", "200000033"),
    # ("200000869", "200000480"),
    # ("200000869", "200000154"),
    # ("200000869", "200000033"),
    # ("200000575", "200000480"),
    # ("200000575", "200000154"),
    # ("200000575", "200000033"),
    # ("200000567", "200000480"),
    # ("200000567", "200000154"),
    # ("200000567", "200000033"),
    # ("200000396", "200000480"),
    # ("200000396", "200000154"),
    # ("200000396", "200000033"),
    
    
    # ============================
    # ("200000480", "200000387"),
    # ("200000387", "200000480"),
    # ("200000480", "200007001"),
    # ("200007001", "200000480"),
    # ("200000480", "200000869"),
    # ("200000869", "200000480"),
    
    # ("200000154", "200000387"),
    # ("200000387", "200000154"),
    # ("200000154", "200007001"),
    # ("200007001", "200000154"),
    # ("200000154", "200000869"),
    # ("200000869", "200000154"),
    
    # ("200000033", "200000387"),
    # ("200000387", "200000033"),
    # ("200000033", "200007001"),
    # ("200007001", "200000033"),
    # ("200000033", "200000869"),
    # ("200000869", "200000033"),]


@dataclass
class ProbeState:
    od_index: int
    start_road: str
    end_road: str
    person_id: Optional[int]
    departure_time: Optional[int]
    started_time: Optional[int]
    arrived_time: Optional[int]
    eta_seconds: Optional[int]
    arrival_reason: str
    last_road: Optional[str]


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
            output_job_prefix="addperson_multi_od_smoke_",
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
                    "routes": [],
                }
            ],
        }
    ]
    return person


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject multiple OD probes in one simulation and collect ETA together."
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
    parser.add_argument(
        "--departure_stagger_steps",
        type=int,
        default=0,
        help="Additional delay between consecutive OD probes (0 means all depart together)",
    )
    parser.add_argument("--max_probe_steps", type=int, default=60)
    return parser.parse_args()


async def inject_all_probes(
    env: Env,
    person_template: Dict[str, Any],
    od_pairs: List[Tuple[str, str]],
    departure_delay_steps: int,
    departure_stagger_steps: int,
) -> List[ProbeState]:
    now = await env.eng.get_current_time()
    base_departure = int(now["t"]) + departure_delay_steps * env.step_size

    states: List[ProbeState] = []
    for idx, (start_road, end_road) in enumerate(od_pairs, start=1):
        state = ProbeState(
            od_index=idx,
            start_road=str(start_road),
            end_road=str(end_road),
            person_id=None,
            departure_time=None,
            started_time=None,
            arrived_time=None,
            eta_seconds=None,
            arrival_reason="not_injected",
            last_road=None,
        )

        try:
            start_lane_id, end_lane_id = await resolve_start_end_lanes(env, state.start_road, state.end_road)
            departure_time = base_departure + (idx - 1) * departure_stagger_steps * env.step_size
            payload = build_probe_person_auto_route(
                template=person_template,
                start_lane_id=start_lane_id,
                end_lane_id=end_lane_id,
                departure_time=departure_time,
            )

            add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
            person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
            if not isinstance(person_id, int):
                raise RuntimeError(f"AddPerson failed: {add_resp}")

            state.person_id = person_id
            state.departure_time = departure_time
            state.arrival_reason = "not_arrived"
        except Exception as exc:
            state.arrival_reason = f"inject_exception: {exc}"

        states.append(state)

    return states


async def track_all_probes(
    env: Env,
    states: List[ProbeState],
    max_probe_steps: int,
) -> None:
    active_ids = {s.person_id for s in states if isinstance(s.person_id, int)}
    if not active_ids:
        return

    for _ in range(max_probe_steps):
        await env.step_once()
        sim_clock = await env.eng.get_current_time()
        now_t = int(sim_clock["t"])

        for state in states:
            if state.person_id not in active_ids:
                continue

            current_road = await env.eng.get_road_id_with_vehicle(state.person_id)
            current_road_str = str(current_road) if current_road is not None else None

            if state.started_time is None and current_road_str is not None:
                state.started_time = now_t

            if state.started_time is not None and current_road_str == state.end_road:
                state.arrived_time = now_t
                state.arrival_reason = "reach_destination_road"
                active_ids.discard(state.person_id)
                continue

            if state.started_time is not None and current_road_str is None and state.last_road is not None:
                state.arrived_time = now_t
                state.arrival_reason = "disappeared_after_departure"
                active_ids.discard(state.person_id)
                continue

            state.last_road = current_road_str

        if not active_ids:
            break

    for state in states:
        if isinstance(state.person_id, int) and state.arrived_time is None and state.arrival_reason == "not_arrived":
            state.arrival_reason = "max_probe_steps_reached_no_eta"

    for state in states:
        if state.started_time is not None and state.arrived_time is not None:
            state.eta_seconds = max(0, state.arrived_time - state.started_time)


def summarize_and_print(states: List[ProbeState], fatal_error: Optional[str] = None) -> None:
    total = len(states)
    injected = sum(1 for s in states if isinstance(s.person_id, int))
    eta_ok = sum(1 for s in states if s.eta_seconds is not None)
    timeout_no_eta = sum(1 for s in states if s.arrival_reason == "max_probe_steps_reached_no_eta")
    inject_fail = sum(1 for s in states if s.arrival_reason.startswith("inject_exception:"))

    print("=== Multi-OD AddPerson Result Summary ===")
    print(f"total_ods:        {total}")
    print(f"injected_ok:      {injected}")
    print(f"eta_available:    {eta_ok}")
    print(f"timeout_no_eta:   {timeout_no_eta}")
    print(f"inject_failures:  {inject_fail}")
    if fatal_error is not None:
        print(f"fatal_error:      {fatal_error}")

    rows: List[Dict[str, Any]] = []
    for s in states:
        rows.append(
            {
                "od_index": s.od_index,
                "start_road": s.start_road,
                "end_road": s.end_road,
                "person_id": s.person_id,
                "departure_time": s.departure_time,
                "started_time": s.started_time,
                "arrived_time": s.arrived_time,
                "eta_seconds": s.eta_seconds,
                "arrival_reason": s.arrival_reason,
            }
        )

    print("===RESULT_ROWS_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    with open("/home/nkk/work/src/python/Single_Accident/Simulation_based_ETA_version/eta_res.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"Results saved to: /home/nkk/work/src/python/Single_Accident/Simulation_based_ETA_version/eta_res.json")


async def amain(args: argparse.Namespace) -> int:
    step_count = args.warmup_steps + args.departure_delay_steps + args.max_probe_steps + 60
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

    states: List[ProbeState] = []
    fatal_error: Optional[str] = None
    exit_code = 0

    try:
        person_template = load_person_template_from_mongo(
            args.mongo_uri, args.agent_db, args.agent_coll
        )

        for _ in range(args.warmup_steps):
            await env.step_once()

        states = await inject_all_probes(
            env=env,
            person_template=person_template,
            od_pairs=DEFAULT_OD_PAIRS,
            departure_delay_steps=args.departure_delay_steps,
            departure_stagger_steps=args.departure_stagger_steps,
        )

        await track_all_probes(env=env, states=states, max_probe_steps=args.max_probe_steps)
    except Exception as exc:
        fatal_error = str(exc)
        exit_code = 1
    finally:
        summarize_and_print(states, fatal_error=fatal_error)
        env.eng._stop_simulator()

    return exit_code


def main() -> int:
    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
