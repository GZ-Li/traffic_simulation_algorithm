#!/usr/bin/env bash

PYTHON_BIN="/home/nkk/work/src/python/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

$PYTHON_BIN /home/nkk/work/src/python/Single_Accident/Simulation_based_ETA_version/case1/get_od_eta_bad.py