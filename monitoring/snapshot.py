"""Bounded, read-only observation of a BreakoutBot simulation.

Uses only the standard library; never imports bot code, calls a network, or
writes state. Reference limits describe the adopted policy, not a verified
running process configuration. Candle freshness and candidate signal quality
cannot be inferred from the saved state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any

MAX_OUTPUT_CHARS = 12_000
MAX_STATE_BYTES = 16 * 1024 * 1024
LOG_TAIL_BYTES = 64 * 1024
STALE_SECONDS = 660
REFERENCE = {
    "basis": "adopted policy; effective server configuration is unverified",
    "bar_seconds": 300, "max_full_positions": 2,
    "daily_dd_fraction": -0.05, "throttle_dd_fraction": -0.07,
    "hard_stop_dd_fraction": -0.15, "daily_sl_count_limit": 2,
    "trail_atr": 3.75, "phase_timeout_bars": 96,
    "session_utc": [4, 23],
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _text(value: Any, length: int = 64) -> str | None:
    return value[:length] if isinstance(value, str) else None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Bot events have offsets. Never guess a timezone for an ambiguous event.
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _read_state(path: Path) -> tuple[dict[str, Any], bytes, float]:
    """Retry reads racing the bot's non-atomic truncate-and-write persistence."""
    for attempt in range(3):
        try:
            with path.open("rb") as handle:
                before = path.stat()
                raw = handle.read(MAX_STATE_BYTES + 1)
                after = path.stat()
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError("state exceeds read limit")
            if (before.st_mtime_ns, before.st_size, before.st_ino) != (
                after.st_mtime_ns, after.st_size, after.st_ino
            ):
                raise ValueError("state changed during read")
            state = json.loads(raw)
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
            return state, raw, after.st_mtime
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def _log_summary(path: Path, now: float) -> dict[str, Any]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
        raw = handle.read(LOG_TAIL_BYTES)
    latest_bar = None
    errors: dict[str, int] = {}
    categories = {
        "fetch_failure": "fetch failed", "regime_failure": "regime failed",
        "unhandled_error": "Unhandled error:",
        "state_load_failure": "Could not load state",
        "hard_stop": "— hard stop.",
    }
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\] (.*)", line)
        if not match:
            continue
        try:
            ts = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            continue
        if ts > now + 60:
            continue
        if re.search(r"Bar #[\d,]+ \d\d:\d\d UTC", match[2]):
            latest_bar = max(ts, latest_bar or ts)
        if 0 <= now - ts <= 1800:
            for category, marker in categories.items():
                if marker in match[2]:
                    errors[category] = errors.get(category, 0) + 1
    return {
        "tail_sha256": hashlib.sha256(raw).hexdigest(),
        "tail_bytes": len(raw), "last_bar_log_at": _iso(latest_bar) if latest_bar else None,
        "last_bar_log_age_seconds": round(now - latest_bar, 1) if latest_bar else None,
        "errors_last_30m_in_tail": errors,
        "note": "Log may be buffered; heartbeat is not per-symbol candle freshness.",
    }


def build_snapshot(state_path: str | Path, log_path: str | Path | None = None,
                   now: float | None = None) -> dict[str, Any]:
    state_path = Path(state_path)
    log_path = Path(log_path) if log_path is not None else None
    now = time.time() if now is None else now
    result: dict[str, Any] = {
        "schema_version": 1, "observed_at": _iso(now), "mode": "read_only_simulation",
        "checks": [], "adopted_reference": REFERENCE,
        "visibility": {
            "market_data_freshness": "unknown: state stores no latest per-symbol candle time",
            "mark_prices_and_unrealized_pnl": "unavailable",
            "candidate_signals": "unavailable: saved probes and OPEN legs are already executed",
            "performance": "recent legs are not positions or a prospective AI track record",
            "effective_runtime_config": "unverified",
        },
    }

    def check(code: str, severity: str, **details: Any) -> None:
        result["checks"].append({"code": code, "severity": severity, **details})

    try:
        state, raw, mtime = _read_state(state_path)
    except (OSError, ValueError) as exc:
        check("state_unreadable", "critical", error_type=type(exc).__name__)
        result["status"] = "unavailable"
        return result
    age = now - mtime
    digest = hashlib.sha256(raw).hexdigest()
    result["source"] = {
        "state_sha256": digest, "state_modified_at": _iso(mtime),
        "state_age_seconds": round(age, 1), "state_bytes": len(raw),
        "state_schema_version": _number(state.get("schema_version")),
        "bar_count": _number(state.get("bar_count")),
        "full_leg_logging_since": _text(state.get("full_leg_logging_since")),
    }
    result["snapshot_id"] = hashlib.sha256((digest + _iso(now)).encode()).hexdigest()
    if age > STALE_SECONDS:
        check("state_stale", "critical", age_seconds=round(age), threshold_seconds=STALE_SECONDS)
    elif age < -60:
        check("state_clock_ahead", "critical", age_seconds=round(age))

    balance, peak, daily_start = (_number(state.get(k)) for k in ("balance", "peak", "daily_start"))
    dd = balance / peak - 1 if balance is not None and peak and peak > 0 else None
    day_dd = balance / daily_start - 1 if balance is not None and daily_start and daily_start > 0 else None
    freeze = state.get("daily_freeze") if isinstance(state.get("daily_freeze"), bool) else None
    daily_day = _text(state.get("daily_day"))
    today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    result["account"] = {
        "balance_usd": balance, "peak_recorded_balance_usd": peak,
        "recorded_balance_dd_fraction": round(dd, 6) if dd is not None else None,
        "daily_start_usd": daily_start, "daily_day": daily_day,
        "recorded_day_dd_fraction": round(day_dd, 6) if day_dd is not None else None,
        "daily_freeze": freeze, "daily_sl_count": _number(state.get("daily_sl_count")),
        "expected_size_factor_from_reference": (0.5 if dd <= -0.07 else 1.0) if dd is not None else None,
        "probe_cost_recorded_usd": _number(state.get("probe_cost")),
        "note": "Balance-based limits exclude unrealized PnL. Size factor is inferred, not observed.",
    }
    if balance is None or balance < 0 or peak is None or peak <= 0 or daily_start is None or daily_start <= 0:
        check("invalid_account_fields", "critical")
    if dd is not None and dd <= -0.15:
        check("reference_hard_stop_reached", "critical", reference_only=True)
    elif dd is not None and dd <= -0.07:
        check("reference_throttle_zone", "info", reference_only=True)
    if daily_day == today and day_dd is not None and day_dd <= -0.05 and freeze is False:
        check("daily_freeze_expected_but_not_recorded", "warning", reference_only=True)
    if daily_day != today and age <= STALE_SECONDS:
        check("state_day_mismatch", "warning")

    positions = []
    symbols = state.get("sym_states", {})
    if not isinstance(symbols, dict):
        symbols = {}
        check("invalid_symbol_states", "critical")
    for symbol, saved in symbols.items():
        if not isinstance(saved, dict):
            check("invalid_symbol_state", "warning", symbol=_text(symbol, 32))
            continue
        phase = _text(saved.get("state"), 24)
        if phase == "IDLE":
            continue
        position: dict[str, Any] = {"symbol": _text(symbol, 32), "state": phase,
                                    "direction": _text(saved.get("direction"), 12)}
        position.update({k: _number(saved.get(k)) for k in (
            "test_entry", "test_sl", "test_atr", "full_entry", "full_sl",
            "full_tp1", "full_tp2", "full_notional", "trail_best", "bars_held"
        )})
        position["bars_held_meaning"] = "current phase only; resets at TP1"
        if phase == "TRAILING":
            position["stop_note"] = "full_sl is not checked in TRAILING; active trail depends on runtime TRAIL_ATR"
        elif phase not in {"TEST_OPEN", "SCALE_OPEN"}:
            check("unknown_position_state", "warning", symbol=_text(symbol, 32))
        positions.append(position)
    full_count = sum(p["state"] in {"SCALE_OPEN", "TRAILING"} for p in positions)
    result["open_positions"] = positions[:16]
    result["position_counts"] = {"active_total": len(positions), "full": full_count,
                                 "omitted": max(0, len(positions) - 16)}
    if full_count > 2:
        check("reference_position_limit_exceeded", "warning", full_count=full_count)
    mr_states = state.get("mr_states", {})
    # mean_reversion.py uses its own MR_IDLE/MR_OPEN enum, unlike momentum.
    # Recognize a position positively; an unknown status is not evidence of one.
    active_mr = [str(s)[:32] for s, p in mr_states.items()
                 if isinstance(p, dict) and p.get("state") == "MR_OPEN"] if isinstance(mr_states, dict) else []
    unknown_mr = [str(s)[:32] for s, p in mr_states.items()
                  if not isinstance(p, dict) or p.get("state") not in
                  {None, "IDLE", "MR_IDLE", "MR_OPEN"}] if isinstance(mr_states, dict) else []
    result["active_mr_symbols"] = active_mr[:16]
    if active_mr:
        check("mr_positions_present_with_reference_mr_disabled", "warning")
    if unknown_mr:
        check("unknown_mr_state", "warning", symbols=unknown_mr[:16], count=len(unknown_mr))

    regimes = state.get("regime", {})
    result["regimes"] = {str(k)[:32]: _text(v, 16) for k, v in list(regimes.items())[:24]} if isinstance(regimes, dict) else {}
    regime_ts = _number(state.get("regime_4h_ts"))
    result["regime_refresh_age_seconds"] = round(now - regime_ts / 1000) if regime_ts and regime_ts > 0 else None
    if regime_ts and 0 < regime_ts / 1000 < now - 4 * 3600 - STALE_SECONDS:
        check("regime_refresh_stale", "warning")
    funnel = state.get("funnel_totals", {})
    result["funnel_totals"] = {k: _number(funnel.get(k)) for k in (
        "scanned", "regime_pass", "probe", "confirm_ok", "confirm_fail", "full", "blocked_max_open"
    )} if isinstance(funnel, dict) else {}

    trades = state.get("trade_log", [])
    if not isinstance(trades, list):
        trades = []
        check("invalid_trade_log", "warning")
    recent = []
    invalid_ts = future_ts = 0
    for index, leg in enumerate(trades):
        if not isinstance(leg, dict):
            invalid_ts += 1
            continue
        ts = _timestamp(leg.get("ts"))
        if ts is None:
            invalid_ts += 1
            continue
        if ts > now:
            future_ts += 1
            continue
        if now - ts > 86400:
            continue
        record: dict[str, Any] = {k: _text(leg.get(k), 48) for k in ("ts", "symbol", "direction", "kind", "sleeve", "exit_type")}
        record.update({k: _number(leg.get(k)) for k in ("entry", "exit", "pnl", "balance")})
        record["source_index"] = index
        record["judgment_eligibility"] = "already_executed; retrospective context only"
        recent.append((ts, index, record))
    recent.sort(key=lambda x: (x[0], x[1]))
    result["recent_legs"] = [r[2] for r in recent[-12:]]
    result["recent_legs_24h_total"] = len(recent)
    result["trade_log_total_legs"] = len(trades)
    if invalid_ts:
        check("trade_timestamps_unusable", "warning", count=invalid_ts)
    if future_ts:
        check("future_trade_timestamps_excluded", "warning", count=future_ts)
    if log_path is not None:
        try:
            result["log"] = _log_summary(log_path, now)
            if result["log"]["errors_last_30m_in_tail"]:
                check("recent_bot_errors", "warning")
        except OSError as exc:
            check("log_unreadable", "warning", error_type=type(exc).__name__)
    result["status"] = "attention" if any(c["severity"] in {"warning", "critical"} for c in result["checks"]) else "observed"
    # Library callers receive the same bounded object as CLI callers.
    return json.loads(render_snapshot(result))


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """Guarantee valid JSON within the prompt budget for a generated snapshot."""
    snapshot = dict(snapshot)

    def encode() -> str:
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    output = encode()
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    snapshot["output_truncated"] = True
    for key in ("recent_legs", "open_positions", "regimes"):
        snapshot[key] = [] if key != "regimes" else {}
        output = encode()
        if len(output) <= MAX_OUTPUT_CHARS:
            return output
    snapshot["checks"] = [{"code": c["code"], "severity": c["severity"]} for c in snapshot["checks"][:40]]
    return encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    snapshot = build_snapshot(args.state, args.log)
    print(render_snapshot(snapshot))
    return 2 if snapshot["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
