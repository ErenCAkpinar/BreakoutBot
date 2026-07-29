"""
BreakoutBot — Live Paper Trading Engine

Runs the Test-Confirm-Scale strategy live on all configured TOKENS.
Two modes:

  python paper_bb.py               # pure local simulation (no API key needed)
  python paper_bb.py --testnet     # place real orders on Binance Futures Testnet
                                   # (requires secrets_local.py with API credentials)

Usage:
    python paper_bb.py                           # simulate locally
    python paper_bb.py --testnet                 # testnet live orders
    python paper_bb.py --tokens SOLUSDT INJUSDT  # subset of tokens
    python paper_bb.py --resume                  # resume from saved state
    python paper_bb.py --status                  # print status and exit

Strategy: Wave 11 MathEngine signal → $20 test → 1-bar confirm → $300×3x full → TP1/TP2/TRAIL

Exit with Ctrl-C; state is saved automatically to state_paper.json.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
import requests as _requests

from config import (
    TOKENS, TIMEFRAME, INITIAL_BALANCE,
    DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT, DAILY_SL_LIMIT,
    SESSION_START_UTC, SESSION_END_UTC,
    TEST_SIZE_USD, FULL_SIZE_USD, LEVERAGE, MAX_OPEN, RISK_PER_TRADE_USD,
    BTC_GATE_ENABLED, BTC_GATE_RETURN, BTC_BETA_WINDOW,
)
from indicators import build_snapshot, hurst_exponent, precompute_indicators
from strategy import SymbolState, IDLE, SCALE_OPEN, TRAILING, TEST_OPEN
from mean_reversion import MRState
import regime
from config import MR_ENABLED
from metrics import aggregate_positions, position_stats

STATE_FILE = "state_paper.json"
LOG_FILE   = "paper_bb.log"

FETCH_BARS = 200   # rolling history window per symbol (enough for all indicators)
BARS_WARM  = 80    # skip first 80 bars (indicator warmup)


# ── Testnet order manager ─────────────────────────────────────────────────────

def _ccxt_sym(symbol: str) -> str:
    """Convert config-style symbol (BTCUSDT) to ccxt unified (BTC/USDT:USDT)."""
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return symbol


class TestnetOrderManager:
    """
    Places and manages REAL orders on Binance Futures Testnet via direct HTTP.

    ccxt's set_sandbox_mode(True) is deprecated for binanceusdm (ccxt v4.5+).
    We bypass it entirely and sign requests manually — confirmed working 2026-05.

    SL/TP timing is handled by the local state machine; this class only executes.
    """

    BASE = "https://testnet.binancefuture.com"

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret  = secret
        # Cache exchange info (symbol precision) — fetched once at init
        self._step_sizes: dict[str, float] = {}
        self._load_exchange_info()

    # ── Signing helpers ───────────────────────────────────────────────────────

    def _sign(self, params: dict) -> dict:
        """Add timestamp + HMAC-SHA256 signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        qs  = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _hdr(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        p = self._sign(params or {})
        r = _requests.get(f"{self.BASE}{path}", params=p,
                          headers=self._hdr(), timeout=8)
        return r.json()

    def _post(self, path: str, params: dict) -> dict:
        p = self._sign(params)
        r = _requests.post(f"{self.BASE}{path}",
                           data=p, headers=self._hdr(), timeout=8)
        return r.json()

    # ── Exchange info + precision ─────────────────────────────────────────────

    def _load_exchange_info(self) -> None:
        """Cache LOT_SIZE stepSize for each symbol."""
        try:
            info = _requests.get(f"{self.BASE}/fapi/v1/exchangeInfo",
                                 timeout=8).json()
            for s in info.get("symbols", []):
                sym = s["symbol"]
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        self._step_sizes[sym] = float(f["stepSize"])
        except Exception as e:
            print(f"  ⚠️  Exchange info load: {e}")

    def _round_qty(self, symbol: str, qty: float) -> float:
        """Round quantity to valid stepSize."""
        step = self._step_sizes.get(symbol, 0.001)
        if step > 0:
            qty = round(round(qty / step) * step, 8)
        return max(qty, step)   # never go below minimum

    # ── Setup ────────────────────────────────────────────────────────────────

    def setup_symbols(self, symbols: list[str]) -> None:
        """Set isolated margin + 3× leverage for each symbol at startup."""
        print(f"  Setting up {len(symbols)} symbols (isolated, {LEVERAGE}× leverage)…")
        for sym in symbols:
            # Set ISOLATED margin mode (ignore "already set" error)
            r = self._post("/fapi/v1/marginType",
                           {"symbol": sym, "marginType": "ISOLATED"})
            if r.get("code") not in (200, None, -4059, -4046):  # -4046/-4059 = already isolated
                print(f"  ⚠️  Margin {sym}: {r}")
            # Set leverage
            r = self._post("/fapi/v1/leverage",
                           {"symbol": sym, "leverage": LEVERAGE})
            if "code" in r and r["code"] != 200:
                print(f"  ⚠️  Leverage {sym}: {r}")
        print("  ✅ Symbol setup complete")

    def get_balance(self) -> float:
        """Fetch available USDT balance from testnet."""
        try:
            items = self._get("/fapi/v2/balance")
            if isinstance(items, list):
                for item in items:
                    if item.get("asset") == "USDT":
                        return float(item.get("availableBalance", 0))
        except Exception as e:
            print(f"  ⚠️  Balance fetch: {e}")
        return 0.0

    # ── Order placement ───────────────────────────────────────────────────────

    def open_market(self, symbol: str, direction: str,
                    notional_usd: float, price: float) -> float:
        """
        Open a market order.
        notional_usd = total exposure USD (TEST_SIZE_USD or FULL_SIZE_USD×LEVERAGE).
        Returns avg fill price (or `price` on error).
        """
        qty  = self._round_qty(symbol, notional_usd / price)
        side = "BUY" if direction == "LONG" else "SELL"
        result = self._post("/fapi/v1/order", {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": qty,
        })
        if "code" in result:
            print(f"  ⚠️  open_market {symbol}: {result}")
            return price
        # Testnet fills async — avgPrice may be '0.00' on initial response; re-query once
        avg = float(result.get("avgPrice", 0))
        if avg == 0 and "orderId" in result:
            time.sleep(0.3)
            status = self._get("/fapi/v1/order",
                               {"symbol": symbol, "orderId": result["orderId"]})
            avg = float(status.get("avgPrice", 0))
        return avg if avg > 0 else price

    def close_market(self, symbol: str, direction: str, fraction: float = 1.0) -> float:
        """
        Close `fraction` of open position (0.5 = half, 1.0 = all).
        Returns fill price or 0.0 if no position.
        """
        try:
            pos_list = self._get("/fapi/v2/positionRisk", {"symbol": symbol})
            if not isinstance(pos_list, list) or not pos_list:
                return 0.0
            pos_qty = abs(float(pos_list[0].get("positionAmt", 0)))
            if pos_qty <= 0:
                return 0.0
            qty  = self._round_qty(symbol, pos_qty * fraction)
            side = "SELL" if direction == "LONG" else "BUY"
            result = self._post("/fapi/v1/order", {
                "symbol": symbol, "side": side, "type": "MARKET",
                "quantity": qty, "reduceOnly": "true",
            })
            if "code" in result:
                print(f"  ⚠️  close_market {symbol}: {result}")
                return 0.0
            avg = float(result.get("avgPrice", 0))
            if avg == 0 and "orderId" in result:
                time.sleep(0.3)
                status = self._get("/fapi/v1/order",
                                   {"symbol": symbol, "orderId": result["orderId"]})
                avg = float(status.get("avgPrice", 0))
            return avg if avg > 0 else 0.0
        except Exception as e:
            print(f"  ⚠️  close_market {symbol}: {e}")
        return 0.0


# ── Binance data helper ───────────────────────────────────────────────────────

# Single shared public exchange — avoids creating 23+ instances per bar
# (each new instance triggers an exchangeInfo call → rate-limit cascade)
_PUBLIC_EX = ccxt.binanceusdm({"enableRateLimit": True})


def fetch_recent(symbol: str, bars: int = FETCH_BARS) -> pd.DataFrame:
    """Fetch the most recent `bars` 5-min candles from Binance Futures (public)."""
    since = _PUBLIC_EX.milliseconds() - (bars + 20) * 300 * 1000
    raw   = _PUBLIC_EX.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=bars + 20)
    df    = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df    = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    df["ts"] = df["ts"].astype(int)
    df    = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    # Drop the still-forming candle: when polled at boundary+2s the last row is the
    # CURRENT (incomplete) bar — its volume ≈ 0 corrupts volume_ratio (→ vol_ok
    # always fails → 100% CONFIRM_FAIL) AND the composite score. Use only fully
    # closed bars, matching the backtest which iterates closed bars exclusively.
    cur_boundary = (_PUBLIC_EX.milliseconds() // (300 * 1000)) * (300 * 1000)
    if len(df) and int(df["ts"].iloc[-1]) >= cur_boundary:
        df = df.iloc[:-1]
    return df.tail(bars).reset_index(drop=True)


def fetch_4h(symbol: str, bars: int = 260) -> pd.DataFrame:
    """Fetch recent 4h candles (public) for the 200-MA regime classifier."""
    span  = 4 * 3600 * 1000
    since = _PUBLIC_EX.milliseconds() - (bars + 5) * span
    raw   = _PUBLIC_EX.fetch_ohlcv(symbol, "4h", since=since, limit=bars + 5)
    df    = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df    = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    df["ts"] = df["ts"].astype(int)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    # Drop the still-forming 4h bar (regime must use closed bars only)
    cur_b = (_PUBLIC_EX.milliseconds() // span) * span
    if len(df) and int(df["ts"].iloc[-1]) >= cur_b:
        df = df.iloc[:-1]
    return df.reset_index(drop=True)


def wait_for_bar_close(bar_seconds: int = 300) -> datetime:
    """Sleep until the next 5-min bar boundary + 2-second exchange latency buffer."""
    now      = time.time()
    next_bar = (int(now) // bar_seconds + 1) * bar_seconds
    sleep    = next_bar - now + 2.0
    if sleep > 0:
        time.sleep(sleep)
    return datetime.fromtimestamp(next_bar, tz=timezone.utc)


def btc_4h_return(btc_df: pd.DataFrame) -> float:
    """Return over last 48 bars (≈ 4 h at 5-min bars)."""
    if len(btc_df) < BTC_BETA_WINDOW + 1:
        return 0.0
    p_new = float(btc_df["close"].iloc[-1])
    p_old = float(btc_df["close"].iloc[-(BTC_BETA_WINDOW + 1)])
    return (p_new - p_old) / p_old if p_old > 0 else 0.0


# ── Paper trader engine ───────────────────────────────────────────────────────

class PaperTrader:
    """Stateful paper trading engine: polls Binance every 5 min, processes bars."""

    def __init__(self, tokens: list[str], resume: bool = False,
                 testnet_om: "TestnetOrderManager | None" = None):
        self.tokens     = tokens
        self.testnet_om = testnet_om   # None → pure simulation; set → real testnet orders

        # Portfolio-level tracking
        self.balance       = INITIAL_BALANCE
        self.peak          = INITIAL_BALANCE
        self.daily_start   = INITIAL_BALANCE
        self.daily_day     = ""
        self.daily_freeze  = False
        self.size_factor   = 1.0   # 1.0 normal · 0.5 when in -7% throttle zone
        self.daily_sl_count= 0
        self.bar_count     = 0

        # Per-symbol state machines
        self.sym_states: dict[str, SymbolState] = {
            tok: SymbolState(symbol=tok) for tok in tokens
        }
        # FAZ 2: range mean-reversion sleeve (parallel to momentum)
        self.mr_states: dict[str, MRState] = {
            tok: MRState(symbol=tok) for tok in tokens
        }
        self.run_mr_tp = self.run_mr_sl = self.run_mr_tmo = 0
        # 4h regime cache (refreshed when a new 4h bar closes — slow timescale)
        self._regime: dict[str, str] = {tok: "NEUTRAL" for tok in tokens}
        self._regime_4h_ts = 0   # ms of last 4h-bar boundary we refreshed on

        # Trade log (dicts, serialisable)
        self.trade_log: list[dict] = []

        # Stats accumulators (this run only — reset on resume)
        self.run_tp1 = self.run_tp2 = self.run_sl = 0
        self.run_trail = self.run_tmo = 0
        self.run_confirm_ok = self.run_confirm_fail = 0
        # Cumulative drag from probes that never became positions (CONFIRM_FAIL +
        # TEST SL). Tracked separately because it is invisible in the trade log
        # yet accounted for ~54% of the live drawdown.
        self.probe_cost = 0.0
        # Signal-funnel totals across the run — compare against the backtest's
        # confirm rate to detect live/backtest divergence.
        self.funnel_totals = {"scanned": 0, "regime_pass": 0, "probe": 0,
                              "confirm_ok": 0, "confirm_fail": 0, "full": 0,
                              "blocked_max_open": 0}

        # Log file (line-buffered so tail -f works)
        self.log_f = open(LOG_FILE, "a", buffering=1)

        if resume and os.path.exists(STATE_FILE):
            self._load_state()

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        sym_data = {}
        for tok, s in self.sym_states.items():
            sym_data[tok] = {
                "state":         s.state,
                "direction":     s.direction,
                "cooldown":      s.cooldown,
                "test_entry":    s.test_entry,
                "test_sl":       s.test_sl,
                "test_atr":      s.test_atr,
                "full_entry":    s.full_entry,
                "full_sl":       s.full_sl,
                "full_tp1":      s.full_tp1,
                "full_tp2":      s.full_tp2,
                "full_notional": s.full_notional,
                "tp1_hit":       s.tp1_hit,
                "be_price":      s.be_price,
                "trail_best":    s.trail_best,
                "bars_held":     s.bars_held,
            }
        state = {
            "balance":        self.balance,
            "peak":           self.peak,
            "daily_start":    self.daily_start,
            "daily_day":      self.daily_day,
            "daily_freeze":   self.daily_freeze,
            "daily_sl_count": self.daily_sl_count,
            "bar_count":      self.bar_count,
            "trade_log":      self.trade_log,
            "sym_states":     sym_data,
            "mr_states":      {t: {"state": m.state, "entry": m.entry,
                                   "notional": m.notional, "sl": m.sl, "tp": m.tp,
                                   "bars_in": m.bars_in, "cooldown": m.cooldown}
                               for t, m in self.mr_states.items()},
            "regime":         self._regime,
            "regime_4h_ts":   self._regime_4h_ts,
            "funnel_totals":  self.funnel_totals,
            "probe_cost":     round(self.probe_cost, 4),
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self) -> None:
        try:
            with open(STATE_FILE) as f:
                st = json.load(f)
            self.balance        = st["balance"]
            self.peak           = st["peak"]
            self.daily_start    = st["daily_start"]
            self.daily_day      = st["daily_day"]
            self.daily_freeze   = st["daily_freeze"]
            self.daily_sl_count = st["daily_sl_count"]
            self.bar_count      = st["bar_count"]
            self.trade_log      = st.get("trade_log", [])
            self.probe_cost     = st.get("probe_cost", 0.0)
            self.funnel_totals.update(st.get("funnel_totals", {}))
            for tok, saved in st.get("sym_states", {}).items():
                if tok not in self.sym_states:
                    continue
                s = self.sym_states[tok]
                s.state         = saved["state"]
                s.direction     = saved["direction"]
                s.cooldown      = saved["cooldown"]
                s.test_entry    = saved["test_entry"]
                s.test_sl       = saved["test_sl"]
                s.test_atr      = saved["test_atr"]
                s.full_entry    = saved["full_entry"]
                s.full_sl       = saved["full_sl"]
                s.full_tp1      = saved["full_tp1"]
                s.full_tp2      = saved["full_tp2"]
                s.full_notional = saved["full_notional"]
                s.tp1_hit       = saved["tp1_hit"]
                s.be_price      = saved["be_price"]
                s.trail_best    = saved["trail_best"]
                s.bars_held     = saved["bars_held"]
            for tok, m in st.get("mr_states", {}).items():
                if tok in self.mr_states:
                    s2 = self.mr_states[tok]
                    s2.state, s2.entry, s2.notional = m["state"], m["entry"], m["notional"]
                    s2.sl, s2.tp = m["sl"], m["tp"]
                    s2.bars_in, s2.cooldown = m["bars_in"], m["cooldown"]
            self._regime       = st.get("regime", self._regime)
            self._regime_4h_ts = st.get("regime_4h_ts", 0)
            self._log(
                f"✅ Resumed from {STATE_FILE} — "
                f"bar #{self.bar_count}, balance ${self.balance:.2f}"
            )
        except Exception as exc:
            self._log(f"⚠️  Could not load state ({exc}) — starting fresh")

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str, also_print: bool = True) -> None:
        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] {msg}"
        if also_print:
            print(line, flush=True)
        self.log_f.write(line + "\n")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _open_full_count(self) -> int:
        """Count symbols currently in SCALE_OPEN or TRAILING (a full position is open)."""
        return sum(
            1 for s in self.sym_states.values()
            if s.state in (SCALE_OPEN, TRAILING)
        )

    def _print_status(self) -> None:
        """Print a concise status block every N bars."""
        ret  = (self.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        pk_dd= (self.balance - self.peak) / self.peak * 100 if self.peak > 0 else 0.0
        day_dd=(self.balance - self.daily_start) / self.daily_start * 100 \
               if self.daily_start > 0 else 0.0

        closed = [t for t in self.trade_log if t.get("exit_type") not in ("OPEN",)]
        # Position-based: TP1 logs its own record and the post-TP1 leg cannot
        # lose, so counting records inflates WR by ~14 points. See metrics.py.
        pstats = position_stats(aggregate_positions(closed),
                                risk_per_trade=RISK_PER_TRADE_USD)

        open_list = [
            f"  {tok} {s.state}({s.direction}) bars={s.bars_held}"
            for tok, s in self.sym_states.items()
            if s.state != IDLE
        ]

        print("─" * 60, flush=True)
        print(f"  Bar #{self.bar_count:,} | Balance: ${self.balance:.2f} | Return: {ret:+.2f}%", flush=True)
        print(f"  PeakDD: {pk_dd:.1f}%  DayDD: {day_dd:.1f}%  DayFreeze: {self.daily_freeze}", flush=True)
        print(f"  Positions: {pstats['n_positions']}  WR: {pstats['win_rate']:.1f}% "
              f"(BE {pstats['breakeven_wr']}%)  payoff {pstats['payoff']}  "
              f"exp ${pstats['expectancy']:+.2f}  Open: {self._open_full_count()}/{MAX_OPEN}",
              flush=True)
        ft = self.funnel_totals
        _cr = (100 * ft["confirm_ok"] / (ft["confirm_ok"] + ft["confirm_fail"])
               if (ft["confirm_ok"] + ft["confirm_fail"]) else 0.0)
        print(f"  Funnel: probe={ft['probe']} confirm={_cr:.0f}% full={ft['full']} "
              f"maxopen_block={ft['blocked_max_open']} | probe drag: ${self.probe_cost:+.2f}",
              flush=True)
        if open_list:
            print("  Positions:", flush=True)
            for line in open_list:
                print(line, flush=True)
        print("─" * 60, flush=True)

    # ── Regime refresh (4h, slow timescale) ───────────────────────────────────

    def _refresh_regimes(self) -> None:
        """Recompute per-coin 4h regime when a new 4h bar has closed."""
        span  = 4 * 3600 * 1000
        cur_b = (int(time.time() * 1000) // span) * span
        if cur_b == self._regime_4h_ts:
            return                                  # already current for this 4h bar
        try:
            btc_4h = fetch_4h("BTCUSDT")
        except Exception as exc:
            self._log(f"⚠️  BTC 4h fetch failed ({exc}) — keeping previous regimes")
            return
        for tok in self.tokens:
            try:
                self._regime[tok] = regime.current_regime(fetch_4h(tok), btc_4h)
            except Exception as exc:
                self._log(f"  ⚠️  {tok} 4h regime failed ({exc}) — keep prev",
                          also_print=False)
        self._regime_4h_ts = cur_b
        bull = [t for t, r in self._regime.items() if r == "BULL"]
        self._log(f"🧭 Regime refresh — BULL: {bull or '—'} "
                  f"(others throttle longs→0; MR active in NEUTRAL)")

    # ── Bar processing ────────────────────────────────────────────────────────

    def _process_bar(self, bar_dt: datetime) -> None:
        self.bar_count += 1
        day  = bar_dt.strftime("%Y-%m-%d")
        hour = bar_dt.hour

        # ── Daily reset ───────────────────────────────────────────────────────
        if day != self.daily_day:
            self.daily_day       = day
            self.daily_start     = self.balance
            self.daily_freeze    = False
            self.daily_sl_count  = 0
            self._log(f"📅 New day {day} | starting balance ${self.balance:.2f}")

        # ── Peak drawdown hard stop ───────────────────────────────────────────
        if self.balance > self.peak:
            self.peak = self.balance
        peak_dd = (self.balance - self.peak) / self.peak if self.peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            self._log(
                f"🛑 PEAK DD {peak_dd:.1%} hit PEAK_DD_LIMIT ({PEAK_DD_LIMIT:.0%}) "
                f"— hard stop. Saving state."
            )
            self._save_state()
            self._print_status()
            sys.exit(1)

        # ── Equity throttle: halve sizing in a -7% peak drawdown ──────────────
        new_factor = 0.5 if peak_dd <= EQUITY_THROTTLE_DD else 1.0
        if new_factor != self.size_factor:
            self.size_factor = new_factor
            if new_factor < 1.0:
                self._log(f"🔻 THROTTLE on: peak DD {peak_dd:.1%} ≤ "
                          f"{EQUITY_THROTTLE_DD:.0%} — position size ×0.5")
            else:
                self._log(f"🔺 THROTTLE off: peak DD {peak_dd:.1%} recovered — full size")

        # ── Daily DD freeze ───────────────────────────────────────────────────
        intraday_dd = (self.balance - self.daily_start) / self.daily_start \
                      if self.daily_start > 0 else 0.0
        if intraday_dd <= DAILY_DD_LIMIT and not self.daily_freeze:
            self.daily_freeze = True
            self._log(f"⚠️  Daily DD {intraday_dd:.1%} hit — freezing new entries today")

        # ── Session check ─────────────────────────────────────────────────────
        in_session = SESSION_START_UTC <= hour < SESSION_END_UTC

        # ── Fetch BTC 5m reference (beta) + refresh 4h regimes (slow) ─────────
        btc_df = None
        try:
            btc_df = fetch_recent("BTCUSDT", FETCH_BARS)
            btc_df = precompute_indicators(btc_df)
        except Exception as exc:
            self._log(f"⚠️  BTC 5m fetch failed ({exc})")

        self._refresh_regimes()
        n_bull = sum(1 for r in self._regime.values() if r == "BULL")
        gate_str = f"regime: BULL {n_bull}/{len(self.tokens)}"

        # Signal-funnel telemetry for THIS bar. Without it a parameter sweep is a
        # blind shot: we cannot tell whether the live bot trades less than the
        # backtest because the regime differs or because a gate behaves
        # differently in production. Counters are per-bar; totals accumulate.
        funnel = {"scanned": 0, "regime_pass": 0, "probe": 0, "confirm_ok": 0,
                  "confirm_fail": 0, "full": 0, "blocked_max_open": 0}

        # ── Process each symbol ───────────────────────────────────────────────
        for symbol in self.tokens:
            s = self.sym_states[symbol]

            # Fetch token data
            try:
                df = fetch_recent(symbol, FETCH_BARS)
                df = precompute_indicators(df)
            except Exception as exc:
                self._log(f"  ⚠️  {symbol} fetch failed: {exc}")
                continue

            if len(df) < BARS_WARM:
                continue  # not enough history yet

            idx = len(df) - 1
            close = float(df["close"].iloc[idx])
            high  = float(df["high"].iloc[idx])
            low   = float(df["low"].iloc[idx])

            # Hurst on last 40 bars (regime indicator)
            hurst_val = hurst_exponent(df["close"].iloc[max(0, idx - 39): idx + 1])

            # BTC row alignment (find matching timestamp for beta calc)
            btc_row = None
            if btc_df is not None:
                ts_now   = int(df["ts"].iloc[idx])
                btc_idxs = btc_df.index[btc_df["ts"] == ts_now]
                btc_row  = int(btc_idxs[0]) if len(btc_idxs) > 0 else None

            snap = build_snapshot(df, idx, symbol,
                                  btc_df=btc_df,
                                  hurst_override=hurst_val,
                                  btc_row=btc_row)
            rsi_val   = snap["indicators"]["rsi_14"]
            vol_ratio = snap["volume"]["volume_ratio"]

            # ── Regime (Faz 1/4c): longs ONLY in BULL (size_mult>0) ───────────
            reg       = self._regime.get(symbol, "NEUTRAL")
            size_mult = regime.LONG_SIZE_MULT.get(reg, 0.0) * self.size_factor
            block_new = self.daily_freeze or not in_session

            # ── Momentum sleeve — runs only if not gated (BULL + open) ────────
            funnel["scanned"] += 1
            if size_mult > 0 and not block_new:
                funnel["regime_pass"] += 1

            events = []
            if not (s.state == IDLE and (block_new or size_mult <= 0)):
                open_full      = self._open_full_count()
                block_new_full = (s.state == TEST_OPEN and open_full >= MAX_OPEN)
                if block_new_full:
                    funnel["blocked_max_open"] += 1
                events = s.process_bar(snap, high, low, rsi_val, vol_ratio,
                                       block_new_full=block_new_full,
                                       size_mult=size_mult)

            for ev in events:
                self.balance += ev.pnl
                tn = self.testnet_om  # shorthand

                if ev.exit_type == "OPEN":
                    if ev.kind == "TEST":
                        funnel["probe"] += 1
                        self._log(
                            f"  🔬 TEST OPEN  {ev.symbol} {ev.direction} "
                            f"@ {ev.entry:.5g} | bal=${self.balance:.2f}"
                        )
                        # ── Testnet: place $20 market order ──────────────
                        if tn:
                            fill = tn.open_market(ev.symbol, ev.direction,
                                                   TEST_SIZE_USD, ev.entry)
                            self._log(
                                f"     ↳ [TESTNET] TEST order filled @ {fill:.5g}",
                                also_print=False)
                    else:
                        funnel["full"] += 1
                        funnel["confirm_ok"] += 1
                        # FIX #1: use the strategy's dynamic, regime+throttle-aware
                        # notional (risk-based sizing) — NOT a flat FULL_SIZE_USD×LEVERAGE.
                        notional = s.full_notional
                        self._log(
                            f"  📈 FULL OPEN  {ev.symbol} {ev.direction} "
                            f"@ {ev.entry:.5g} | notional=${notional:.0f} "
                            f"| bal=${self.balance:.2f}"
                        )
                        # ── Testnet: place risk-sized notional market order ─────
                        if tn:
                            fill = tn.open_market(ev.symbol, ev.direction,
                                                   notional, ev.entry)
                            self._log(
                                f"     ↳ [TESTNET] FULL order filled @ {fill:.5g}",
                                also_print=False)
                    continue

                # ── Test position result ───────────────────────────────────
                if ev.kind == "TEST":
                    if ev.exit_type == "CONFIRM_OK":
                        self.run_confirm_ok += 1
                        self._log(
                            f"  ✅ CONFIRMED  {ev.symbol} {ev.direction} "
                            f"pnl=${ev.pnl:+.3f} | bal=${self.balance:.2f}"
                        )
                        # ── Testnet: close the test position ──────────────
                        if tn:
                            fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                            self._log(
                                f"     ↳ [TESTNET] TEST closed @ {fill:.5g}",
                                also_print=False)
                    elif ev.exit_type == "CONFIRM_FAIL":
                        self.run_confirm_fail += 1
                        funnel["confirm_fail"] += 1
                        # Probes that never became positions still cost money.
                        # On the live record this drag was ~54% of the total loss,
                        # so it is tracked explicitly rather than inferred.
                        self.probe_cost += ev.pnl
                        self._log(
                            f"  ❌ CONF FAIL  {ev.symbol} {ev.direction} "
                            f"pnl=${ev.pnl:+.3f} | bal=${self.balance:.2f}"
                        )
                        # ── Testnet: close failed test ────────────────────
                        if tn:
                            fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                            self._log(
                                f"     ↳ [TESTNET] TEST closed @ {fill:.5g}",
                                also_print=False)
                    elif ev.exit_type == "SL":
                        # Probe stopped out before it could be confirmed — same
                        # category of cost as a CONFIRM_FAIL: paid, never traded.
                        self.probe_cost += ev.pnl
                        self._log(
                            f"  🛑 TEST SL    {ev.symbol} pnl=${ev.pnl:+.3f} "
                            f"| bal=${self.balance:.2f}"
                        )
                    continue

                # ── Full trade closed ─────────────────────────────────────
                emoji = "✅" if ev.pnl > 0 else "❌"
                self._log(
                    f"  {emoji} CLOSE FULL  {ev.symbol} [{ev.exit_type}] "
                    f"{ev.direction} entry={ev.entry:.5g} exit={ev.exit:.5g} "
                    f"pnl=${ev.pnl:+.2f} | bal=${self.balance:.2f}"
                )

                # ── Testnet: execute the close ─────────────────────────────
                if tn:
                    if ev.exit_type == "TP1":
                        # Close half position at TP1
                        fill = tn.close_market(ev.symbol, ev.direction, 0.5)
                        self._log(
                            f"     ↳ [TESTNET] TP1 half-close @ {fill:.5g}",
                            also_print=False)
                    else:
                        # Close entire remaining position (TP2/SL/TRAIL/TIMEOUT)
                        fill = tn.close_market(ev.symbol, ev.direction, 1.0)
                        self._log(
                            f"     ↳ [TESTNET] {ev.exit_type} full-close @ {fill:.5g}",
                            also_print=False)

                # Update counters
                if ev.exit_type == "TP1":    self.run_tp1   += 1
                elif ev.exit_type == "TP2":  self.run_tp2   += 1
                elif ev.exit_type == "SL":
                    self.run_sl   += 1
                    self.daily_sl_count += 1
                    if self.daily_sl_count >= DAILY_SL_LIMIT:
                        self.daily_freeze = True
                        self._log(f"  ⚠️  Daily SL limit reached — freezing {symbol} entries today")
                elif ev.exit_type == "TRAIL":  self.run_trail += 1
                elif ev.exit_type == "TIMEOUT":self.run_tmo   += 1

                # Append to trade log
                self.trade_log.append({
                    "ts":        bar_dt.isoformat(),
                    "symbol":    ev.symbol,
                    "direction": ev.direction,
                    "exit_type": ev.exit_type,
                    "entry":     ev.entry,
                    "exit":      ev.exit,
                    "pnl":       round(ev.pnl, 4),
                    "balance":   round(self.balance, 4),
                })

            # ── FAZ 2: range mean-reversion sleeve (NEUTRAL-only) ─────────────
            if MR_ENABLED:
                mr = self.mr_states[symbol]
                # NETTING GUARD (Gemini risk-audit): don't open MR on a symbol that
                # already has an active momentum position — the exchange would net
                # both LONGs into one, and an MR close would shut the momentum leg.
                mr_block = block_new or (s.state != IDLE)
                mr_events = mr.process_bar(
                    snap, high, low, regime=reg, block_new=mr_block,
                    size_mult=self.size_factor)   # FIX #3: equity-throttle aware
                tn = self.testnet_om
                for ev in mr_events:
                    self.balance += ev.pnl
                    if ev.exit_type == "OPEN":
                        self._log(f"  🔁 MR OPEN    {ev.symbol} @ {ev.entry:.5g} "
                                  f"| notional=${mr.notional:.0f} | bal=${self.balance:.2f}")
                        # FIX #2: MR was sim-only — now mirror to testnet (LONG-only).
                        # NETTING CAVEAT: if a momentum FULL long is already open on this
                        # symbol the exchange nets both into one position (rare: momentum
                        # fires in BULL, MR in NEUTRAL — regimes seldom overlap same-bar).
                        # Flagged for the exec-audit role before any live promotion.
                        if tn:
                            fill = tn.open_market(ev.symbol, "LONG",
                                                  mr.notional, ev.entry)
                            self._log(f"     ↳ [TESTNET] MR order filled @ {fill:.5g}",
                                      also_print=False)
                        continue
                    emoji = "✅" if ev.pnl > 0 else "❌"
                    self._log(f"  {emoji} MR {ev.exit_type:<4}  {ev.symbol} "
                              f"entry={ev.entry:.5g} exit={ev.exit:.5g} "
                              f"pnl=${ev.pnl:+.2f} | bal=${self.balance:.2f}")
                    # FIX #2: close the testnet MR position (full)
                    if tn:
                        fill = tn.close_market(ev.symbol, "LONG", 1.0)
                        self._log(f"     ↳ [TESTNET] MR {ev.exit_type} close @ {fill:.5g}",
                                  also_print=False)
                    if   ev.exit_type == "TP":      self.run_mr_tp  += 1
                    elif ev.exit_type == "SL":      self.run_mr_sl  += 1
                    elif ev.exit_type == "TIMEOUT": self.run_mr_tmo += 1
                    self.trade_log.append({
                        "ts": bar_dt.isoformat(), "symbol": ev.symbol,
                        "direction": "MR", "exit_type": ev.exit_type,
                        "entry": ev.entry, "exit": ev.exit,
                        "pnl": round(ev.pnl, 4), "balance": round(self.balance, 4),
                    })

        # ── Bar summary ───────────────────────────────────────────────────────
        ret_pct = (self.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        active  = [
            f"{t}:{s.state[0]}"  # T=TEST_OPEN, S=SCALE_OPEN, R=TRAILING
            for t, s in self.sym_states.items()
            if s.state != IDLE
        ]
        active_str = " ".join(active) if active else "—"
        self._log(
            f"Bar #{self.bar_count:,} {bar_dt.strftime('%H:%M')} UTC | "
            f"${self.balance:.2f} ({ret_pct:+.2f}%) | "
            f"open={self._open_full_count()}/{MAX_OPEN} [{active_str}] | "
            f"{gate_str}"
        )

        for k, v in funnel.items():
            self.funnel_totals[k] += v
        # Only log the funnel on bars where something actually happened, so the
        # log stays readable but every probe/confirm decision leaves a trace.
        if any(funnel[k] for k in ("probe", "confirm_ok", "confirm_fail", "blocked_max_open")):
            ft = self.funnel_totals
            cr = (100 * ft["confirm_ok"] / (ft["confirm_ok"] + ft["confirm_fail"])
                  if (ft["confirm_ok"] + ft["confirm_fail"]) else 0.0)
            self._log(
                f"  📊 funnel bar[scan={funnel['scanned']} regime_ok={funnel['regime_pass']} "
                f"probe={funnel['probe']} conf_ok={funnel['confirm_ok']} "
                f"conf_fail={funnel['confirm_fail']} full={funnel['full']} "
                f"maxopen_block={funnel['blocked_max_open']}] | "
                f"total[probe={ft['probe']} conf={cr:.0f}% full={ft['full']}] | "
                f"probe_cost=${self.probe_cost:+.2f}"
            )

        # Save state every bar
        self._save_state()

        # Detailed status every 12 bars (≈ 1 hour)
        if self.bar_count % 12 == 0:
            self._print_status()

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        mode = "TESTNET live orders" if self.testnet_om else "local simulation"
        self._log("=" * 60)
        self._log(f"BreakoutBot Paper Trader — {len(self.tokens)} symbols | {mode}")
        self._log(
            f"Balance: ${self.balance:.2f} | MAX_OPEN: {MAX_OPEN} | "
            f"Session: {SESSION_START_UTC:02d}–{SESSION_END_UTC:02d} UTC"
        )
        if self.testnet_om:
            tb = self.testnet_om.get_balance()
            self._log(f"Testnet account balance: ${tb:.2f} USDT")
        self._log("=" * 60)

        while True:
            try:
                bar_dt = wait_for_bar_close()
                self._process_bar(bar_dt)
            except KeyboardInterrupt:
                self._log("\nStopped by user (Ctrl-C).")
                self._save_state()
                self._print_status()
                self.log_f.close()
                break
            except Exception as exc:
                self._log(f"❌ Unhandled error: {exc}")
                import traceback
                traceback.print_exc()
                time.sleep(30)   # brief pause before retrying


# ── Status-only mode ──────────────────────────────────────────────────────────

def print_saved_status() -> None:
    """Read state_paper.json and pretty-print the current status."""
    if not os.path.exists(STATE_FILE):
        print(f"No state file found ({STATE_FILE}). Run paper_bb.py first.")
        return
    with open(STATE_FILE) as f:
        st = json.load(f)

    balance    = st["balance"]
    initial    = INITIAL_BALANCE
    ret_pct    = (balance - initial) / initial * 100
    peak       = st["peak"]
    peak_dd    = (balance - peak) / peak * 100 if peak > 0 else 0.0
    bar_count  = st["bar_count"]
    daily_day  = st["daily_day"]
    daily_freeze = st["daily_freeze"]
    trade_log  = st.get("trade_log", [])

    closed = [t for t in trade_log if t.get("exit_type") not in ("OPEN",)]
    ps     = position_stats(aggregate_positions(closed),
                            risk_per_trade=RISK_PER_TRADE_USD)

    print("=" * 60)
    print(f"  BreakoutBot Paper Status")
    print(f"  Day: {daily_day} | Bar: #{bar_count:,} | Freeze: {daily_freeze}")
    print(f"  Balance: ${balance:.2f} ({ret_pct:+.2f}%) | PeakDD: {peak_dd:.1f}%")
    print(f"  Positions: {ps['n_positions']} ({ps['n_wins']}W/{ps['n_losses']}L) | "
          f"WR: {ps['win_rate']}% (break-even {ps['breakeven_wr']}%) | PF: {ps['profit_factor']}")
    print(f"  Avg win/loss: ${ps['avg_win']:+.2f}/${ps['avg_loss']:+.2f} | "
          f"payoff {ps['payoff']} | expectancy ${ps['expectancy']:+.2f}/pos")
    ft = st.get("funnel_totals") or {}
    if ft:
        ok, fail = ft.get("confirm_ok", 0), ft.get("confirm_fail", 0)
        cr = 100 * ok / (ok + fail) if (ok + fail) else 0.0
        print(f"  Funnel: probe={ft.get('probe',0)} confirm={cr:.0f}% "
              f"full={ft.get('full',0)} maxopen_block={ft.get('blocked_max_open',0)} | "
              f"probe drag: ${st.get('probe_cost', 0.0):+.2f}")
    print("=" * 60)

    # Open positions
    sym_states = st.get("sym_states", {})
    active = {t: s for t, s in sym_states.items() if s["state"] != IDLE}
    if active:
        print("  Open positions:")
        for tok, s in active.items():
            print(f"    {tok}: {s['state']} | dir={s['direction']} "
                  f"| entry={s['full_entry']:.5g} | bars={s['bars_held']}")
    else:
        print("  No open positions")

    # Recent trades
    if trade_log:
        print(f"\n  Last {min(10, len(trade_log))} full trades:")
        for t in trade_log[-10:]:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            print(f"    {emoji} {t['ts'][:16]} {t['symbol']:15s} [{t['exit_type']:<8}] "
                  f"pnl=${t['pnl']:+.2f} → bal=${t['balance']:.2f}")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="BreakoutBot Paper Trader")
    ap.add_argument("--tokens", nargs="+", default=None,
                    help="Subset of tokens to trade (default: all from config.py)")
    ap.add_argument("--resume", action="store_true",
                    help="Resume from saved state_paper.json")
    ap.add_argument("--status", action="store_true",
                    help="Print current paper trading status and exit")
    ap.add_argument("--testnet", action="store_true",
                    help="Place real orders on Binance Futures Testnet (requires secrets_local.py)")
    args = ap.parse_args()

    if args.status:
        print_saved_status()
        return

    tokens = args.tokens if args.tokens else TOKENS
    unknown = [t for t in tokens if t not in TOKENS]
    if unknown:
        print(f"⚠️  Unknown tokens (not in config.py): {unknown}")

    # ── Testnet setup ─────────────────────────────────────────────────────────
    testnet_om = None
    if args.testnet:
        try:
            from secrets_local import TESTNET_API_KEY, TESTNET_API_SECRET
        except ImportError:
            print("❌ secrets_local.py bulunamadı.")
            print("   1. cp secrets_local.py'yi oluştur ve API key'leri gir")
            print("   2. https://testnet.binancefuture.com → API Management")
            sys.exit(1)

        if TESTNET_API_KEY == "BURAYA_KOPYALA":
            print("❌ secrets_local.py içindeki API key henüz doldurulmamış.")
            print("   https://testnet.binancefuture.com → API Management → Generate Key")
            sys.exit(1)

        print("🔗 Binance Futures Testnet'e bağlanılıyor…")
        testnet_om = TestnetOrderManager(TESTNET_API_KEY, TESTNET_API_SECRET)
        bal = testnet_om.get_balance()
        print(f"   Testnet bakiyesi: ${bal:.2f} USDT")
        print(f"   {len(tokens)} sembol için kaldıraç + margin ayarlanıyor…")
        testnet_om.setup_symbols(tokens)
        print("   ✅ Testnet hazır — emirler testnet'e gidecek\n")

    trader = PaperTrader(tokens=tokens, resume=args.resume, testnet_om=testnet_om)
    trader.run()


if __name__ == "__main__":
    main()
