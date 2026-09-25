"""One pass: find unreviewed FULL OPENs, review each once, append to decisions.jsonl.

Run by `breakoutbot-ai-shadow.timer` every 5 minutes, ~25 s after the bar the
bot has just written. Idempotent: a signal is reviewed at most once, whatever
the outcome, so a crash or a restart never double-bills a candidate.

    python -m ai_shadow.worker --state … --out-dir … --since 2026-09-25T12:00:00Z
    python -m ai_shadow.worker --out-dir … --summary
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Callable

from .candidates import Candidate, full_opens, iso_utc, ts_ms
from .llm import Outcome, decide
from .market import FetchJSON, LookaheadError, http_json
from .snapshot import build_input

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "prompt_v1.md"
SCHEMA = HERE / "decision.schema.json"
RECORD_VERSION = 1
EST_CALL_USD = 0.30           # reserve per call when checking the budget
PHASE0_TARGETS = {"valid_share": 0.95, "median_latency_s": 120.0, "mean_cost_usd": 0.30}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue           # a torn last line must not block the whole log
        if isinstance(rec, dict):
            out.append(rec)
    return out


def append_record(path: Path, rec: dict[str, Any]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with path.open("a") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def month_to_date_usd(records: list[dict[str, Any]], now_ms: int) -> float:
    month = iso_utc(now_ms)[:7]
    return round(sum(float(r.get("cost_usd") or 0) for r in records
                     if str(r.get("decided_utc", ""))[:7] == month), 6)


def _code_version() -> str:
    stamp = HERE.parent / "DEPLOYED_SHA"
    return stamp.read_text().strip() if stamp.exists() else "working-tree"


def review(cand: Candidate, state: dict[str, Any], *, records: list[dict[str, Any]],
           runs_dir: Path, model: str, effort: str, budget_usd: float, max_age_min: int,
           client_factory: Callable[[], Any], fetch: FetchJSON = http_json,
           now_ms: Callable[[], int] = _now_ms) -> dict[str, Any]:
    """Build one record. Every path that is not a clean decision is a NO_DATA with a reason."""
    rec: dict[str, Any] = {
        "v": RECORD_VERSION, "signal_id": cand.signal_id, "symbol": cand.symbol,
        "direction": cand.direction, "cut_utc": cand.cut_iso, "entry": cand.entry,
        "model": model, "effort": effort, "prompt_sha256": _sha(PROMPT),
        "schema_sha256": _sha(SCHEMA), "code": _code_version(),
    }
    outcome: Outcome
    if now_ms() - cand.cut_ms > max_age_min * 60_000:
        outcome = Outcome("no_data", "backlog_expired")
    elif month_to_date_usd(records, now_ms()) + EST_CALL_USD > budget_usd:
        outcome = Outcome("no_data", "budget")
    else:
        try:
            user, meta = build_input(cand, state, fetch)
        except LookaheadError:
            outcome = Outcome("no_data", "lookahead_guard")
        except Exception as exc:  # noqa: BLE001 — market data failure is a recorded miss
            outcome = Outcome("no_data", f"market_data_{type(exc).__name__}")
        else:
            rec.update(meta)
            (runs_dir / f"{cand.signal_id.replace(':', '')}.input.md").write_text(user)
            outcome = decide(client_factory(), model=model, effort=effort,
                             system=PROMPT.read_text(), user=user,
                             schema=json.loads(SCHEMA.read_text()))
            if outcome.raw_text is not None:
                (runs_dir / f"{cand.signal_id.replace(':', '')}.output.json").write_text(outcome.raw_text)
    decided = now_ms()
    rec.update({
        "status": outcome.status, "no_data_reason": outcome.reason,
        "recommendation": (outcome.decision or {}).get("recommendation", "NO_DATA"),
        "decision": outcome.decision, "decided_utc": iso_utc(decided),
        "latency_s": round((decided - cand.cut_ms) / 1000, 1),
        "api_seconds": round(outcome.api_seconds, 2), "stop_reason": outcome.stop_reason,
        "request_id": outcome.request_id, "input_tokens": outcome.input_tokens,
        "output_tokens": outcome.output_tokens, "cost_usd": outcome.cost_usd,
    })
    return rec


def run(args: argparse.Namespace, client_factory: Callable[[], Any],
        fetch: FetchJSON = http_json, now_ms: Callable[[], int] = _now_ms) -> int:
    out_dir = Path(args.out_dir)
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    decisions = out_dir / "decisions.jsonl"
    with (out_dir / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another pass is running; skipping")
            return 0
        try:
            state = json.loads(Path(args.state).read_text())
        except (OSError, ValueError) as exc:
            # The bot may be mid-write; the next pass retries. Nothing is marked seen.
            print(f"state unreadable ({type(exc).__name__}); retry next pass")
            return 0
        records = load_records(decisions)
        seen = {r.get("signal_id") for r in records}
        since = ts_ms(args.since)
        pending = [c for c in full_opens(state.get("trade_log") or [])
                   if c.cut_ms >= since and c.signal_id not in seen]
        for cand in pending[:args.max_per_run]:
            rec = review(cand, state, records=records, runs_dir=runs_dir, model=args.model,
                         effort=args.effort, budget_usd=args.budget_usd,
                         max_age_min=args.max_age_min, client_factory=client_factory,
                         fetch=fetch, now_ms=now_ms)
            append_record(decisions, rec)
            records.append(rec)
            print(f"{rec['signal_id']}: {rec['recommendation']} "
                  f"({rec['no_data_reason'] or 'ok'}, {rec['latency_s']}s, ${rec['cost_usd']})")
    return 0


def summarize(records: list[dict[str, Any]], now_ms: int | None = None) -> dict[str, Any]:
    """Phase 0 plumbing metrics (DEFTER Tur 15). Not an evaluation of the veto."""
    n = len(records)
    decided = [r for r in records if r.get("status") == "decided"]
    reasons: dict[str, int] = {}
    for r in records:
        if r.get("status") != "decided":
            key = str(r.get("no_data_reason"))
            reasons[key] = reasons.get(key, 0) + 1
    latencies = [float(r["latency_s"]) for r in decided if r.get("latency_s") is not None]
    costs = [float(r.get("cost_usd") or 0) for r in records if r.get("input_tokens")]
    vetoes = sum(1 for r in decided if r.get("recommendation") == "VETO_RECOMMENDED")
    valid_share = len(decided) / n if n else None
    median_latency = statistics.median(latencies) if latencies else None
    mean_cost = sum(costs) / len(costs) if costs else None
    return {
        "records": n, "decided": len(decided), "no_data": reasons,
        "veto_rate": round(vetoes / len(decided), 3) if decided else None,
        "valid_share": round(valid_share, 3) if valid_share is not None else None,
        "median_latency_s": median_latency, "mean_cost_usd": round(mean_cost, 4) if mean_cost else None,
        "total_cost_usd": round(sum(costs), 4),
        "month_to_date_usd": month_to_date_usd(records, now_ms if now_ms is not None else _now_ms()),
        "lookahead_guard_hits": reasons.get("lookahead_guard", 0),
        "phase0_targets_met": bool(
            n and valid_share is not None and valid_share >= PHASE0_TARGETS["valid_share"]
            and median_latency is not None and median_latency < PHASE0_TARGETS["median_latency_s"]
            and mean_cost is not None and mean_cost <= PHASE0_TARGETS["mean_cost_usd"]
            and not reasons.get("lookahead_guard")),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", default="/root/BreakoutBot-test/state_paper.json")
    p.add_argument("--out-dir", default="/var/lib/breakoutbot-ai-shadow")
    p.add_argument("--since", help="review only entries at/after this UTC time (phase start)")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--budget-usd", type=float, default=18.0,
                   help="calendar-month cap for this worker's own calls")
    p.add_argument("--max-per-run", type=int, default=3)
    p.add_argument("--max-age-min", type=int, default=360)
    p.add_argument("--timeout", type=float, default=240.0, help="per API request, seconds")
    p.add_argument("--summary", action="store_true", help="print Phase 0 metrics and exit")
    args = p.parse_args(argv)
    if not args.summary and not args.since:
        p.error("--since is required (the phase start); refusing to review the whole history")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.summary:
        records = load_records(Path(args.out_dir) / "decisions.jsonl")
        print(json.dumps(summarize(records), indent=1))
        return 0

    def client_factory() -> Any:
        import anthropic
        return anthropic.Anthropic(timeout=args.timeout, max_retries=2)

    return run(args, client_factory)


if __name__ == "__main__":
    sys.exit(main())

