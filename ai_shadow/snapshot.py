"""Assemble the reviewer's input for one candidate: context JSON + compact candle tables.

The code-computed features use longer windows than the tables shown to the
model (e.g. 220 4h bars for the 200-SMA, 90 shown) so the input stays near the
~15K-token budget DEFTER Tur 15 costs against.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .candidates import Candidate, book_at, iso_utc
from .features import atr, geometry, return_corr, timeframe_features
from .market import Candle, FetchJSON, fetch_closed, http_json

BTC = "BTCUSDT"
FETCH = {"5m": 288, "1h": 168, "4h": 220}
SHOWN = {"5m": 144, "1h": 72, "4h": 90}
SHOWN_BTC = {"5m": 48, "1h": 72, "4h": 90}
RECENT_CLOSED = 15
ENTRY_FIELDS = ("test_entry", "test_atr", "full_entry", "full_sl", "full_tp1", "full_tp2",
                "full_notional")


def _table(symbol: str, interval: str, candles: list[Candle], n: int) -> str:
    rows = candles[-n:]
    head = (f"## {symbol} {interval} — {len(rows)} closed candles, first open "
            f"{iso_utc(rows[0].open_ms)}, oldest→newest, columns o,h,l,c,v")
    body = "\n".join(f"{c.open:.5g},{c.high:.5g},{c.low:.5g},{c.close:.5g},{c.volume:.3g}"
                     for c in rows)
    return f"{head}\n{body}"


def entry_levels(state: dict[str, Any], cand: Candidate) -> dict[str, Any] | None:
    """The bot's own levels for this position — only while it is the same position.

    Only fields fixed at entry are read; trail/TP1 progress would be future data.
    """
    sym = (state.get("sym_states") or {}).get(cand.symbol)
    if not isinstance(sym, dict) or sym.get("direction") != cand.direction:
        return None
    full_entry = sym.get("full_entry")
    if not isinstance(full_entry, (int, float)) or abs(full_entry - cand.entry) > 1e-9 * max(1.0, cand.entry):
        return None
    return {k: sym.get(k) for k in ENTRY_FIELDS}


def build_input(cand: Candidate, state: dict[str, Any],
                fetch: FetchJSON = http_json) -> tuple[str, dict[str, Any]]:
    """Return (user message text, metadata). Raises LookaheadError / ValueError / OSError."""
    coin = {tf: fetch_closed(cand.symbol, tf, cand.cut_ms, FETCH[tf], fetch) for tf in FETCH}
    btc = {tf: fetch_closed(BTC, tf, cand.cut_ms, FETCH[tf], fetch) for tf in FETCH}
    levels = entry_levels(state, cand)
    atr5 = atr(coin["5m"])
    closed, still_open = book_at(state.get("trade_log") or [], cand.cut_ms, exclude=cand.signal_id)
    recent = closed[-RECENT_CLOSED:]
    context = {
        "candidate": {"signal_id": cand.signal_id, "symbol": cand.symbol,
                      "direction": cand.direction, "entry_bar_close_utc": cand.cut_iso,
                      "entry_price": cand.entry, "entry_fee_usd": cand.entry_fee,
                      "balance_after_fee_usd": cand.balance},
        "bot_levels_at_entry": levels,
        "geometry": geometry(cand.direction, cand.entry, (levels or {}).get("full_sl"),
                             (levels or {}).get("full_tp1"), (levels or {}).get("full_tp2"), atr5),
        "features": {cand.symbol: timeframe_features(coin["5m"], coin["1h"], coin["4h"]),
                     BTC: timeframe_features(btc["5m"], btc["1h"], btc["4h"]),
                     "corr_1h_returns_vs_btc_72": return_corr(coin["1h"], btc["1h"], 72)},
        "book": {
            "open_positions_at_entry": still_open,
            "recent_closed_positions": [vars(p) for p in recent],
            "recent_closed_summary": {
                "n": len(recent),
                "wins": sum(1 for p in recent if p.pnl > 0),
                "pnl_sum_usd": round(sum(p.pnl for p in recent), 2),
            },
        },
    }
    tables = [_table(cand.symbol, tf, coin[tf], SHOWN[tf]) for tf in SHOWN]
    tables += [_table(BTC, tf, btc[tf], SHOWN_BTC[tf]) for tf in SHOWN_BTC]
    text = ("# Context (JSON)\n" + json.dumps(context, ensure_ascii=False, indent=1)
            + "\n\n# Candles\n" + "\n\n".join(tables))
    meta = {
        "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "input_chars": len(text),
        "levels_available": levels is not None,
        "last_candle_open_utc": {tf: coin[tf][-1].open_ms for tf in FETCH},
    }
    return text, meta
