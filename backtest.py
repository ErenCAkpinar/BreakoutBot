"""
BreakoutBot Backtest

Replays the deployed system on historical 5m OHLCV data.

Two modes:

  portfolio (default) — ONE shared account across all tokens, bar-synchronised,
      with MAX_OPEN, the equity throttle and the drawdown guards applied to the
      book. This is what paper_bb.py does live, and it is the only mode whose
      numbers describe the real account.

  --per-symbol        — one independent account per token, no portfolio cap.
      Useful for RANKING coins against each other (that is what the curation in
      config.TOKENS was built on); it is not a picture of the deployed system.

Every gate in both modes mirrors paper_bb.PaperTrader._process_bar — see the
PARITY CONTRACT block below, which cites the live line for each one.

Usage:
    python3.12 backtest.py                          # live 5-coin book, 90d
    python3.12 backtest.py --days 240               # deeper window
    python3.12 backtest.py --per-symbol --beta      # per-coin ranking + beta
    python3.12 backtest.py --all --days 240         # full 23-coin universe
    python3.12 backtest.py --cache                  # pin/reuse the window on disk
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd

from indicators import build_snapshot, token_beta_vs_btc, hurst_exponent, precompute_indicators
from strategy   import SymbolState, IDLE, TEST_OPEN, SCALE_OPEN, TRAILING
from mean_reversion import MRState
from short_sleeve import ShortState
import regime
from config import MR_ENABLED, SHORT_ENABLED
from config import (
    TOKENS, TIMEFRAME, INITIAL_BALANCE,
    DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT, DAILY_SL_LIMIT,
    SESSION_START_UTC, SESSION_END_UTC,
    MAX_OPEN, RISK_PER_TRADE_USD,
    BTC_GATE_ENABLED, BTC_BETA_WINDOW,
    BTC_BETA_BOOST, BTC_BETA_BLOCK,
)
from metrics import aggregate_positions, position_stats, exit_distribution
from config import REGIME_WARMUP_DAYS, REGIME_WARMUP_BARS, RISK_BY_SLEEVE
from config import _FULL_UNIVERSE

CACHE_DIR = "backtests/data"    # shared with bench.py (same file naming)


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_history(symbol: str, days: int, silent: bool = False) -> pd.DataFrame:
    """Fetch historical OHLCV from Binance Futures (paginated, 1000 bars/call)."""
    exchange   = ccxt.binanceusdm({"enableRateLimit": True})
    bar_sec    = 300           # 5m = 300 seconds
    limit      = 1000
    total      = days * 24 * 3600 // bar_sec
    all_bars:  list = []
    since      = exchange.milliseconds() - total * bar_sec * 1000

    if not silent:
        print(f"  Fetching {days}d of {symbol} 5m data ({total:,} bars)…", flush=True)
    while len(all_bars) < total:
        batch = exchange.fetch_ohlcv(symbol, TIMEFRAME, since=since, limit=limit)
        if not batch:
            break
        all_bars.extend(batch)
        since = batch[-1][0] + bar_sec * 1000
        if not silent:
            print(f"  … {len(all_bars):,}/{total:,} bars", end="\r", flush=True)
        if len(batch) < limit:
            break
        time.sleep(0.25)
    if not silent:
        print()

    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})
    df["ts"] = df["ts"].astype(int)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if not silent:
        print(f"  ✅ Fetched {len(df):,} bars", flush=True)
    return df


# ── Beta ranking ──────────────────────────────────────────────────────────────

def print_beta_ranking(tokens: list[str], days: int, btc_df: pd.DataFrame,
                       prefetched: dict[str, pd.DataFrame] | None = None) -> None:
    """Compute and print each token's beta vs BTC over the test period.

    prefetched : dict of {symbol → DataFrame} from the backtest run (no re-fetch).
    """
    print(f"\n{'═'*56}")
    print(f"  TOKEN BETA vs BTC  ({days}d, {BTC_BETA_WINDOW}-bar rolling)")
    print(f"  β>2 = amplifies BTC 2×   β<0 = moves opposite to BTC")
    print(f"{'─'*56}")
    print(f"  {'Token':<18} {'Beta':>6}  {'Interpretation'}")
    print(f"{'─'*56}")

    betas: list[tuple[str, float]] = []
    for tok in tokens:
        if tok == "BTCUSDT":
            betas.append((tok, 1.0))
            continue
        try:
            # Reuse already-fetched data if available (avoids double network calls)
            if prefetched and tok in prefetched:
                tok_df = prefetched[tok]
            else:
                tok_df = fetch_history(tok, days, silent=True)
            merged = pd.merge(
                tok_df[["ts", "close"]].rename(columns={"close": "tok"}),
                btc_df[["ts", "close"]].rename(columns={"close": "btc"}),
                on="ts",
            )
            if len(merged) < 50:
                continue
            beta = token_beta_vs_btc(
                merged["tok"], merged["btc"], window=BTC_BETA_WINDOW
            )
            betas.append((tok, beta))
        except Exception:
            pass

    betas.sort(key=lambda x: x[1], reverse=True)
    for tok, beta in betas:
        if beta > BTC_BETA_BLOCK:
            label = f"🔥 HIGH BETA (amplifies {beta:.1f}×, BLOCK in BTC down)"
        elif beta > BTC_BETA_BOOST:
            label = f"⚡ BOOST ZONE (amplifies {beta:.1f}×)"
        elif beta > 1.0:
            label = f"✅ tracks BTC ({beta:.1f}×)"
        elif beta > 0:
            label = f"⚠️  weak tracking ({beta:.1f}×)"
        else:
            label = f"❌ inverse/uncorrelated ({beta:.1f}×)"
        print(f"  {tok:<18} {beta:>6.2f}  {label}")
    print(f"{'═'*56}\n")


# ── Live-parity core ──────────────────────────────────────────────────────────
#
# PARITY CONTRACT
# ---------------
# paper_bb.PaperTrader._process_bar is the reference implementation — it is the
# code actually running the account. Everything in this section reproduces its
# gate ORDER and its gate CONDITIONS exactly, with the mirrored line cited per
# gate. A change on either side that is not mirrored here turns every backtest
# number into a fiction, which is the whole failure mode this section exists to
# prevent.
#
# Differences that CANNOT be reproduced offline (and are immaterial):
#   · live re-fetches a rolling 200-bar frame per symbol per bar, so its EWM
#     indicators warm from 200 bars while the backtest warms from the whole
#     window. Every EWM span here is <= 50, so the two converge well inside the
#     65-bar warm-up.
#   · live drops the still-forming candle (paper_bb.fetch_recent); the backtest
#     only ever sees closed bars. Same thing by construction.
#   · live can miss a bar on a fetch error and skip that symbol. The backtest
#     skips a symbol whose frame has no row for the bar — same handling.
#
# Difference that is REAL and deliberate: the short sleeve. paper_bb does not
# import short_sleeve at all, so the live bot cannot take a short. run_symbol
# still runs it when config.SHORT_ENABLED is on (research path) and warns loudly.

# ── Exit parameters actually deployed ─────────────────────────────────────────
# These used to live ONLY in the systemd unit as env overrides while config.py
# defaulted to something else, so a bare `python3.12 backtest.py` silently tested
# a different exit structure than the account was running. As of 2026-08-27 the
# validated set (arm R1-sl225t96) IS the config.py default, so this table exists
# to catch the reverse drift: a stale env still pinned in the unit.
#
# ⚠️ The unit is expected to carry `X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5` from
#    the E6 era. X_TRAIL_ATR=2.5 now OVERRIDES the adopted 3.75 — that env has to
#    be removed from the unit, or the live bot runs a mix nothing validated.
LIVE_ENV = {
    "X_TP1_CLOSE_FRAC": 0.0,
    "X_TRAIL_ATR":      3.75,
    "X_SL_FULL_ATR":    2.25,
    "X_TP1_ATR":        3.0,
    "X_TP2_ATR":        6.0,
    "X_TIMEOUT_BARS":   96.0,
}


def check_live_params() -> None:
    """Print the exit params in force and flag any that differ from production."""
    import config as _cfg
    current = {"X_TP1_CLOSE_FRAC": _cfg.TP1_CLOSE_FRAC,
               "X_TRAIL_ATR":      _cfg.TRAIL_ATR,
               "X_SL_FULL_ATR":    _cfg.SL_FULL_ATR,
               "X_TP1_ATR":        _cfg.TP1_ATR,
               "X_TP2_ATR":        _cfg.TP2_ATR,
               "X_TIMEOUT_BARS":   float(_cfg.TIMEOUT_BARS)}
    drift = {k: (v, LIVE_ENV[k]) for k, v in current.items()
             if abs(v - LIVE_ENV[k]) > 1e-9}

    print(f"⚙️  Exits: TP1_CLOSE_FRAC={_cfg.TP1_CLOSE_FRAC:g} "
          f"TRAIL_ATR={_cfg.TRAIL_ATR:g} SL_FULL_ATR={_cfg.SL_FULL_ATR:g} "
          f"TP1_ATR={_cfg.TP1_ATR:g} TP2_ATR={_cfg.TP2_ATR:g} "
          f"TIMEOUT_BARS={_cfg.TIMEOUT_BARS}")
    print(f"   Risk: ${_cfg.RISK_PER_TRADE_USD:g}/trade  MAX_OPEN={_cfg.MAX_OPEN}  "
          f"MR={'on' if _cfg.MR_ENABLED else 'off'} (${_cfg.MR_RISK_PER_TRADE_USD:g})  "
          f"shorts={'ON — NOT LIVE' if _cfg.SHORT_ENABLED else 'off'}  "
          f"longs {regime.LONG_SIZE_MULT}")
    if drift:
        print("  ⚠️  These are NOT the deployed exits. The live bot runs:")
        for k, (have, want) in drift.items():
            print(f"        {k}={want:g}   (this run: {have:g})")
        print("      Re-run as:  "
              + " ".join(f"{k}={v:g}" for k, v in LIVE_ENV.items())
              + " python3.12 backtest.py …")
    else:
        print("  ✅ matches the deployed E6 exit set")


# PEAK_DD_LIMIT ends a run: live, paper_bb calls sys.exit and a human decides
# whether to restart. A backtest cannot model that decision, so by default it
# stops too — and MUST then report the window it actually replayed, not the one
# that was requested. BT_RESTARTS=1 models "the operator restarts the bot after
# every hard stop" (peak resets to the current balance, the run continues) so a
# multi-year study is not truncated by the first bad quarter. It is NOT live
# parity: it assumes someone always restarts, which is itself a decision.
_RESTARTS     = os.getenv("BT_RESTARTS") == "1"
_NO_REGIME    = os.getenv("BT_NO_REGIME") == "1"           # ablation: no regime gate
_HURST_STRIDE = max(1, int(os.getenv("BT_HURST_STRIDE", "1")))


def _row_for_ts(ts_arr: np.ndarray, ts: int) -> int | None:
    """Row index of `ts` in a sorted ts column, or None when that bar is missing.

    Replaces a per-symbol {ts: row} dict. Those cost ~60 MB per 615k-bar frame,
    ~370 MB across six frames — most of the footprint that killed a 6-year
    portfolio run. A binary search on the array we already hold costs nothing
    next to the ~2.4 ms each symbol-bar spends in Hurst and the snapshot.
    """
    if ts_arr.size == 0:
        return None
    i = int(np.searchsorted(ts_arr, ts))
    if i >= ts_arr.size or int(ts_arr[i]) != ts:
        return None
    return i


def _bar_close_dt(ts_ms: int) -> datetime:
    """UTC time the 5m bar stamped `ts_ms` CLOSED at.

    Binance stamps a candle with its OPEN time. The live bot wakes on the bar
    boundary and processes the candle that just closed (paper_bb.wait_for_bar_close
    returns that boundary), so its session filter and its daily reset key off the
    CLOSE time. Keying off the open time shifts every session edge and every day
    rollover by one bar.
    """
    return datetime.fromtimestamp((ts_ms + 300_000) / 1000, tz=timezone.utc)


def _hurst_at(closes: pd.Series, i: int, cache: dict[int, float]) -> float:
    """Hurst over the 40 closes ending at bar `i`.

    Live recomputes this every single bar (paper_bb.py:651). The backtest used to
    reuse a value up to 4 bars stale, and Hurst is an ENTRY GATE
    (strategy.py:119, HURST_LONG_MIN) — a stale value silently opens and blocks
    different trades than production. Stride is 1 (exact) by default;
    BT_HURST_STRIDE>1 buys speed on a sweep at the cost of that parity.
    """
    key = i - (i % _HURST_STRIDE)
    val = cache.get(key)
    if val is None:
        val = hurst_exponent(closes.iloc[max(0, key - 39): key + 1])
        cache[key] = val
    return val


def _leg(ev, sleeve: str, exit_type: str | None = None,
         ts: int | None = None) -> dict:
    """One balance-moving leg, shaped exactly like paper_bb._log_leg writes it.

    T0 — every leg that moves the balance must land here, OPEN legs included.
    Their pnl is the entry fee; dropping them is what made the live trade log
    disagree with the balance by -$33 and flipped the published expectancy's sign.
    aggregate_positions() ignores exit_type "OPEN", so logging them changes no
    position count — it only makes the money add up.
    """
    return {
        "ts":        ts,
        "symbol":    ev.symbol,
        "direction": ev.direction,
        "sleeve":    sleeve,
        "kind":      getattr(ev, "kind", ""),
        "exit_type": exit_type if exit_type is not None else ev.exit_type,
        "entry":     ev.entry,
        "exit":      ev.exit,
        "pnl":       ev.pnl,
    }


def _mom_leg(ev, ts: int | None = None) -> dict:
    """Route a momentum-sleeve event to its sleeve the way paper_bb does.

    A probe that stops out carries exit_type "SL". Left alone, metrics.py reads it
    as a MOMENTUM final leg and lets it close an unrelated pending TP1. The live
    bot renames it PROBE_SL (paper_bb.py:753); so does this.
    """
    if getattr(ev, "kind", "") == "TEST":
        return _leg(ev, "PROBE",
                    "PROBE_SL" if ev.exit_type == "SL" else ev.exit_type, ts)
    return _leg(ev, "MOMENTUM", None, ts)


def _progress(done: int, total: int, t0: float, bal: float, extra: str = "") -> None:
    """One-line progress for the bar loop.

    The loop is silent for ~14 minutes on a 240d 5-coin run (2.4 ms per
    symbol-bar × 345k symbol-bars, most of it the per-bar Hurst that parity
    requires). Silence that long is indistinguishable from a hang, so it reports.
    Carriage-return in a terminal, plain lines when redirected to a file.
    """
    frac = done / total if total else 1.0
    el   = time.time() - t0
    eta  = (el / frac - el) if frac > 0 else 0.0
    line = (f"  … {frac*100:5.1f}%  bar {done:,}/{total:,}  "
            f"${bal:,.2f}  ETA {eta/60:4.1f}m{extra}")
    if sys.stdout.isatty():
        print(f"{line}    ", end="\r", flush=True)
    else:
        print(line, flush=True)


def new_tally() -> dict:
    """Counters both backtest modes share (the live bot keeps them as attributes)."""
    return {
        "probe": 0, "full": 0, "confirm_ok": 0, "confirm_fail": 0,
        "maxopen_block": 0, "probe_cost": 0.0,
        "TP1": 0, "TP2": 0, "SL": 0, "TRAIL": 0, "TIMEOUT": 0,
        "MR_TP": 0, "MR_SL": 0, "MR_TIMEOUT": 0,
    }


def process_symbol_bar(sym_state: SymbolState, mr_state: MRState, snap: dict,
                       high: float, low: float, *,
                       reg: str, size_factor: float, block_new: bool,
                       open_full: int, rsi_val: float, vol_ratio: float
                       ) -> tuple[list, list, dict]:
    """One symbol, one bar — the exact gate sequence paper_bb._process_bar runs.

    Returns (momentum_events, mr_events, info).

    Gate-by-gate, against the live bot:

      size_mult   = LONG_SIZE_MULT[reg] * size_factor        paper_bb.py:665-666
                    LONG_SIZE_MULT is {BULL: 1.0, NEUTRAL: 0, BEAR: 0}, and the
                    default on an unknown label is 0.0 (live) — NOT 1.0.

      momentum    skipped entirely while IDLE if block_new OR size_mult <= 0
                                                              paper_bb.py:673
                    This is the fix that matters most. The backtest used to gate
                    on block_new only, so in NEUTRAL/BEAR it opened $20 probes the
                    live bot never opens — paying two sides of fees, occasionally
                    a probe SL, and parking the symbol in a 10-bar cooldown — then
                    hit strategy.py:178 and refused the full position anyway. Pure
                    manufactured drag, on ~all bars outside BULL.

      block_new_full = (state == TEST_OPEN and open_full >= MAX_OPEN)
                                                              paper_bb.py:675-676
                    The portfolio cap. Never binds in a single-symbol run.

      MR          blocked by block_new OR an open momentum position on the same
                  symbol (the netting guard), sized by the equity throttle
                                                              paper_bb.py:828-833
                    Evaluated AFTER momentum has processed the bar, so it sees the
                    post-momentum state — same as live.
    """
    mult      = 1.0 if _NO_REGIME else regime.LONG_SIZE_MULT.get(reg, 0.0)
    size_mult = mult * size_factor

    mom_events: list = []
    block_new_full = False
    if not (sym_state.state == IDLE and (block_new or size_mult <= 0)):
        block_new_full = (sym_state.state == TEST_OPEN and open_full >= MAX_OPEN)
        mom_events = sym_state.process_bar(snap, high, low, rsi_val, vol_ratio,
                                           block_new_full=block_new_full,
                                           size_mult=size_mult)

    mr_events: list = []
    if MR_ENABLED:
        mr_block  = block_new or (sym_state.state != IDLE)
        mr_events = mr_state.process_bar(snap, high, low, regime=reg,
                                         block_new=mr_block,
                                         size_mult=size_factor)

    return mom_events, mr_events, {"size_mult": size_mult,
                                   "block_new_full": block_new_full}


def account_events(mom_events: list, mr_events: list,
                   legs: list[dict], tally: dict,
                   ts: int | None = None) -> float:
    """Log every leg, bump the tally, return the balance delta for this bar.

    Both backtest modes go through this one function so they cannot drift apart,
    and every leg reaches `legs` so sum(legs.pnl) == balance change (T0).
    """
    delta = 0.0

    for ev in mom_events:
        delta += ev.pnl
        legs.append(_mom_leg(ev, ts))
        if ev.exit_type == "OPEN":
            tally["probe" if ev.kind == "TEST" else "full"] += 1
            continue
        if ev.kind == "TEST":
            # Probe outcomes. CONFIRM_OK rolls into a full position; the other two
            # are money paid for a position that never existed — the live bot
            # tracks that drag explicitly (paper_bb.probe_cost) because it was 54%
            # of the total loss on the live record.
            if ev.exit_type == "CONFIRM_OK":
                tally["confirm_ok"] += 1
            elif ev.exit_type == "CONFIRM_FAIL":
                tally["confirm_fail"] += 1
                tally["probe_cost"]  += ev.pnl
            elif ev.exit_type == "SL":
                tally["probe_cost"]  += ev.pnl
            continue
        tally[ev.exit_type] = tally.get(ev.exit_type, 0) + 1

    for ev in mr_events:
        delta += ev.pnl
        legs.append(_leg(ev, "MR", None, ts))
        if ev.exit_type == "OPEN":
            continue
        key = "MR_" + ev.exit_type
        tally[key] = tally.get(key, 0) + 1

    return delta

# ── Single-symbol backtest ────────────────────────────────────────────────────

def run_symbol(symbol: str, days: int, balance: float = INITIAL_BALANCE,
               btc_df: pd.DataFrame | None = None,
               df: pd.DataFrame | None = None,
               trade_start_idx: int | None = None) -> dict:
    """Run backtest for one symbol.

    df     : pre-fetched OHLCV DataFrame (skips network fetch if supplied).
             It SHOULD carry a REGIME_WARMUP_BARS prefix before the trading
             window — see `trade_start_idx`.
    btc_df : BTC reference data for macro gate + beta. Must span the same range
             as `df`, prefix included, or the regime labels go cold with it.
    trade_start_idx :
             Row index in `df` where the TRADING window begins. Everything
             before it is warm-up only: indicators and the 4h regime MA are
             computed across it, but no bar is traded and none of it reaches
             the metrics.

    M1 — why the prefix exists:
        regime.score_series_4h needs 210 closed 4h bars (~35d). Without them
        regime.py:82 sets score = 0.0 → NEUTRAL, and LONG_SIZE_MULT["NEUTRAL"]
        is 0.0, so momentum trades NOTHING for the first ~33 days of a window
        while the MR sleeve (gated to NEUTRAL) inherits the whole month.

        df rows:  [─── warm-up prefix ───|───── trading window ─────]
                  0                    trade_start_idx           len(df)
                  └ indicators + 4h regime MA warm here
                                       └ first bar that can open a position

        `days` stays the TRADING window length — it only feeds trades_per_day
        and monthly_est, which must not be diluted by the prefix.
    """
    if df is None:
        # Self-fetch: pull the window PLUS the regime warm-up prefix.
        df = fetch_history(symbol, days + REGIME_WARMUP_DAYS)
        trade_start_idx = REGIME_WARMUP_BARS
    elif trade_start_idx is None:
        print(f"  ⚠️  {symbol}: run_symbol got a pre-fetched frame with no "
              f"trade_start_idx — the 4h regime MA will be cold for its first "
              f"~{REGIME_WARMUP_DAYS}d and momentum will trade nothing there (M1).",
              flush=True)

    warmup  = max(65, int(trade_start_idx or 0))   # ≥65 bars for indicator warm-up
    if warmup >= len(df):
        raise ValueError(
            f"{symbol}: trade_start_idx={trade_start_idx} leaves no bars to trade "
            f"(len(df)={len(df)}). Fetch days + REGIME_WARMUP_DAYS."
        )

    # ── Vectorised bulk indicator pre-computation (major speedup) ─────────
    df = precompute_indicators(df)

    # ── FAZ 1: per-bar regime labels (BULL/NEUTRAL/BEAR) for long throttling ──
    regimes = regime.backtest_regimes(df, btc_df)

    # Build BTC timestamp → row-index lookup for fast alignment
    btc_ts_index: dict[int, int] = {}
    if btc_df is not None:
        btc_ts_index = {int(ts): i for i, ts in enumerate(btc_df["ts"])}

    # Hurst is recomputed EVERY bar (stride 1) to match live — see _hurst_at.
    hurst_cache: dict[int, float] = {}
    closes_arr = df["close"]

    if SHORT_ENABLED:
        print(f"  ⚠️  {symbol}: SHORT_ENABLED=1, but the live bot has NO short "
              f"sleeve (paper_bb never imports short_sleeve). This run is "
              f"research-only — it does not describe the deployed system.",
              flush=True)

    sym_state   = SymbolState(symbol=symbol)
    mr_state    = MRState(symbol=symbol)
    short_state = ShortState(symbol=symbol)

    bal            = balance
    peak           = balance
    daily_start    = balance
    daily_day      = ""
    daily_freeze   = False
    daily_sl_count = 0          # full-position SL hits today (resets each day)
    size_factor    = 1.0        # equity throttle — paper_bb.size_factor

    legs: list[dict] = []       # every balance-moving leg, live trade_log shape
    short_trades: list = []     # short sleeve — outside the live-parity accounting
    tally          = new_tally()
    equity_curve   = [balance]
    regime_counts  = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}
    s_tp1 = s_tp2 = s_sl = s_trail = s_tmo = 0

    t0     = time.time()
    n_bar  = len(df) - warmup
    verbose_bars = n_bar > 20_000        # long window → report, else stay quiet
    halted   = False
    bankrupt = False
    n_halts  = 0
    bars_run = 0
    first_ts = last_ts = 0

    for i in range(warmup, len(df)):
        if verbose_bars and (i - warmup) % 5000 == 0 and i > warmup:
            _progress(i - warmup, n_bar, t0, bal, f"  {symbol}")
        row   = df.iloc[i]
        high  = float(row["high"])
        low   = float(row["low"])
        ts    = int(row["ts"])
        dt    = _bar_close_dt(ts)        # live keys off the bar's CLOSE time
        day   = dt.strftime("%Y-%m-%d")
        hour  = dt.hour
        if not bars_run:
            first_ts = ts
        bars_run += 1
        last_ts   = ts

        # ── Daily reset (paper_bb.py:573) ─────────────────────────────────
        if day != daily_day:
            daily_day      = day
            daily_start    = bal
            daily_freeze   = False
            daily_sl_count = 0

        # ── Peak DD hard stop (paper_bb.py:581) ───────────────────────────
        if bal > peak:
            peak = bal
        peak_dd = (bal - peak) / peak if peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            n_halts += 1
            if not _RESTARTS:
                halted = True
                break
            peak    = bal          # operator restarts: peak rebased, run continues
            peak_dd = 0.0

        # Position size is a FIXED dollar risk, not a fraction of equity, so a
        # simulated account keeps trading at full size after the money is gone —
        # a 2095d --restarts run replayed 300k more bars at a balance of -$915.
        # A real account is liquidated at zero, so everything past that point is
        # fiction. The run ends here and says why.
        if bal <= 0:
            bankrupt = True
            halted   = True
            break

        # ── Equity throttle (paper_bb.py:594) — halve size in a -7% peak DD ──
        size_factor = 0.5 if peak_dd <= EQUITY_THROTTLE_DD else 1.0

        # ── Daily DD freeze (paper_bb.py:604) ─────────────────────────────
        intraday_dd = (bal - daily_start) / daily_start if daily_start > 0 else 0.0
        if intraday_dd <= DAILY_DD_LIMIT and not daily_freeze:
            daily_freeze = True

        # ── Session filter (paper_bb.py:611) ──────────────────────────────
        in_session = SESSION_START_UTC <= hour < SESSION_END_UTC
        block_new  = daily_freeze or not in_session

        reg = regimes[i]
        if sym_state.state == IDLE:
            regime_counts[reg] = regime_counts.get(reg, 0) + 1

        snap = build_snapshot(df, i, symbol, btc_df=btc_df,
                              hurst_override=_hurst_at(closes_arr, i, hurst_cache),
                              btc_row=btc_ts_index.get(ts))

        # open_full=0: a single-symbol run holds at most ONE full position and
        # MAX_OPEN is 2, so the portfolio cap can never bind here. It does bind in
        # run_portfolio — the only mode that can measure it.
        mom_events, mr_events, _info = process_symbol_bar(
            sym_state, mr_state, snap, high, low,
            reg=reg, size_factor=size_factor, block_new=block_new, open_full=0,
            rsi_val=snap["indicators"]["rsi_14"],
            vol_ratio=snap["volume"]["volume_ratio"])

        sl_before       = tally["SL"]
        bal            += account_events(mom_events, mr_events, legs, tally, ts)
        daily_sl_count += tally["SL"] - sl_before
        if daily_sl_count >= DAILY_SL_LIMIT:
            daily_freeze = True          # paper_bb.py:806

        # ── FAZ 3: short sleeve — NOT part of the live system ──────────────
        if SHORT_ENABLED:
            for ev in short_state.process_bar(snap, high, low,
                                              regime=reg, block_new=block_new):
                bal += ev.pnl
                if ev.exit_type == "OPEN":
                    continue
                short_trades.append(ev)
                if   ev.exit_type == "TP1":     s_tp1   += 1
                elif ev.exit_type == "TP2":     s_tp2   += 1
                elif ev.exit_type == "SL":      s_sl    += 1
                elif ev.exit_type == "TRAIL":   s_trail += 1
                elif ev.exit_type == "TIMEOUT": s_tmo   += 1

        equity_curve.append(bal)

    if verbose_bars and sys.stdout.isatty():
        print(flush=True)          # close the progress line

    # ── Metrics ───────────────────────────────────────────────────────────────
    # Positions are folded from the LEG LOG, exactly the way the live bot computes
    # them (paper_bb._print_status → aggregate_positions(trade_log)). That means
    # probe legs count as PROBE positions here too: they are real closed
    # round-trips with real cost. The backtest used to drop them, which is one
    # reason its expectancy read better than the account's.
    positions = aggregate_positions(legs)
    pos_stats = position_stats(positions, risk_per_trade=RISK_PER_TRADE_USD,
                               risk_by_sleeve=RISK_BY_SLEEVE)
    entry_fees = sum(l["pnl"] for l in legs if l["exit_type"] == "OPEN")

    # T0 identity: every leg that moved the balance is in the log.
    recon_err = ((bal - balance) - sum(l["pnl"] for l in legs)
                 - sum(t.pnl for t in short_trades))
    if abs(recon_err) > 0.01:
        print(f"  ⚠️  {symbol}: leg log does not reconcile with the equity curve "
              f"(off by ${recon_err:+.4f}) — a balance-moving leg is missing.",
              flush=True)

    full_closed = [l for l in legs
                   if l["sleeve"] == "MOMENTUM" and l["exit_type"] != "OPEN"]
    mr_closed   = [l for l in legs
                   if l["sleeve"] == "MR" and l["exit_type"] != "OPEN"]
    closed_pnls = ([l["pnl"] for l in full_closed] + [l["pnl"] for l in mr_closed]
                   + [t.pnl for t in short_trades])

    n    = len(closed_pnls)
    wins = sum(1 for p in closed_pnls if p > 0)
    wr   = wins / n * 100 if n > 0 else 0.0

    # PEAK_DD_LIMIT ends the loop mid-window. Scoring a truncated run against the
    # REQUESTED length is how a coin that lost 15% in three months got reported as
    # "-15% over 665d, -0.68%/month" — a slow bleed, when it was a fast one. Rate
    # metrics use the window that was actually replayed.
    days_run = ((last_ts - first_ts) / 86_400_000) if bars_run > 1 else 0.0
    days_eff = days_run if days_run > 0.5 else float(days)
    tpd      = n / days_eff

    total_return = (bal - balance) / balance * 100
    avg_pnl      = (bal - balance) / n if n > 0 else 0.0

    eq    = np.array(equity_curve)
    pk    = np.maximum.accumulate(eq)
    maxdd = float(((eq - pk) / pk).min()) * 100

    gross_w = sum(p for p in closed_pnls if p > 0)
    gross_l = abs(sum(p for p in closed_pnls if p < 0))
    pf      = gross_w / gross_l if gross_l > 0 else float("inf")

    return {
        "symbol":          symbol,
        "days":            days,
        "trades":          n,
        "trades_per_day":  tpd,
        "win_rate":        wr,
        "profit_factor":   pf,
        "pos_stats":       pos_stats,
        "pos_exits":       exit_distribution(positions),
        "_positions":      positions,   # pooled cross-symbol stats (not printed)
        "_legs":           legs,
        "recon_err":       recon_err,
        "entry_fees":      entry_fees,
        "total_return":    total_return,
        "start_balance":   balance,
        "final_balance":   bal,
        "max_dd":          maxdd,
        "avg_pnl":         avg_pnl,
        "monthly_est":     total_return / days_eff * 30,
        "days_run":        days_run,
        "bars_run":        bars_run,
        "halted":          halted,
        "bankrupt":        bankrupt,
        "n_halts":         n_halts,
        "first_ts":        first_ts,
        "last_ts":         last_ts,
        "tp1":             tally["TP1"],
        "tp2":             tally["TP2"],
        "sl":              tally["SL"],
        "trail":           tally["TRAIL"],
        "timeout":         tally["TIMEOUT"],
        "confirm_ok":      tally["confirm_ok"],
        "confirm_fail":    tally["confirm_fail"],
        "probe":           tally["probe"],
        "probe_cost":      tally["probe_cost"],
        "btc_gate_blocks": 0,           # obsolete — the regime gate replaced it
        "regime_counts":   regime_counts,
        "trades_full":     len(full_closed),
        "trades_mr":       len(mr_closed),
        "n_long":          sum(1 for l in full_closed if l["direction"] == "LONG"),
        "n_short":         len(short_trades),
        "short_wins":      sum(1 for t in short_trades if t.pnl > 0),
        "short_pnl":       sum(t.pnl for t in short_trades),
        "s_tp1":           s_tp1,
        "s_tp2":           s_tp2,
        "s_sl":            s_sl,
        "s_trail":         s_trail,
        "s_tmo":           s_tmo,
        "mr_tp":           tally["MR_TP"],
        "mr_sl":           tally["MR_SL"],
        "mr_tmo":          tally["MR_TIMEOUT"],
        "mr_wins":         sum(1 for l in mr_closed if l["pnl"] > 0),
        "tally":           tally,
        "_df":             df,      # keep fetched data for beta reuse (not printed)
    }


# ── Report ────────────────────────────────────────────────────────────────────

def _allin_line(r: dict) -> str:
    """Entry fees are in the equity curve but NOT in position pnl.

    aggregate_positions ignores "OPEN" legs, and an exit leg only carries its own
    side's cost — so every position's pnl is short one entry fee (~$0.68 on a $900
    momentum notional, i.e. ~0.07R). That is the live report's convention too
    (paper_bb._print_status filters exit_type != "OPEN"), so it is kept for
    parity — but it is stated here instead of hiding in the gap between the
    expectancy line and the balance.
    """
    n = (r.get("pos_stats") or {}).get("n_positions", 0)
    fees = r.get("entry_fees", 0.0)
    equity_delta = r["final_balance"] - r.get("start_balance", INITIAL_BALANCE)
    allin = equity_delta / n if n else 0.0
    return (f"${fees:+.2f} entry fees not in position pnl  →  all-in "
            f"${allin:+.2f}/position")


def _window_line(r: dict) -> str:
    """What was ACTUALLY replayed, versus what was asked for.

    PEAK_DD_LIMIT ends a run mid-window (live, paper_bb calls sys.exit there). A
    report that still prints the REQUESTED span turns a fast loss into a slow one:
    a 2095d run replayed 87 days, hit the guard, and reported "-0.09%/month" over
    a window it never saw. Same distortion on any per-symbol coin that stopped at
    -15%. Never print the request as if it were the result.
    """
    asked = float(r["days"])
    run   = float(r.get("days_run") or 0.0)
    halts = int(r.get("n_halts") or 0)
    if r.get("bankrupt"):
        return (f"{asked:.0f}d requested → {run:.0f}d REPLAYED   "
                f"💀 ACCOUNT WIPED OUT — balance reached zero after {halts} "
                f"hard stop(s); the remaining {asked - run:.0f}d were never traded")
    if r.get("halted"):
        return (f"{asked:.0f}d requested → {run:.0f}d REPLAYED   "
                f"🛑 hard-stopped at PEAK_DD_LIMIT ({PEAK_DD_LIMIT:.0%}); "
                f"the remaining {asked - run:.0f}d were never traded")
    if halts:
        return (f"{asked:.0f}d requested → {run:.0f}d replayed   "
                f"⚠️  {halts} hard stop(s) crossed via BT_RESTARTS=1 "
                f"(assumes an operator restarts every time — not live parity)")
    if 0 < run < asked * 0.98:
        return f"{asked:.0f}d requested → {run:.0f}d replayed (data starts later)"
    return f"{run:.0f}d replayed"


def _sleeve_block(r: dict) -> str:
    """Per-sleeve economics WITH each sleeve's own entry fees charged to it.

    This is the line that decides whether a sleeve earns its place. A sleeve can
    show a positive expectancy and still lose money, because position pnl omits
    the entry fee (aggregate_positions drops "OPEN" legs, and an exit leg carries
    only its own side's cost). MR at +$0.33/position against a ~$0.38 entry fee is
    exactly that case — and neither the pooled expectancy nor the compact
    by-sleeve line made it visible.

    Note the probe cost is kept in its OWN sleeve rather than charged to momentum.
    That is the project's existing convention (metrics.PROBE_EXITS) and it keeps
    the filter's price legible, but it means momentum's all-in figure is the cost
    of the positions it opened, not the cost of finding them.
    """
    by = (r.get("pos_stats") or {}).get("by_sleeve") or {}
    if not by:
        return "    —"
    fees: dict[str, float] = {}
    for leg in r.get("_legs", []):
        if leg["exit_type"] == "OPEN":
            fees[leg["sleeve"]] = fees.get(leg["sleeve"], 0.0) + leg["pnl"]
    out = []
    for name, v in by.items():
        n     = v["n"] or 1
        fee   = fees.get(name, 0.0)
        risk  = v.get("risk_per_trade")
        allin = (v["pnl"] + fee) / n
        exp_r = f" /{v['expectancy_r']:+.3f}R" if v.get("expectancy_r") is not None else ""
        all_r = f" /{allin/risk:+.3f}R" if risk else ""
        flag  = "" if (v["pnl"] + fee) > 0 else "   ⚠️  net negative all-in"
        out.append(
            f"    {name:<9} n={v['n']:<4} WR {v['win_rate']:>5.1f}%   "
            f"pnl ${v['pnl']:+9.2f}   exp ${v['expectancy']:+6.2f}{exp_r}"
            f"   entry fees ${fee:+8.2f}  →  all-in ${allin:+6.2f}{all_r}{flag}")
    return "\n".join(out)


def _sleeve_line(ps: dict) -> str:
    """Per-sleeve expectancy — a blended number hides which sleeve pays (M2)."""
    bs = ps.get("by_sleeve") or {}
    if not bs:
        return "—"
    return "   ".join(
        f"{k} n={v['n']} ${v['expectancy']:+.2f}"
        + (f"/{v['expectancy_r']:+.3f}R" if v.get("expectancy_r") is not None else "")
        for k, v in bs.items()
    )


def print_report(r: dict) -> None:
    wr_ok  = "✅" if r["win_rate"]      >= 55  else "❌"
    pf_ok  = "✅" if r["profit_factor"] >= 1.3 else "❌"
    tpd_ok = "✅" if r["trades_per_day"] >= 0.5 else "⚠️ "
    ret_ok = "✅" if r["total_return"]  >  0   else "❌"
    dd_ok  = "✅" if r["max_dd"]        >= -20 else "❌"

    confirm_rate = (r["confirm_ok"] / (r["confirm_ok"] + r["confirm_fail"]) * 100
                    if (r["confirm_ok"] + r["confirm_fail"]) > 0 else 0.0)
    rc         = r.get("regime_counts", {})
    rc_total   = sum(rc.values()) or 1
    regime_str = "  ".join(f"{k} {v/rc_total*100:.0f}%" for k, v in rc.items())

    # Position-based block — the honest numbers. The record-based Win Rate /
    # Profit Factor printed above double-count every winner (TP1 logs its own
    # record and the post-TP1 leg cannot lose), so judge arms on THESE.
    ps      = r.get("pos_stats") or {}
    pe      = r.get("pos_exits") or {}
    pe_str  = " ".join(f"{k}={v}" for k, v in pe.items()) or "—"
    be      = ps.get("breakeven_wr")
    pos_ok  = "✅" if (be is not None and ps.get("win_rate", 0) > be) else "❌"
    exp     = ps.get("expectancy")
    exp_r   = ps.get("expectancy_r")
    exp_str = (f"${exp:+.2f}" + (f" ({exp_r:+.3f} R)" if exp_r is not None else "")
               ) if exp is not None else "—"

    print(f"""
════════════════════════════════════════════════════
  BREAKOUT BOT — {r["symbol"]}  ({r["days"]}d)   [per-symbol account]
════════════════════════════════════════════════════
  Window           : {_window_line(r)}
  Trades (mom)     : {r.get("trades_full", r["trades"])}  (TP1={r["tp1"]} TP2={r["tp2"]} SL={r["sl"]} TRAIL={r["trail"]} TMO={r["timeout"]})
  Trades (SHORT)   : {r.get("n_short",0)}  (TP1={r.get("s_tp1",0)} TP2={r.get("s_tp2",0)} SL={r.get("s_sl",0)} TRAIL={r.get("s_trail",0)} TMO={r.get("s_tmo",0)})  WR {(r.get("short_wins",0)/r["n_short"]*100) if r.get("n_short") else 0:.0f}%  PnL ${r.get("short_pnl",0):+.2f}
  Trades (MR)      : {r.get("trades_mr", 0)}  (TP={r.get("mr_tp",0)} SL={r.get("mr_sl",0)} TMO={r.get("mr_tmo",0)})  WR {(r.get("mr_wins",0)/r["trades_mr"]*100) if r.get("trades_mr") else 0:.0f}%
  Probes           : {r.get("probe", 0)}  → drag ${r.get("probe_cost", 0.0):+.2f}  (fees paid for positions that never opened)
  Trades/day (all) : {r["trades_per_day"]:.2f}  {tpd_ok}
  ── record-based (inflated: TP1 counted twice — kept for continuity) ──
  Win Rate         : {r["win_rate"]:.1f}%  {wr_ok}
  Profit Factor    : {r["profit_factor"]:.2f}  {pf_ok}
  ── POSITION-BASED (the real numbers — judge on these) ──
  Positions        : {ps.get("n_positions", 0)}  ({pe_str})
  Win Rate (pos)   : {ps.get("win_rate", 0)}%   break-even {be}%  {pos_ok}
  Avg win / loss   : ${ps.get("avg_win", 0):+.2f} / ${ps.get("avg_loss", 0):+.2f}   payoff {ps.get("payoff")}
  Expectancy/pos   : {exp_str}
  Profit Factor    : {ps.get("profit_factor")}  (position-based)
  Cost not in above: {_allin_line(r)}
  ── BY SLEEVE (each sleeve charged its own entry fees) ──
{_sleeve_block(r)}
  ──
  Total Return     : {r["total_return"]:+.2f}%  {ret_ok}
  Monthly estimate : {r["monthly_est"]:+.2f}%
  Final Balance    : ${r["final_balance"]:.2f}
  Max Drawdown     : {r["max_dd"]:+.2f}%  {dd_ok}
  Avg PnL/trade    : ${r["avg_pnl"]:+.3f}
  Confirm rate     : {confirm_rate:.0f}%  ({r["confirm_ok"]} ok / {r["confirm_fail"]} fail)
  Regime (IDLE bar): {regime_str}
════════════════════════════════════════════════════
  VERDICT: {"🛑 HARD-STOPPED — the window was never finished, nothing to judge" if r.get("halted") else ("✅ DEPLOY" if (ps.get("profit_factor") or 0) >= 1.3 and (ps.get("expectancy") or 0) > 0 and r["max_dd"] >= -20 else "❌ NEEDS TUNING")}   (position-based PF≥1.3 + positive expectancy + MaxDD≥-20%)
""")


# ── Portfolio backtest (the live-parity mode) ─────────────────────────────────

def run_portfolio(tokens: list[str], days: int, balance: float = INITIAL_BALANCE,
                  *, data: dict, btc_df: "pd.DataFrame | None",
                  trade_start_idx: int) -> dict:
    """All tokens, ONE shared account, bar-synchronised — what production does.

    run_symbol() gives every token its own $1000 and no portfolio cap. That is a
    useful way to RANK coins, but it is not the deployed system and it flatters it
    in three specific ways:

      · MAX_OPEN never binds. Live holds at most 2 full positions across the whole
        book (config.MAX_OPEN); a confirmed signal on a third coin is dropped
        (paper_bb.py:675). Per-symbol runs take every one of them.
      · the drawdown guards never see the book. DAILY_DD_LIMIT, EQUITY_THROTTLE_DD
        and PEAK_DD_LIMIT are measured on ONE account live — five coins losing
        together throttle and then halt each other. Split across five independent
        accounts, the same losses barely register.
      · the returns are not additive. Five $1000 accounts is $5000 of capital.

    data : {symbol → OHLCV DataFrame}, each carrying REGIME_WARMUP_BARS of prefix
           BEFORE the trading window (see run_symbol's M1 note).
    """
    tokens = [t for t in tokens if t in data and len(data[t]) > trade_start_idx]
    if not tokens:
        raise ValueError("run_portfolio: no usable token data supplied")
    if SHORT_ENABLED:
        print("  ⚠️  SHORT_ENABLED=1 is ignored in portfolio mode — the live bot "
              "has no short sleeve, so including one would not be parity.",
              flush=True)

    # ── Per-token prep ────────────────────────────────────────────────────────
    if btc_df is not None:
        btc_df = precompute_indicators(btc_df)
    btc_ts_arr = (btc_df["ts"].to_numpy() if btc_df is not None
                  else np.empty(0, dtype=np.int64))

    frames: dict[str, pd.DataFrame] = {}
    regs:   dict[str, np.ndarray]   = {}
    ts_idx: dict[str, np.ndarray]   = {}
    hcache: dict[str, dict]         = {}
    for sym in tokens:
        d           = precompute_indicators(data[sym])
        frames[sym] = d
        regs[sym]   = regime.backtest_regimes(d, btc_df)
        ts_idx[sym] = d["ts"].to_numpy()
        hcache[sym] = {}

    # Master clock: every bar any token has, from the first tradable one onward.
    ref      = frames[tokens[0]]
    start_ts = int(ref["ts"].iloc[min(trade_start_idx, len(ref) - 1)])
    all_ts   = sorted({int(t) for sym in tokens for t in frames[sym]["ts"]
                       if int(t) >= start_ts})

    sym_states = {s: SymbolState(symbol=s) for s in tokens}
    mr_states  = {s: MRState(symbol=s)     for s in tokens}

    bal            = balance
    peak           = balance
    daily_start    = balance
    daily_day      = ""
    daily_freeze   = False
    daily_sl_count = 0        # GLOBAL, as live keeps it (paper_bb.daily_sl_count)
    size_factor    = 1.0

    legs: list[dict]  = []
    tally             = new_tally()
    equity_curve      = [balance]
    regime_counts     = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}
    bar_count         = 0
    halted            = False
    bankrupt          = False
    n_halts           = 0
    last_ts           = 0
    coverage: dict[str, list] = {}   # sym → [first_ts, last_ts, bars_seen]

    t0    = time.time()
    n_bar = len(all_ts)
    print(f"  ▶ replaying {n_bar:,} bars × {len(tokens)} symbols…", flush=True)

    for ts in all_ts:
        bar_count += 1
        last_ts    = ts
        if bar_count % 2000 == 0:
            _progress(bar_count, n_bar, t0, bal,
                      f"  pos={len(legs)} legs")
        dt   = _bar_close_dt(ts)
        day  = dt.strftime("%Y-%m-%d")
        hour = dt.hour

        # ── Daily reset (paper_bb.py:573) ─────────────────────────────────
        if day != daily_day:
            daily_day      = day
            daily_start    = bal
            daily_freeze   = False
            daily_sl_count = 0

        # ── Peak DD hard stop (paper_bb.py:581 — live calls sys.exit here) ──
        if bal > peak:
            peak = bal
        peak_dd = (bal - peak) / peak if peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            n_halts += 1
            if not _RESTARTS:
                halted = True
                break
            peak    = bal          # operator restarts: peak rebased, run continues
            peak_dd = 0.0

        # Position size is a FIXED dollar risk, not a fraction of equity, so a
        # simulated account keeps trading at full size after the money is gone —
        # a 2095d --restarts run replayed 300k more bars at a balance of -$915.
        # A real account is liquidated at zero, so everything past that point is
        # fiction. The run ends here and says why.
        if bal <= 0:
            bankrupt = True
            halted   = True
            break

        # ── Equity throttle (paper_bb.py:594) ─────────────────────────────
        size_factor = 0.5 if peak_dd <= EQUITY_THROTTLE_DD else 1.0

        # ── Daily DD freeze (paper_bb.py:604) ─────────────────────────────
        intraday_dd = (bal - daily_start) / daily_start if daily_start > 0 else 0.0
        if intraday_dd <= DAILY_DD_LIMIT and not daily_freeze:
            daily_freeze = True

        in_session = SESSION_START_UTC <= hour < SESSION_END_UTC
        block_new  = daily_freeze or not in_session
        btc_row    = _row_for_ts(btc_ts_arr, ts)

        # Token order matters and matches live: a symbol that opens a full
        # position early in the loop occupies a MAX_OPEN slot for the ones after it
        # on the SAME bar (paper_bb iterates self.tokens in config order).
        for sym in tokens:
            i = _row_for_ts(ts_idx[sym], ts)
            if i is None or i < 65:
                continue          # bar missing / not warm — live skips the symbol
            c = coverage.get(sym)
            if c is None:
                coverage[sym] = [ts, ts, 1]
            else:
                c[1], c[2] = ts, c[2] + 1
            d   = frames[sym]
            row = d.iloc[i]
            reg = str(regs[sym][i])
            s   = sym_states[sym]
            if s.state == IDLE:
                regime_counts[reg] = regime_counts.get(reg, 0) + 1

            snap = build_snapshot(
                d, i, sym, btc_df=btc_df,
                hurst_override=_hurst_at(d["close"], i, hcache[sym]),
                btc_row=btc_row)

            open_full = sum(1 for st in sym_states.values()
                            if st.state in (SCALE_OPEN, TRAILING))

            mom_events, mr_events, info = process_symbol_bar(
                s, mr_states[sym], snap, float(row["high"]), float(row["low"]),
                reg=reg, size_factor=size_factor, block_new=block_new,
                open_full=open_full,
                rsi_val=snap["indicators"]["rsi_14"],
                vol_ratio=snap["volume"]["volume_ratio"])
            if info["block_new_full"]:
                tally["maxopen_block"] += 1

            sl_before       = tally["SL"]
            bal            += account_events(mom_events, mr_events, legs, tally, ts)
            daily_sl_count += tally["SL"] - sl_before
            if daily_sl_count >= DAILY_SL_LIMIT:
                daily_freeze = True      # paper_bb.py:806 — freezes the WHOLE book

        equity_curve.append(bal)

    if sys.stdout.isatty():
        print(flush=True)          # close the progress line

    # ── Metrics ───────────────────────────────────────────────────────────────
    positions = aggregate_positions(legs)
    pos_stats = position_stats(positions, risk_per_trade=RISK_PER_TRADE_USD,
                               risk_by_sleeve=RISK_BY_SLEEVE)
    entry_fees = sum(l["pnl"] for l in legs if l["exit_type"] == "OPEN")

    recon_err = (bal - balance) - sum(l["pnl"] for l in legs)
    if abs(recon_err) > 0.01:
        print(f"  ⚠️  leg log does not reconcile with the equity curve "
              f"(off by ${recon_err:+.4f}) — a balance-moving leg is missing.",
              flush=True)

    eq    = np.array(equity_curve)
    pk    = np.maximum.accumulate(eq)
    maxdd = float(((eq - pk) / pk).min()) * 100
    total_return = (bal - balance) / balance * 100

    first_ts = all_ts[0] if all_ts else 0
    days_run = ((last_ts - first_ts) / 86_400_000) if bar_count > 1 else 0.0
    days_eff = days_run if days_run > 0.5 else float(days)

    return {
        "symbols":       tokens,
        "days":          days,
        "bars":          bar_count,
        "halted":        halted,
        "start_ts":      first_ts,
        "end_ts":        last_ts,          # where the replay ACTUALLY stopped
        "window_end_ts": all_ts[-1] if all_ts else 0,
        "days_run":      days_run,
        "n_halts":       n_halts,
        "bankrupt":      bankrupt,
        "pos_stats":     pos_stats,
        "pos_exits":     exit_distribution(positions),
        "_positions":    positions,
        "_legs":         legs,
        "recon_err":     recon_err,
        "entry_fees":    entry_fees,
        "start_balance": balance,
        "final_balance": bal,
        "total_return":  total_return,
        "monthly_est":   total_return / days_eff * 30,
        "max_dd":        maxdd,
        "regime_counts": regime_counts,
        "tally":         tally,
        "coverage":      {k: {"first": v[0], "last": v[1], "bars": v[2]}
                          for k, v in coverage.items()},
    }


def print_portfolio_report(r: dict) -> None:
    ps  = r["pos_stats"]
    t   = r["tally"]
    be  = ps.get("breakeven_wr")
    exp = ps.get("expectancy")
    exp_r = ps.get("expectancy_r")
    pe_str = " ".join(f"{k}={v}" for k, v in (r.get("pos_exits") or {}).items()) or "—"
    conf   = t["confirm_ok"] + t["confirm_fail"]
    cr     = 100 * t["confirm_ok"] / conf if conf else 0.0
    rc     = r["regime_counts"]
    rc_tot = sum(rc.values()) or 1
    reg_str = "  ".join(f"{k} {v/rc_tot*100:.0f}%" for k, v in rc.items())
    win = (be is not None and ps.get("win_rate", 0) > be)

    # Per-symbol contribution — which coin actually paid, on the shared account.
    per_sym: dict[str, list] = {}
    for p in r["_positions"]:
        per_sym.setdefault(p.symbol, []).append(p)
    # Coverage matters the moment a window reaches back past a coin's listing:
    # Binance USDⓈ-M futures only start 2019-09, and each alt joins later still
    # (POLUSDT carries no MATIC history — the rename reset it). Without this
    # column a ragged run reads as a 5-coin backtest when most of it was three.
    cov        = r.get("coverage") or {}
    total_bars = r.get("bars") or 1
    ragged     = False
    sym_lines  = []
    for sym in sorted(set(list(per_sym) + list(cov)),
                      key=lambda k: -sum(p.pnl for p in per_sym.get(k, []))):
        ps_list = per_sym.get(sym, [])
        pnl = sum(p.pnl for p in ps_list)
        mom = sum(1 for p in ps_list if p.sleeve == "MOMENTUM")
        mr  = sum(1 for p in ps_list if p.sleeve == "MR")
        prb = sum(1 for p in ps_list if p.sleeve == "PROBE")
        span = ""
        c = cov.get(sym)
        if c:
            pct = 100.0 * c["bars"] / total_bars
            ragged = ragged or pct < 95.0
            a = datetime.fromtimestamp(c["first"] / 1000, tz=timezone.utc)
            span = f"   from {a:%Y-%m-%d}  {pct:3.0f}% of window"
        sym_lines.append(
            f"    {sym:<12} ${pnl:+9.2f}   mom={mom:<3} mr={mr:<3} probe={prb:<4}{span}")
    if ragged:
        sym_lines.append(
            "    ⚠️  Not every coin existed across the whole window. This equity "
            "curve is a CHANGING\n        book, not today's book — the headline % "
            "is not comparable across the span.")

    span = ""
    if r["start_ts"] and r["end_ts"]:
        a = datetime.fromtimestamp(r["start_ts"] / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp(r["end_ts"]   / 1000, tz=timezone.utc)
        span = f"{a:%Y-%m-%d} → {b:%Y-%m-%d}"

    print(f"""
══════════════════════════════════════════════════════════════
  PORTFOLIO BACKTEST — live parity   ({len(r["symbols"])} coins, {r["days"]}d)
  {span}   {r["bars"]:,} bars   MAX_OPEN={MAX_OPEN}   ${r["start_balance"]:.0f} shared account
  Window: {_window_line(r)}
══════════════════════════════════════════════════════════════
  Final Balance    : ${r["final_balance"]:.2f}   ({r["total_return"]:+.2f}%)
  Monthly estimate : {r["monthly_est"]:+.2f}%
  Max Drawdown     : {r["max_dd"]:+.2f}%   {"🛑 HARD-STOPPED (PEAK_DD_LIMIT)" if r["halted"] else ""}
  Hard stops       : {r.get("n_halts", 0)}
  ── POSITION-BASED (the only honest numbers) ──
  Positions        : {ps.get("n_positions", 0)}  ({pe_str})
  Win Rate         : {ps.get("win_rate", 0)}%   break-even {be}%   {"✅" if win else "❌"}
  Avg win / loss   : ${ps.get("avg_win", 0):+.2f} / ${ps.get("avg_loss", 0):+.2f}   payoff {ps.get("payoff")}
  Expectancy/pos   : ${exp:+.2f}{f"  ({exp_r:+.3f} R)" if exp_r is not None else ""}
  Profit Factor    : {ps.get("profit_factor")}
  Cost not in above: {_allin_line(r)}
  ── BY SLEEVE (each sleeve charged its own entry fees) ──
{_sleeve_block(r)}
  ── SIGNAL FUNNEL (same counters the live bot logs) ──
  Probes           : {t["probe"]}   confirm {cr:.0f}% ({t["confirm_ok"]} ok / {t["confirm_fail"]} fail)
  Full positions   : {t["full"]}   blocked by MAX_OPEN: {t["maxopen_block"]}
  Probe drag       : ${t["probe_cost"]:+.2f}   (paid for positions that never opened)
  Momentum exits   : TP1={t["TP1"]} TP2={t["TP2"]} SL={t["SL"]} TRAIL={t["TRAIL"]} TMO={t["TIMEOUT"]}
  MR exits         : TP={t["MR_TP"]} SL={t["MR_SL"]} TMO={t["MR_TIMEOUT"]}
  Regime (IDLE bar): {reg_str}
  ── PER SYMBOL ──
{chr(10).join(sym_lines) if sym_lines else "    (no positions)"}
══════════════════════════════════════════════════════════════
  VERDICT: {"🛑 HARD-STOPPED — the window was never finished, nothing to judge" if r.get("halted") else ("✅ DEPLOY" if (ps.get("profit_factor") or 0) >= 1.3 and (exp or 0) > 0 and r["max_dd"] >= -20 else "❌ NEEDS TUNING")}   (PF≥1.3 + positive expectancy + MaxDD≥-20%)
  reconciliation: legs vs equity off by ${r["recon_err"]:+.4f} (must be ~0)
""")


def dump_run(r: dict, path: str) -> None:
    """Persist the leg log + summary so a run can be re-analysed without replaying.

    A 240d 5-coin replay costs ~14 minutes of CPU. Every follow-up question about
    the result — which month carried it, what a sleeve costs, how the drawdown was
    shaped — should not cost another one. Legs carry their bar timestamp, so the
    whole run is reconstructible from this file.
    """
    import json
    payload = {
        "symbols":       r.get("symbols") or [r.get("symbol")],
        "days":          r["days"],
        # A run that hit PEAK_DD_LIMIT stopped mid-window. Without these fields a
        # reader compares two arms that replayed DIFFERENT amounts of history and
        # cannot tell: on the 665d window three arms halted between month 2 and
        # month 6, and their final balances were being read as if they were
        # like-for-like. print_portfolio_report says so on screen; the dump has to
        # carry it too, or every downstream table repeats the mistake.
        "halted":        r.get("halted"),
        "n_halts":       r.get("n_halts"),
        "bankrupt":      r.get("bankrupt"),
        "days_run":      r.get("days_run"),
        "start_balance": r.get("start_balance"),
        "final_balance": r["final_balance"],
        "total_return":  r["total_return"],
        "max_dd":        r["max_dd"],
        "entry_fees":    r.get("entry_fees"),
        "recon_err":     r.get("recon_err"),
        "tally":         r.get("tally"),
        "pos_stats":     r.get("pos_stats"),
        "regime_counts": r.get("regime_counts"),
        "legs":          r.get("_legs", []),
    }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        print(f"  💾 run saved → {path}  ({len(payload['legs']):,} legs, "
              f"re-analyse without replaying)")
    except Exception as exc:
        print(f"  ⚠️  could not save the run ({exc})", flush=True)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_frames(tokens: list[str], days: int,
                use_cache: bool = False) -> dict[str, pd.DataFrame]:
    """Fetch `days` of 5m data PLUS the REGIME_WARMUP_DAYS prefix, per token.

    The prefix is not optional. regime.score_series_4h needs 210 CLOSED 4h bars
    (~35d) before it can label anything; without them every bar reads NEUTRAL, and
    LONG_SIZE_MULT["NEUTRAL"] is 0.0 — so momentum trades nothing for the first
    ~35 days of the window and the MR sleeve inherits the month (M1). This applies
    to the BTC frame too: the CLI used to fetch BTC for `days` only, which left the
    BTC half of the regime blend pinned at 0 for the first 35 days of every run.

    Cache files share bench.py's naming, so the two harnesses reuse each other's
    downloads. Caching is opt-in here: an uncached run always means "the last N
    days as of now".
    """
    out: dict[str, pd.DataFrame] = {}
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
    for sym in tokens:
        path = f"{CACHE_DIR}/{sym}_{days}d+{REGIME_WARMUP_DAYS}w.pkl"
        if use_cache and os.path.exists(path):
            try:
                out[sym] = pd.read_pickle(path)
                print(f"  [cache] {sym}: {len(out[sym]):,} bars", flush=True)
                continue
            except Exception as exc:
                print(f"  ⚠️  {sym}: cache unreadable ({exc}) — refetching",
                      flush=True)
        df = fetch_history(sym, days + REGIME_WARMUP_DAYS)
        want = days + REGIME_WARMUP_DAYS
        if len(df) > 1:
            got = (int(df["ts"].iloc[-1]) - int(df["ts"].iloc[0])) / 86_400_000
            if got < want * 0.95:
                first = datetime.fromtimestamp(int(df["ts"].iloc[0]) / 1000,
                                               tz=timezone.utc)
                print(f"  ⚠️  {sym}: asked {want}d, the exchange only has {got:.0f}d "
                      f"(first bar {first:%Y-%m-%d}). It trades only where it has "
                      f"data — see the coverage column in the report.", flush=True)
        if use_cache:
            try:
                df.to_pickle(path)
            except Exception as exc:
                print(f"  ⚠️  {sym}: could not cache ({exc})", flush=True)
        out[sym] = df
    return out

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BreakoutBot Backtest — default mode mirrors the live bot")
    parser.add_argument("--tokens", nargs="+", default=None,
                        help="Tokens to test (default: config.TOKENS, the live universe)")
    parser.add_argument("--days",    type=int,   default=90)
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE,
                        help="Starting equity. In portfolio mode this is the WHOLE "
                             "account, as it is live; in --per-symbol mode each "
                             "token gets this much on its own.")
    parser.add_argument("--per-symbol", action="store_true",
                        help="Legacy mode: one independent account per token and no "
                             "MAX_OPEN cap. Good for RANKING coins, not a picture "
                             "of the deployed account.")
    parser.add_argument("--all", action="store_true",
                        help="Run the full 23-coin universe instead of the live 5")
    parser.add_argument("--beta", action="store_true",
                        help="Print beta ranking vs BTC after the backtest")
    parser.add_argument("--no-btc-gate", action="store_true",
                        help="Drop BTC from the regime blend (ablation)")
    parser.add_argument("--restarts", action="store_true",
                        help="Continue past a PEAK_DD_LIMIT hard stop by rebasing "
                             "the equity peak — models an operator restarting the "
                             "bot each time. Needed for any multi-year study, but "
                             "NOT live parity (same as BT_RESTARTS=1).")
    parser.add_argument("--cache", action="store_true",
                        help="Reuse/store the window under backtests/data/ (shared "
                             "with bench.py). Off by default, so a plain run always "
                             "means 'the last N days as of now'.")
    args = parser.parse_args()

    if args.restarts:
        globals()["_RESTARTS"] = True

    check_live_params()

    tokens = list(_FULL_UNIVERSE) if args.all else (args.tokens or list(TOKENS))
    need   = list(dict.fromkeys(tokens + ["BTCUSDT"]))   # BTC always: regime blend

    print(f"📡 Window: last {args.days}d + {REGIME_WARMUP_DAYS}d regime warm-up "
          f"| {len(need)} symbols | {'portfolio (live parity)' if not args.per_symbol else 'per-symbol'} mode")
    frames = load_frames(need, args.days, use_cache=args.cache)

    btc_df: pd.DataFrame | None = None
    if BTC_GATE_ENABLED and not args.no_btc_gate:
        btc_df = frames["BTCUSDT"]
    else:
        print("ℹ️  BTC dropped from the regime blend (ablation run).")

    _span = frames[tokens[0]]
    _a = datetime.fromtimestamp(int(_span["ts"].iloc[0])  / 1000, tz=timezone.utc)
    _b = datetime.fromtimestamp(int(_span["ts"].iloc[-1]) / 1000, tz=timezone.utc)
    print(f"   data {_a:%Y-%m-%d} → {_b:%Y-%m-%d}  ({len(_span):,} bars; the first "
          f"{REGIME_WARMUP_BARS:,} are warm-up and are never traded)")

    if args.per_symbol:
        all_results: list[dict] = []
        for tok in tokens:
            try:
                r = run_symbol(tok, args.days, args.balance,
                               btc_df=btc_df.copy() if btc_df is not None else None,
                               df=frames[tok].copy(),
                               trade_start_idx=REGIME_WARMUP_BARS)
                print_report(r)
                r.pop("_df", None)
                all_results.append(r)
            except Exception as e:
                print(f"\n  ❌ {tok} failed: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()

        if len(all_results) > 1:
            n_ok    = sum(1 for r in all_results if r["total_return"] > 0)
            monthly = sum(r["monthly_est"] * args.balance / 100 for r in all_results)
            tpd     = sum(r["trades_per_day"] for r in all_results)
            avg_wr  = sum(r["win_rate"] for r in all_results) / len(all_results)
            pooled  = position_stats(
                [p for r in all_results for p in r.get("_positions", [])],
                risk_per_trade=RISK_PER_TRADE_USD, risk_by_sleeve=RISK_BY_SLEEVE)
            print(f"\n{'═'*56}")
            print(f"  COMBINED ({len(all_results)} independent accounts, {n_ok} profitable)")
            print(f"  Total trades/day : {tpd:.1f}")
            print(f"  Avg Win Rate     : {avg_wr:.1f}%  (record-based, inflated)")
            print(f"  Monthly est ($)  : ${monthly:+.2f}  "
                  f"(on ${args.balance:.0f} EACH — implies ${args.balance*len(all_results):.0f} deployed)")
            print(f"  Pooled positions : {pooled['n_positions']}  WR {pooled['win_rate']}%  "
                  f"exp ${pooled['expectancy']:+.2f}"
                  + (f" ({pooled['expectancy_r']:+.3f} R)"
                     if pooled['expectancy_r'] is not None else ""))
            print(f"  By sleeve        : {_sleeve_line(pooled)}")
            print(f"{'═'*56}")
            print("  ⚠️  These are SEPARATE accounts with no MAX_OPEN cap and no "
                  "shared drawdown guard.\n      Drop --per-symbol for the "
                  "live-parity portfolio run.")
    else:
        r = run_portfolio(tokens, args.days, args.balance,
                          data={t: frames[t].copy() for t in tokens},
                          btc_df=btc_df.copy() if btc_df is not None else None,
                          trade_start_idx=REGIME_WARMUP_BARS)
        print_portfolio_report(r)
        # BT_RUN_TAG keeps parallel arms from overwriting each other's dump: two
        # arms on the SAME window write the same path otherwise, and the second
        # one silently wins. Unset → the old filename, so nothing else changes.
        _tag = os.getenv("BT_RUN_TAG", "")
        dump_run(r, f"{CACHE_DIR}/last_run_{args.days}d{'_' + _tag if _tag else ''}.json")

    # ── Optional beta ranking (reuses fetched data — no extra network calls) ──
    if args.beta and btc_df is not None:
        print_beta_ranking(tokens, args.days, btc_df, prefetched=frames)