#!/usr/bin/env bash
# Tur 10 open items: (1) six more seeds on null_mart × wide3, (2) random-entry
# controls — the exit geometry alone on a martingale (wide3 and baseline) and
# on the planted trend. Arms are env strings so the definition is the run.
set -uo pipefail
cd "$(dirname "$0")/../.."
W3="X_SL_FULL_ATR=6.75 X_TP1_ATR=9 X_TP2_ATR=18 X_TRAIL_ATR=11.25 X_TIMEOUT_BARS=288"
RUN="python3.12 experiments/synth/run.py"
# baseline × random entry × null_mart may already be running from the first launch
while pgrep -f "run.py --scenario null_mart .*--tag rand" >/dev/null; do sleep 30; done
[ -f experiments/synth/results/null_mart_240d_rand.json ] || $RUN --scenario null_mart --days 240 --seeds 6 --jobs 6 --random-entry 0.003 --tag rand
env $W3 $RUN --scenario null_mart --days 240 --seeds 6 --jobs 6 --seed0 7 --tag wide3b
env $W3 $RUN --scenario null_mart --days 240 --seeds 6 --jobs 6 --random-entry 0.003 --tag wide3_rand
env $W3 $RUN --scenario trend     --days 240 --seeds 6 --jobs 6 --random-entry 0.003 --tag wide3_rand
echo "TUR10C DONE $(date -u +%FT%TZ)"
