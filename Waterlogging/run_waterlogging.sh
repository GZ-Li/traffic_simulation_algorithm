#!/usr/bin/env bash

PYTHON_BIN="/home/nkk/work/src/python/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

$PYTHON_BIN /home/nkk/work/src/python/Waterlogging/simulation_validation.py \
  --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
  --map_db tsingroc \
  --map_coll map_pku_wuhan_demo_1015c_rl \
  --agent_db tsingroc \
  --agent_coll person_test_20250724_1015cc \
  --interval 10 \
  --warmup_steps 0 \
  --eval_steps 30 \
  --max_clean_at_once 2 \
  --order best \
  --exp best

$PYTHON_BIN /home/nkk/work/src/python/Waterlogging/simulation_validation.py \
  --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
  --map_db tsingroc \
  --map_coll map_pku_wuhan_demo_1015c_rl \
  --agent_db tsingroc \
  --agent_coll person_test_20250724_1015cc \
  --interval 10 \
  --warmup_steps 0 \
  --eval_steps 30 \
  --max_clean_at_once 2 \
  --order worst \
  --exp random