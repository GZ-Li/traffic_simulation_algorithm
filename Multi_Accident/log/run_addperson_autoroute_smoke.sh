#!/usr/bin/env bash
set -euo pipefail

# Minimal smoke test for AddPerson + auto routing (routes=[]).
# You can override each var before running, e.g.:
# MONGO_URI=... MAP_DB=... MAP_COLL=... AGENT_DB=... AGENT_COLL=... bash run_addperson_autoroute_smoke.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MONGO_URI="${MONGO_URI:-mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017}"
MAP_DB="${MAP_DB:-tsingroc}"
MAP_COLL="${MAP_COLL:-map_pku_wuhan_demo_1015c_rl}"
AGENT_DB="${AGENT_DB:-tsingroc}"
AGENT_COLL="${AGENT_COLL:-person_test_20250724_1015cc}"

python3 "${SCRIPT_DIR}/get_eta_addperson_autoroute.py" \
  --mongo_uri "${MONGO_URI}" \
  --map_db "${MAP_DB}" \
  --map_coll "${MAP_COLL}" \
  --agent_db "${AGENT_DB}" \
  --agent_coll "${AGENT_COLL}" \
  --paths_file "${SCRIPT_DIR}/shortest_paths_minimal_addperson_test.json" \
  --interval 30 \
  --warmup_steps 10 \
  --departure_delay_steps 1 \
  --max_probe_steps 300 \
  --probes_per_od 1 \
  --probe_stagger_steps 1 \
  --exp addperson_autoroute_smoke

