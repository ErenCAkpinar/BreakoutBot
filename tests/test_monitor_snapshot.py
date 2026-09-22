"""Monitoring must not confuse operational observations with trading evidence."""
from datetime import datetime, timezone
import json
import os

from monitoring.snapshot import MAX_OUTPUT_CHARS, build_snapshot, render_snapshot

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc).timestamp()


def state_file(tmp_path, **changes):
    state = {"balance": 980.0, "peak": 1000.0, "daily_start": 985.0,
             "daily_day": "2026-09-16", "daily_freeze": False,
             "daily_sl_count": 0, "bar_count": 200, "schema_version": 2,
             "sym_states": {}, "trade_log": [], "regime": {}, "mr_states": {}}
    state.update(changes)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    os.utime(path, (NOW - 10, NOW - 10))
    return path


def codes(snapshot):
    return {c["code"] for c in snapshot["checks"]}


def test_recent_state_with_no_trades_does_not_claim_stale_market_data(tmp_path):
    path = state_file(tmp_path)
    before = path.read_bytes(), path.stat().st_mtime_ns
    snapshot = build_snapshot(path, now=NOW)
    assert snapshot["status"] == "observed"
    assert snapshot["visibility"]["market_data_freshness"].startswith("unknown")
    assert snapshot["recent_legs"] == []
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_staleness_uses_state_mtime_not_old_or_recent_trades(tmp_path):
    path = state_file(tmp_path, trade_log=[{"ts": "2026-09-16T12:00:00+00:00"}])
    os.utime(path, (NOW - 900, NOW - 900))
    assert "state_stale" in codes(build_snapshot(path, now=NOW))


def test_trailing_does_not_report_dead_breakeven_field_as_active_stop(tmp_path):
    path = state_file(tmp_path, sym_states={"UNIUSDT": {
        "state": "TRAILING", "direction": "LONG", "full_sl": 10,
        "full_entry": 10, "trail_best": 10.3, "test_atr": 0.2,
        "bars_held": 20, "full_notional": 500,
    }})
    position = build_snapshot(path, now=NOW)["open_positions"][0]
    assert "not checked" in position["stop_note"]
    assert "resets at TP1" in position["bars_held_meaning"]
    assert "active_stop" not in position


def test_five_idle_mr_machines_are_not_reported_as_positions(tmp_path):
    symbols = ["UNIUSDT", "INJUSDT", "ADAUSDT", "POLUSDT", "NEARUSDT"]
    path = state_file(tmp_path, mr_states={
        symbol: {"state": "MR_IDLE", "entry": 0, "notional": 0}
        for symbol in symbols
    })
    snapshot = build_snapshot(path, now=NOW)
    assert snapshot["active_mr_symbols"] == []
    assert "mr_positions_present_with_reference_mr_disabled" not in codes(snapshot)
    assert snapshot["status"] == "observed"


def test_only_explicit_mr_open_counts_and_unknown_state_is_separate(tmp_path):
    path = state_file(tmp_path, mr_states={
        "UNIUSDT": {"state": "MR_OPEN", "entry": 10, "notional": 500},
        "ADAUSDT": {"state": "MR_IDLE"},
        "NEARUSDT": {"state": "UNRECOGNIZED"},
    })
    snapshot = build_snapshot(path, now=NOW)
    assert snapshot["active_mr_symbols"] == ["UNIUSDT"]
    assert "mr_positions_present_with_reference_mr_disabled" in codes(snapshot)
    assert "unknown_mr_state" in codes(snapshot)


def test_closed_trade_is_explicitly_retrospective_and_future_is_excluded(tmp_path):
    path = state_file(tmp_path, trade_log=[
        {"ts": "2026-09-16T11:55:00+00:00", "exit_type": "TP2", "pnl": 20},
        {"ts": "2026-09-16T12:05:00+00:00", "exit_type": "SL", "pnl": -10},
        {"ts": "2026-09-16T11:55:00", "exit_type": "SL", "pnl": -10},
    ])
    snapshot = build_snapshot(path, now=NOW)
    assert len(snapshot["recent_legs"]) == 1
    assert "retrospective" in snapshot["recent_legs"][0]["judgment_eligibility"]
    assert "future_trade_timestamps_excluded" in codes(snapshot)
    assert "trade_timestamps_unusable" in codes(snapshot)


def test_log_checks_ignore_old_errors_and_never_forward_raw_text(tmp_path):
    path = state_file(tmp_path)
    log = tmp_path / "bot.log"
    log.write_text(
        "[2026-09-15 12:00:00 UTC] Unhandled error: old failure\n"
        "[2026-09-16 11:55:02 UTC] Bar #1,234 11:55 UTC | $980\n"
        "[2026-09-16 11:59:00 UTC] BTC 5m fetch failed (PRIVATE PAYLOAD)\n"
    )
    snapshot = build_snapshot(path, log, now=NOW)
    assert snapshot["log"]["errors_last_30m_in_tail"] == {"fetch_failure": 1}
    assert snapshot["log"]["last_bar_log_age_seconds"] == 298
    assert "PRIVATE PAYLOAD" not in render_snapshot(snapshot)


def test_reference_risk_is_not_misrepresented_as_runtime_configuration(tmp_path):
    path = state_file(tmp_path, balance=840, daily_start=1000)
    snapshot = build_snapshot(path, now=NOW)
    assert "reference_hard_stop_reached" in codes(snapshot)
    assert "daily_freeze_expected_but_not_recorded" in codes(snapshot)
    assert snapshot["visibility"]["effective_runtime_config"] == "unverified"
    assert snapshot["account"]["expected_size_factor_from_reference"] == 0.5


def test_invalid_state_and_nonfinite_values_do_not_fabricate_balance(tmp_path):
    path = state_file(tmp_path, balance=float("nan"))
    snapshot = build_snapshot(path, now=NOW)
    assert snapshot["account"]["balance_usd"] is None
    assert "invalid_account_fields" in codes(snapshot)
    json.loads(render_snapshot(snapshot))
    path.write_text("{")
    assert build_snapshot(path, now=NOW)["status"] == "unavailable"


def test_large_state_stays_bounded_and_keeps_check_codes(tmp_path):
    path = state_file(tmp_path,
                      sym_states={f"COIN{i}": {"state": "INVALID"} for i in range(500)},
                      trade_log=[{"ts": "2026-09-16T11:55:00+00:00", "pnl": 1}
                                 for _ in range(1000)])
    snapshot = build_snapshot(path, now=NOW)
    output = render_snapshot(snapshot)
    assert len(output) <= MAX_OUTPUT_CHARS
    parsed = json.loads(output)
    assert parsed["output_truncated"]
    assert parsed["position_counts"]["active_total"] == 500
    assert "unknown_position_state" in codes(parsed)
