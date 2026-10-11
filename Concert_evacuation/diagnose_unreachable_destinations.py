#!/usr/bin/env python3
"""Diagnose unreachable destination roads by isolating vehicle injection.

This script runs one destination at a time with a small headway so that
any vehicle that never appears on the network can be identified clearly.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# default CSV path and config
SCRIPT_VERSION = "diagnose_unreachable_destinations_20260423"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DESTINATION_CSV = "/home/nkk/work/src/python/concert_evacuation/result/smart_od_trips.csv"
DEFAULT_ORIGIN_ROAD = "200002061"
DEFAULT_MAX_DESTINATIONS = 40
DEFAULT_HEADWAY_SECONDS = 1
DEFAULT_MAX_PROBE_STEPS = 1800
DEFAULT_QUERY_CONCURRENCY = 20
DEFAULT_TRACK_EVERY_STEPS = 10
DEFAULT_REPORT_JSON = str(SCRIPT_DIR / "diagnose_unreachable_destinations_report.json")

# make trafficlight_rl importable from the parent directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

@dataclass
class DiagnosticTask:
    od_index: int
    source_person_id: Optional[str]
    start_road: str
    end_road: str
    planned_departure_time: Optional[int]
    batch_index: int
    seat_index_in_batch: int


@dataclass
class DiagnosticState:
    od_index: int
    source_person_id: Optional[str]
    start_road: str
    end_road: str
    planned_departure_time: Optional[int]
    start_lane_id: Optional[int]
    end_lane_id: Optional[int]
    person_id: Optional[int]
    departure_time: Optional[int]
    started_time: Optional[int]
    arrived_time: Optional[int]
    eta_seconds: Optional[int]
    arrival_reason: str
    last_road: Optional[str]
    tracking_error_count: int
    last_tracking_error: Optional[str]


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
        from trafficlight_rl.engine import get_engine

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
            output_job_prefix="diagnose_unreachable_destinations_",
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
    import pymongo

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


def first_existing_value(row: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value) != "":
            return value
    return None


def load_destination_rows_from_csv(destination_csv: str, max_destinations: Optional[int]) -> List[Dict[str, Any]]:
    csv_path = Path(destination_csv).expanduser().resolve()
    if not csv_path.exists():
        raise RuntimeError(f"Destination CSV not found: {csv_path}")
    if max_destinations is not None and max_destinations <= 0:
        raise RuntimeError("max_destinations must be positive")
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"Destination CSV has no header: {csv_path}")
        rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            if max_destinations is not None and len(rows) >= max_destinations:
                break
            end_road = first_existing_value(row, ["D_Road_ID", "destination_road", "end_road", "d", "home_road", "road_id"])
            if end_road is None:
                raise RuntimeError(
                    f"Destination CSV row #{idx} must contain one of: D_Road_ID / destination_road / end_road / d / home_road / road_id"
                )
            rows.append(
                {
                    "od_index": idx,
                    "source_person_id": first_existing_value(row, ["Person_ID", "person_id", "personId", "user_id", "id"]),
                    "end_road": str(end_road),
                }
            )
    if not rows:
        raise RuntimeError(f"Destination CSV contains no rows: {csv_path}")
    return rows


async def resolve_lane_with_cache(env: Env, road_id: str, lane_cache: Dict[str, int]) -> int:
    road_id = str(road_id)
    if road_id in lane_cache:
        return lane_cache[road_id]
    lanes = await env.eng.get_lane_id_of_road(road_id)
    if not lanes:
        raise RuntimeError(f"Cannot resolve lane ids for road: {road_id}")
    lane_id = int(lanes[0])
    lane_cache[road_id] = lane_id
    return lane_id


def build_probe_person_auto_route(template: Dict[str, Any], start_lane_id: int, end_lane_id: int, departure_time: int) -> Dict[str, Any]:
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
        description="Diagnose unreachable destinations by injecting one vehicle at a time."
    )
    parser.add_argument("--mongo_uri", default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017")
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")
    parser.add_argument("--destination_csv", default=DEFAULT_DESTINATION_CSV)
    parser.add_argument("--origin_road", default=DEFAULT_ORIGIN_ROAD)
    parser.add_argument("--max_destinations", type=int, default=DEFAULT_MAX_DESTINATIONS)
    parser.add_argument("--headway_seconds", type=int, default=DEFAULT_HEADWAY_SECONDS)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--max_probe_steps", type=int, default=DEFAULT_MAX_PROBE_STEPS)
    parser.add_argument("--query_concurrency", type=int, default=DEFAULT_QUERY_CONCURRENCY)
    parser.add_argument("--track_every_steps", type=int, default=DEFAULT_TRACK_EVERY_STEPS)
    parser.add_argument("--report_json", default=DEFAULT_REPORT_JSON)
    parser.add_argument("--rows_print_limit", type=int, default=20)
    return parser.parse_args()


async def inject_one_probe(
    env: Env,
    person_template: Dict[str, Any],
    task: DiagnosticTask,
    lane_cache: Dict[str, int],
) -> DiagnosticState:
    state = DiagnosticState(
        od_index=task.od_index,
        source_person_id=task.source_person_id,
        start_road=task.start_road,
        end_road=task.end_road,
        planned_departure_time=task.planned_departure_time,
        start_lane_id=None,
        end_lane_id=None,
        person_id=None,
        departure_time=None,
        started_time=None,
        arrived_time=None,
        eta_seconds=None,
        arrival_reason="not_injected",
        last_road=None,
        tracking_error_count=0,
        last_tracking_error=None,
    )
    try:
        start_lane_id = await resolve_lane_with_cache(env, state.start_road, lane_cache)
        end_lane_id = await resolve_lane_with_cache(env, state.end_road, lane_cache)
        state.start_lane_id = start_lane_id
        state.end_lane_id = end_lane_id
        payload = build_probe_person_auto_route(
            template=person_template,
            start_lane_id=start_lane_id,
            end_lane_id=end_lane_id,
            departure_time=int(task.planned_departure_time or 0),
        )
        add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
        person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
        if not isinstance(person_id, int):
            raise RuntimeError(f"AddPerson failed: {add_resp}")
        state.person_id = person_id
        state.departure_time = int(task.planned_departure_time or 0)
        state.arrival_reason = "not_arrived"
    except Exception as exc:
        state.arrival_reason = f"inject_exception: {exc}"
    return state


async def run_diagnosis(
    env: Env,
    person_template: Dict[str, Any],
    tasks: List[DiagnosticTask],
    max_probe_steps: int,
    query_concurrency: int,
    track_every_steps: int,
) -> Tuple[List[DiagnosticState], Dict[str, Any]]:
    if query_concurrency <= 0:
        raise RuntimeError("--query_concurrency must be greater than 0")
    if track_every_steps <= 0:
        raise RuntimeError("--track_every_steps must be greater than 0")

    states: List[DiagnosticState] = []
    lane_cache: Dict[str, int] = {}
    active_by_person_id: Dict[int, DiagnosticState] = {}
    semaphore = asyncio.Semaphore(query_concurrency)

    async def query_current_road(state: DiagnosticState) -> Tuple[DiagnosticState, Optional[str], Optional[str]]:
        try:
            async with semaphore:
                current_road = await env.eng.get_road_id_with_vehicle(state.person_id)
            current_road_str = str(current_road) if current_road is not None else None
            return state, current_road_str, None
        except Exception as exc:
            return state, None, str(exc)

    diagnostics: Dict[str, Any] = {
        "tasks_total": len(tasks),
        "injected_ok": 0,
        "inject_failures": 0,
        "never_started": 0,
        "reached_destination": 0,
        "timed_out": 0,
        "tracking_ticks_executed": 0,
        "vehicle_query_count": 0,
        "max_probe_steps": max_probe_steps,
        "track_every_steps": track_every_steps,
    }

    # inject one destination at a time, with a short headway to avoid start collisions
    for task in tasks:
        state = await inject_one_probe(env, person_template, task, lane_cache)
        states.append(state)
        if state.person_id is not None:
            diagnostics["injected_ok"] += 1
            active_by_person_id[state.person_id] = state
        else:
            diagnostics["inject_failures"] += 1
            state.arrival_reason = state.arrival_reason or "inject_exception"
            continue

        for step_index in range(max_probe_steps):
            sim_clock = await env.eng.get_current_time()
            now_t = int(sim_clock["t"])

            # query vehicle state every track_every_steps or on last step
            should_query = (step_index % track_every_steps == 0) or (step_index == max_probe_steps - 1)
            if should_query and state.person_id in active_by_person_id:
                diagnostics["tracking_ticks_executed"] += 1
                diagnostics["vehicle_query_count"] += 1
                _, current_road_str, query_error = await query_current_road(state)
                if query_error is not None:
                    state.tracking_error_count += 1
                    state.last_tracking_error = query_error
                else:
                    if state.started_time is None and current_road_str is not None:
                        state.started_time = now_t
                    if current_road_str == state.end_road and state.started_time is not None:
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "reach_destination_road"
                        active_by_person_id.pop(state.person_id, None)
                        diagnostics["reached_destination"] += 1
                    elif state.started_time is not None and current_road_str is None and state.last_road is not None:
                        state.arrived_time = now_t
                        state.eta_seconds = max(0, state.arrived_time - state.started_time)
                        state.arrival_reason = "disappeared_after_departure"
                        active_by_person_id.pop(state.person_id, None)
                    state.last_road = current_road_str

            if state.person_id not in active_by_person_id:
                break

            await env.step_once()

        if state.person_id in active_by_person_id:
            state.arrival_reason = "timed_out_no_start" if state.started_time is None else "timed_out_no_arrival"
            state.arrived_time = None
            state.eta_seconds = None
            active_by_person_id.pop(state.person_id, None)
            diagnostics["timed_out"] += 1

    return states, diagnostics


def state_to_row(state: DiagnosticState) -> Dict[str, Any]:
    return {
        "od_index": state.od_index,
        "source_person_id": state.source_person_id,
        "start_road": state.start_road,
        "end_road": state.end_road,
        "planned_departure_time": state.planned_departure_time,
        "start_lane_id": state.start_lane_id,
        "end_lane_id": state.end_lane_id,
        "person_id": state.person_id,
        "departure_time": state.departure_time,
        "started_time": state.started_time,
        "arrived_time": state.arrived_time,
        "eta_seconds": state.eta_seconds,
        "arrival_reason": state.arrival_reason,
        "last_road": state.last_road,
        "tracking_error_count": state.tracking_error_count,
        "last_tracking_error": state.last_tracking_error,
    }


def write_report_json(report_json: Optional[str], rows: List[Dict[str, Any]], summary: Dict[str, Any], diagnostics: Dict[str, Any]) -> None:
    if not report_json:
        return
    output_path = Path(report_json).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "script_version": SCRIPT_VERSION,
        "summary": summary,
        "runtime_diagnostics": diagnostics,
        "rows": rows,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved to: {output_path}")


def summarize_and_print(rows: List[DiagnosticState], diagnostics: Dict[str, Any], report_json: Optional[str], rows_print_limit: int) -> None:
    summary = {
        "total_tasks": len(rows),
        "injected_ok": diagnostics["injected_ok"],
        "inject_failures": diagnostics["inject_failures"],
        "reached_destination": diagnostics["reached_destination"],
        "timed_out": diagnostics["timed_out"],
        "never_started_count": sum(1 for s in rows if s.arrival_reason == "timed_out_no_start"),
        "started_but_not_arrived_count": sum(1 for s in rows if s.arrival_reason == "timed_out_no_arrival"),
    }

    print("=== Diagnose Unreachable Destinations Summary ===")
    print(f"total_tasks:              {summary['total_tasks']}")
    print(f"injected_ok:              {summary['injected_ok']}")
    print(f"inject_failures:          {summary['inject_failures']}")
    print(f"reached_destination:      {summary['reached_destination']}")
    print(f"timed_out:                {summary['timed_out']}")
    print(f"never_started_count:      {summary['never_started_count']}")
    print(f"started_but_not_arrived:  {summary['started_but_not_arrived_count']}")
    print("===RUNTIME_DIAGNOSTICS_JSON===")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    rows_json = [state_to_row(s) for s in rows]
    if rows_print_limit > 0:
        print(json.dumps(rows_json[:rows_print_limit], ensure_ascii=False, indent=2))
        if len(rows_json) > rows_print_limit:
            print(f"... {len(rows_json) - rows_print_limit} more rows saved only in report_json")
    else:
        print(f"{len(rows_json)} rows saved only in report_json")
    write_report_json(report_json, rows_json, summary, diagnostics)


async def amain(args: argparse.Namespace) -> int:
    tasks: List[DiagnosticTask] = []
    destination_rows = load_destination_rows_from_csv(args.destination_csv, args.max_destinations)
    for idx, row in enumerate(destination_rows, start=0):
        tasks.append(
            DiagnosticTask(
                od_index=int(row["od_index"]),
                source_person_id=(str(row["source_person_id"]) if row.get("source_person_id") is not None else None),
                start_road=args.origin_road,
                end_road=row["end_road"],
                planned_departure_time=None,
                batch_index=idx,
                seat_index_in_batch=1,
            )
        )

    env = Env(
        mongo_uri=args.mongo_uri,
        map_db=args.map_db,
        map_coll=args.map_coll,
        agent_db=args.agent_db,
        agent_coll=args.agent_coll,
        start_step=0,
        step_size=args.interval,
        step_count=args.warmup_steps + args.departure_delay_steps + args.max_probe_steps + 60,
    )

    fatal_error: Optional[str] = None
    states: List[DiagnosticState] = []
    diagnostics: Dict[str, Any] = {}

    try:
        person_template = load_person_template_from_mongo(args.mongo_uri, args.agent_db, args.agent_coll)
        for _ in range(args.warmup_steps):
            await env.step_once()
        now = await env.eng.get_current_time()
        first_departure_time = int(now["t"]) + args.departure_delay_steps * env.step_size
        for task in tasks:
            task.planned_departure_time = first_departure_time + task.batch_index * args.headway_seconds
        states, diagnostics = await run_diagnosis(
            env=env,
            person_template=person_template,
            tasks=tasks,
            max_probe_steps=args.max_probe_steps,
            query_concurrency=args.query_concurrency,
            track_every_steps=args.track_every_steps,
        )
    except Exception as exc:
        fatal_error = str(exc)
    finally:
        summarize_and_print(states, diagnostics, args.report_json, args.rows_print_limit)
        try:
            env.eng._stop_simulator()
        except Exception as stop_exc:
            print(f"WARNING: simulator stop failed and was ignored: {stop_exc}")

    return 1 if fatal_error else 0


def main() -> int:
    args = parse_args()
    start_time = time.time()
    exit_code = asyncio.run(amain(args))
    end_time = time.time()
    print(f"Total diagnosis execution time: {end_time - start_time:.2f} seconds")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
