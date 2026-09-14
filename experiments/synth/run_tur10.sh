#!/usr/bin/env bash
# Tur 10: noise-scaled exit geometry on the synthetic markets, baseline seeds.
set -uo pipefail
cd "$(dirname "$0")/../.."
W3="X_SL_FULL_ATR=6.75 X_TP1_ATR=9 X_TP2_ATR=18 X_TRAIL_ATR=11.25 X_TIMEOUT_BARS=288"
W4="X_SL_FULL_ATR=9 X_TP1_ATR=12 X_TP2_ATR=24 X_TRAIL_ATR=15 X_TIMEOUT_BARS=576"
env $W3 python3.12 experiments/synth/run.py --scenario trend --days 240 --seeds 6 --jobs 6 --tag wide3
env $W3 python3.12 experiments/synth/run.py --scenario null  --days 240 --seeds 6 --jobs 6 --tag wide3
env $W4 python3.12 experiments/synth/run.py --scenario trend --days 240 --seeds 6 --jobs 6 --tag wide4
echo "TUR10 DONE $(date -u +%FT%TZ)"
