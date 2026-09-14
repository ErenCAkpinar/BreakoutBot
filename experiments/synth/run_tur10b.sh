#!/usr/bin/env bash
# Tur 10 follow-up: the price-martingale null under both geometries, after the
# running wide4 batch. Same seeds 1–6.
set -uo pipefail
cd "$(dirname "$0")/../.."
W3="X_SL_FULL_ATR=6.75 X_TP1_ATR=9 X_TP2_ATR=18 X_TRAIL_ATR=11.25 X_TIMEOUT_BARS=288"
while pgrep -f "run.py --scenario trend .*wide4" >/dev/null; do sleep 30; done
python3.12 experiments/synth/run.py --scenario null_mart --days 240 --seeds 6 --jobs 6
env $W3 python3.12 experiments/synth/run.py --scenario null_mart --days 240 --seeds 6 --jobs 6 --tag wide3
echo "TUR10B DONE $(date -u +%FT%TZ)"
