"""AI shadow worker (DEFTER Tur 15): no network, no real model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import urllib.parse

import anthropic
import httpx
import pytest

from ai_shadow import candidates as C
from ai_shadow import features as F
from ai_shadow import llm as L
from ai_shadow import market as M
from ai_shadow import worker as W

CUT = "2026-09-25T10:35:00+00:00"
CUT_MS = C.ts_ms(CUT)


def leg(ts: str, symbol: str, exit_type: str, pnl: float, kind: str = "FULL",
        sleeve: str = "MOMENTUM", entry: float = 10.0) -> dict[str, Any]:
    return {"ts": ts, "symbol": symbol, "direction": "LONG", "sleeve": sleeve, "kind": kind,
            "exit_type": exit_type, "entry": entry, "exit": 0.0, "pnl": pnl, "balance": 950.0}


def make_state() -> dict[str, Any]:
    return {
        "trade_log": [
            leg("2026-09-25T09:00:00+00:00", "ADAUSDT", "OPEN", -0.2),
            leg("2026-09-25T09:30:00+00:00", "ADAUSDT", "TP1", 0.0),
            leg("2026-09-25T10:00:00+00:00", "ADAUSDT", "SL", -4.8),
            leg("2026-09-25T10:10:00+00:00", "INJUSDT", "OPEN", -0.2),
            leg("2026-09-25T10:30:00+00:00", "NEARUSDT", "OPEN", -0.1, kind="TEST", sleeve="PROBE"),
            leg(CUT, "UNIUSDT", "OPEN", -0.34, entry=9.377),
            # Known only after the cut — must never reach the candidate's snapshot.
            leg("2026-09-25T10:40:00+00:00", "INJUSDT", "TRAIL", 7.0),
            leg("2026-09-25T11:30:00+00:00", "UNIUSDT", "TP2", 12.0),
        ],
        "sym_states": {"UNIUSDT": {
            "state": "SCALE_OPEN", "direction": "LONG", "test_entry": 9.356, "test_atr": 0.046,
            "full_entry": 9.377, "full_sl": 9.2734, "full_tp1": 9.5152, "full_tp2": 9.6534,
            "full_notional": 452.35, "tp1_hit": True, "trail_best": 9.9, "bars_held": 17}},
    }


def fake_fetch(url: str) -> list[list[Any]]:
    """Binance-like klines ending at endTime — including the still-forming bar."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    span = M.SPAN_MS[q["interval"]]
    last_open = int(q["endTime"]) // span * span
    rows = []
    for i in range(int(q["limit"])):
        t = last_open - (int(q["limit"]) - 1 - i) * span
        base = 10 + 0.001 * (t // span % 997) + (0.05 if (t // span) % 7 == 0 else 0)
        rows.append([t, f"{base:.4f}", f"{base + 0.02:.4f}", f"{base - 0.02:.4f}",
                     f"{base + 0.005:.4f}", "1234.5", t + span - 1])
    return rows


class FakeClient:
    def __init__(self, text: str | None = None, stop_reason: str = "end_turn",
                 exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self
        self._text = text if text is not None else json.dumps({
            "recommendation": "VETO_RECOMMENDED", "confidence": 0.62,
            "key_factors": ["4h direnç 9.52'de üç kez reddedildi"], "reasoning": "Kısa gerekçe."})
        self._stop, self._exc = stop_reason, exc

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=14_000, output_tokens=5_000),
            stop_reason=self._stop, id="msg_1", _request_id="req_1")


def args_for(tmp_path: Path, state: dict[str, Any], **over: Any) -> argparse.Namespace:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    base = dict(state=str(state_path), out_dir=str(tmp_path / "out"), since="2026-09-25T10:30:00Z",
                model="claude-opus-5-5", effort="medium", budget_usd=18.0, max_per_run=3,
                max_age_min=360, timeout=240.0, summary=False)
    base.update(over)
    return argparse.Namespace(**base)


def now_at(minutes_after_cut: float):
    return lambda: CUT_MS + int(minutes_after_cut * 60_000)


# ── candidates ────────────────────────────────────────────────────────────────

def test_full_opens_takes_only_momentum_full_opens():
    cands = C.full_opens(make_state()["trade_log"])
    assert [c.signal_id for c in cands] == [
        "ADAUSDT@2026-09-25T09:00:00Z", "INJUSDT@2026-09-25T10:10:00Z", "UNIUSDT@2026-09-25T10:35:00Z"]
    uni = cands[-1]
    assert uni.cut_ms == CUT_MS and uni.entry == 9.377 and uni.entry_fee == -0.34


def test_book_ignores_everything_after_the_cut():
    closed, still_open = C.book_at(make_state()["trade_log"], CUT_MS,
                                   exclude="UNIUSDT@2026-09-25T10:35:00Z")
    assert [(p.symbol, p.exit_type, p.pnl) for p in closed] == [("ADAUSDT", "SL", -5.0)]
    # INJ's 10:40 TRAIL exit is in the future of the cut: still open, pnl not exposed.
    assert [(p["symbol"], "pnl" in p) for p in still_open] == [("INJUSDT", False)]


# ── market / look-ahead ───────────────────────────────────────────────────────

@pytest.mark.parametrize("interval", ["5m", "1h", "4h"])
def test_fetch_closed_never_returns_a_candle_closing_after_the_cut(interval):
    seen: list[str] = []

    def fetch(url: str) -> Any:
        seen.append(url)
        return fake_fetch(url)

    candles = M.fetch_closed("UNIUSDT", interval, CUT_MS, 60, fetch)
    span = M.SPAN_MS[interval]
    assert len(candles) == 60
    assert all(c.open_ms + span <= CUT_MS for c in candles)
    assert f"endTime={CUT_MS - 1}" in seen[0]
    if interval == "5m":          # the 10:30 bar closed exactly at the cut — it belongs in
        assert candles[-1].open_ms == CUT_MS - span


def test_assert_closed_rejects_a_forming_candle():
    forming = [M.Candle(CUT_MS - 300_000, 1, 1, 1, 1, 1), M.Candle(CUT_MS - 1_800_000, 1, 1, 1, 1, 1)]
    with pytest.raises(M.LookaheadError):
        M.assert_closed(forming, "1h", CUT_MS)


# ── features ──────────────────────────────────────────────────────────────────

def test_geometry_matches_the_friction_identity():
    g = F.geometry("LONG", 100.0, 98.0, 106.0, 112.0, atr5=0.5)
    assert g["sl_pct"] == 2.0 and g["sl_atr5"] == 4.0
    assert g["tp1_r"] == 3.0 and g["tp2_r"] == 6.0
    assert g["fee_share_of_risk"] == 0.075        # 0.0015 / 0.02
    assert F.geometry("LONG", 100.0, None, None, None, None) == {"available": False}


def test_timeframe_features_are_bounded():
    rows = fake_fetch(f"x?symbol=A&interval=5m&endTime={CUT_MS - 1}&limit=300")
    candles = [M.Candle(int(r[0]), *(float(x) for x in r[1:6])) for r in rows]
    feats = F.timeframe_features(candles, candles, candles)
    assert 0 <= feats["range_position"]["5m_48"] <= 1
    assert feats["atr14_pct"]["5m"] > 0 and feats["volume_ratio_5m_last_vs_48"] == 1.0


# ── llm ───────────────────────────────────────────────────────────────────────

def test_decide_sends_the_pinned_arm_and_no_fallback():
    client = FakeClient()
    out = L.decide(client, model="claude-opus-5-5", effort="medium", system="s", user="u",
                   schema={"type": "object"})
    assert out.status == "decided" and out.decision["recommendation"] == "VETO_RECOMMENDED"
    assert out.cost_usd == pytest.approx(14_000 * 4 / 1e6 + 5_000 * 20 / 1e6)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" not in kw and "betas" not in kw


@pytest.mark.parametrize("client,reason", [
    (FakeClient(stop_reason="refusal"), "refusal"),
    (FakeClient(stop_reason="max_tokens"), "max_tokens"),
    (FakeClient(text="not json"), "invalid_output"),
    (FakeClient(text=json.dumps({"recommendation": "BUY", "confidence": 1,
                                 "key_factors": [], "reasoning": ""})), "invalid_output"),
    (FakeClient(exc=RuntimeError("boom")), "error_RuntimeError"),
])
def test_every_miss_becomes_a_categorised_no_data(client, reason):
    out = L.decide(client, model="claude-opus-5-5", effort="medium", system="s", user="u", schema={})
    assert out.status == "no_data" and out.reason == reason


def test_api_errors_map_to_their_status():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    auth = anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    credit = anthropic.BadRequestError("Your credit balance is too low",
                                       response=httpx.Response(400, request=req), body=None)
    assert L._error_reason(auth) == "auth_401"
    assert L._error_reason(credit) == "credits"
    assert L._error_reason(anthropic.APITimeoutError(request=req)) == "timeout"


# ── worker ────────────────────────────────────────────────────────────────────

def test_a_signal_is_reviewed_once_and_only_after_the_phase_start(tmp_path):
    client = FakeClient()
    args = args_for(tmp_path, make_state())
    W.run(args, lambda: client, fake_fetch, now_at(1))
    W.run(args, lambda: client, fake_fetch, now_at(6))       # second pass: nothing new
    records = W.load_records(tmp_path / "out" / "decisions.jsonl")
    assert [r["signal_id"] for r in records] == ["UNIUSDT@2026-09-25T10:35:00Z"]
    assert len(client.calls) == 1
    rec = records[0]
    assert rec["status"] == "decided" and rec["recommendation"] == "VETO_RECOMMENDED"
    assert rec["latency_s"] == 60.0 and rec["levels_available"] is True
    assert len(rec["prompt_sha256"]) == 64 and len(rec["input_sha256"]) == 64
    snapshot = (tmp_path / "out" / "runs" / "UNIUSDT@2026-09-25T103500Z.input.md").read_text()
    # Entry-time levels only: TP1 progress and the trail are future information.
    assert "9.2734" in snapshot and "trail_best" not in snapshot and "tp1_hit" not in snapshot
    assert "TP2" not in snapshot.split("# Candles")[0]        # UNI's own future exit


def test_budget_guard_records_no_data_without_calling_the_model(tmp_path):
    client = FakeClient()
    args = args_for(tmp_path, make_state(), budget_usd=5.0)
    out = tmp_path / "out"
    out.mkdir()
    W.append_record(out / "decisions.jsonl", {"signal_id": "old", "status": "decided",
                                              "decided_utc": "2026-09-25T08:00:00Z", "cost_usd": 4.9})
    W.run(args, lambda: client, fake_fetch, now_at(1))
    rec = W.load_records(out / "decisions.jsonl")[-1]
    assert rec["no_data_reason"] == "budget" and client.calls == []


def test_stale_backlog_is_not_sent_to_the_model(tmp_path):
    client = FakeClient()
    W.run(args_for(tmp_path, make_state(), max_age_min=30), lambda: client, fake_fetch, now_at(90))
    rec = W.load_records(tmp_path / "out" / "decisions.jsonl")[-1]
    assert rec["no_data_reason"] == "backlog_expired" and client.calls == []


def test_lookahead_guard_is_a_recorded_miss(tmp_path, monkeypatch):
    def leaky(*_a: Any, **_k: Any) -> Any:
        raise M.LookaheadError("1h: 1 candle(s) close after the cut")

    monkeypatch.setattr(W, "build_input", leaky)
    client = FakeClient()
    W.run(args_for(tmp_path, make_state()), lambda: client, fake_fetch, now_at(1))
    rec = W.load_records(tmp_path / "out" / "decisions.jsonl")[-1]
    assert rec["no_data_reason"] == "lookahead_guard" and client.calls == []


def test_unreadable_state_marks_nothing_seen(tmp_path):
    args = args_for(tmp_path, make_state())
    Path(args.state).write_text("{truncated")
    client = FakeClient()
    assert W.run(args, lambda: client, fake_fetch, now_at(1)) == 0
    assert not (tmp_path / "out" / "decisions.jsonl").exists()
    Path(args.state).write_text(json.dumps(make_state()))
    W.run(args, lambda: client, fake_fetch, now_at(2))
    assert len(W.load_records(tmp_path / "out" / "decisions.jsonl")) == 1


def test_summary_reports_phase0_targets():
    recs = [
        {"status": "decided", "recommendation": "ALLOW", "latency_s": 70, "input_tokens": 1,
         "cost_usd": 0.2, "decided_utc": "2026-09-25T10:00:00Z"},
        {"status": "decided", "recommendation": "VETO_RECOMMENDED", "latency_s": 90,
         "input_tokens": 1, "cost_usd": 0.2, "decided_utc": "2026-09-25T11:00:00Z"},
        {"status": "no_data", "no_data_reason": "network", "latency_s": 5, "cost_usd": 0},
    ]
    s = W.summarize(recs, now_ms=CUT_MS)
    assert s["decided"] == 2 and s["no_data"] == {"network": 1} and s["veto_rate"] == 0.5
    assert s["median_latency_s"] == 80 and s["month_to_date_usd"] == 0.4
    assert s["phase0_targets_met"] is False          # 2/3 valid < 95%


def test_since_is_mandatory_for_a_review_pass():
    with pytest.raises(SystemExit):
        W.parse_args(["--state", "s.json"])
