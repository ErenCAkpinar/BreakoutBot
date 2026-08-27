#!/usr/bin/env bash
# Run ONE arm on ONE window and record it in the ledger.
#
#   ./experiments/run_arm.sh <arm-id> <days> [ENV=VAL ...]
#
# Every arm runs on cached data (backtests/data/*_${days}d+40w.pkl), so a window
# is byte-identical across arms and a metric difference is the CODE/PARAM change
# only. BT_RUN_TAG keeps parallel arms on the same window from overwriting each
# other's dump.
#
# Results land in experiments/results/<arm-id>_<days>d.json and are summarised by
# experiments/ledger.py.
set -euo pipefail

ARM="${1:?arm id required}"; DAYS="${2:?days required}"; shift 2

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p experiments/results experiments/logs

TAG="${ARM}"
LOG="experiments/logs/${ARM}_${DAYS}d.log"

# Arms are declared as ENV=VAL pairs on the command line so the arm definition
# and the run are the same string — nothing to keep in sync by hand.
env "$@" BT_RUN_TAG="$TAG" \
    python3.12 backtest.py --days "$DAYS" --cache > "$LOG" 2>&1

SRC="backtests/data/last_run_${DAYS}d_${TAG}.json"
DST="experiments/results/${ARM}_${DAYS}d.json"
if [ ! -f "$SRC" ]; then
  echo "FAIL ${ARM} ${DAYS}d — no dump produced; see $LOG" >&2
  exit 1
fi
mv "$SRC" "$DST"

# Stamp the arm definition into the result so a number can never be separated
# from the parameters that produced it.
python3.12 - "$DST" "$ARM" "$DAYS" "$@" <<'PY'
import json, sys
path, arm, days = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path))
d["_arm"] = {"id": arm, "days": int(days), "env": sys.argv[4:]}
json.dump(d, open(path, "w"))
PY

echo "OK ${ARM} ${DAYS}d → ${DST}"
