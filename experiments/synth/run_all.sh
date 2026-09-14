#!/usr/bin/env bash
# The Tur 9 synthetic-market study, in the order the questions matter. This is
# the sequence that produced experiments/synth/results/ (see DEFTER.md Tur 9).
#
#   1. null / trend / chop       (240d, live parity)  — bug vs no-edge diagnostics
#   2. bootstrap in-sample + OOS (240d)               — is the 240d edge in the days
#                                                       or in the geometry's fit to them?
#   3. 2027…2030 regime years    (365d, restarts)     — scenario Monte Carlo, two
#                                                       years at a time (3 seeds each)
#   4. trend_slow                (240d)               — multi-day momentum only
#
# One 240d seed is ~40–55 min on this Mac with 6 in parallel (4 of the 8 cores
# are efficiency cores); a 365d seed ~80–90 min; the whole pass ~9 h. Results are
# checkpointed after every seed, so report.py can be read at any point.
set -uo pipefail
cd "$(dirname "$0")/../.."
J="${JOBS:-6}"
run() { python3.12 experiments/synth/run.py "$@"; }
run --scenario null       --days 240 --seeds 6 --jobs "$J"
run --scenario trend      --days 240 --seeds 6 --jobs "$J"
run --scenario chop       --days 240 --seeds 6 --jobs "$J"
run --scenario bootstrap  --days 240 --seeds 6 --jobs "$J"
run --scenario bootstrap  --days 240 --seeds 6 --jobs "$J" --source-days 665 --before 2025-12-29 --tag oos
for pair in "2027 2028" "2029 2030"; do
  for y in $pair; do
    run --scenario "$y" --days 365 --seeds 3 --restarts --jobs $((J / 2)) &
  done
  wait
done
run --scenario trend_slow --days 240 --seeds 6 --jobs "$J"
echo "ALL DONE $(date -u +%FT%TZ)"
