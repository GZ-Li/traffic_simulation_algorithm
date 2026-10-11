#!/usr/bin/env bash

PYTHON_BIN="/home/nkk/work/src/python/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

# $PYTHON_BIN /home/nkk/work/src/python/Snowplowing/simulation_validation_ext.py \
# --mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
# --map_db tsingroc \
# --map_coll map_pku_wuhan_demo_1015c_rl \
# --agent_db tsingroc \
# --agent_coll person_test_20250724_1015cc \
# --interval 10 \
# --warmup_steps 5 \
# --eval_steps 18 \
# --speed_sample_every 5 \
# --snowed_speed 3 \
# --normal_speed 20 \
# --exp snow_extreme_baseline

$PYTHON_BIN /home/nkk/work/src/python/Snowplowing/simulation_validation_ext.py \
--mongo_uri mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017 \
--map_db tsingroc \
--map_coll map_pku_wuhan_demo_1015c_rl \
--agent_db tsingroc \
--agent_coll person_test_20250724_1015cc \
--interval 30 \
--warmup_steps 5 \
--eval_steps 60 \
--speed_sample_every 1700 \
--snowed_speed 3 \
--normal_speed 20 \
--exp snow_extreme_baseline
