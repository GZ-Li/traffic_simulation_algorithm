#!/usr/bin/env python3
"""Minimal smoke test for AddPerson + auto routing in Multi_Accident.

This script does two things:
1. Writes a minimal OD file (if missing): shortest_paths_minimal_addperson_test.json
2. Runs get_eta_addperson_autoroute.py with very small settings to quickly validate
   whether AddPerson + routes=[] is workable in the simulator.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def build_minimal_paths() -> dict:
    # One hospital-accident pair and one candidate path.
    # Only start/end roads are used by get_eta_addperson_autoroute.py.
    return {
        "200002126": {
            "200002141": [
                {
                    "rank": 1,
                    "path": ["200002126", "200002141"],
                    "num_edges": 1,
                }
            ]
        }
    }


def ensure_paths_file(paths_file: Path) -> None:
    if paths_file.exists():
        return
    data = build_minimal_paths()
    paths_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[smoke-test] created minimal paths file: {paths_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a minimal AddPerson auto-route smoke test."
    )
    parser.add_argument(
        "--mongo_uri",
        default="mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017",
    )
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--warmup_steps", type=int, default=10)
    parser.add_argument("--departure_delay_steps", type=int, default=1)
    parser.add_argument("--max_probe_steps", type=int, default=300)
    parser.add_argument("--exp", default="addperson_autoroute_smoke")
    parser.add_argument(
        "--paths_file",
        default=None,
        help="Optional custom paths json. If omitted, use/create minimal test file.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Only print command, do not execute simulator.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    target_script = script_dir / "get_eta_addperson_autoroute.py"
    if not target_script.exists():
        print(f"[smoke-test] target script not found: {target_script}")
        return 2

    if args.paths_file:
        paths_file = Path(args.paths_file).resolve()
    else:
        paths_file = script_dir / "shortest_paths_minimal_addperson_test.json"
        ensure_paths_file(paths_file)

    cmd = [
        sys.executable,
        str(target_script),
        "--mongo_uri",
        args.mongo_uri,
        "--map_db",
        args.map_db,
        "--map_coll",
        args.map_coll,
        "--agent_db",
        args.agent_db,
        "--agent_coll",
        args.agent_coll,
        "--paths_file",
        str(paths_file),
        "--interval",
        str(args.interval),
        "--warmup_steps",
        str(args.warmup_steps),
        "--departure_delay_steps",
        str(args.departure_delay_steps),
        "--max_probe_steps",
        str(args.max_probe_steps),
        "--probes_per_od",
        "1",
        "--probe_stagger_steps",
        "1",
        "--exp",
        args.exp,
    ]

    print("[smoke-test] running command:")
    print(" ".join(cmd))

    if args.dry_run:
        return 0

    env = os.environ.copy()
    result = subprocess.run(cmd, env=env)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
