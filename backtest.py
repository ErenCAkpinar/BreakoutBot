"""
BreakoutBot Backtest

Simulates the Test-Confirm-Scale strategy on historical 5m OHLCV data.
Runs each symbol independently, reports per-symbol + combined metrics.
BTC macro gate: blocks new LONGs when BTC 4h return < BTC_GATE_RETURN.
Beta scoring: ranks tokens by responsiveness to BTC moves.

Usage:
    python backtest.py                              # SOL/USDT 30 days
    python backtest.py --tokens SOLUSDT --days 30
    python backtest.py --tokens SOLUSDT ETHUSDT BTCUSDT --days 30
    python backtest.py --days 90                    # full 30 tokens, 90 days
    python backtest.py --all --beta                 # run all tokens + print beta ranking
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
from strategy   import SymbolState, IDLE
from mean_reversion import MRState
from short_sleeve import ShortState
import regime
from config import MR_ENABLED, SHORT_ENABLED
from config import (
    TOKENS, TIMEFRAME, INITIAL_BALANCE,
    DAILY_DD_LIMIT, PEAK_DD_LIMIT, DAILY_SL_LIMIT,
    SESSION_START_UTC, SESSION_END_UTC,
    TAKER_FEE, FULL_SIZE_USD, LEVERAGE, RISK_PER_TRADE_USD,
    BTC_GATE_ENABLED, BTC_GATE_RETURN, BTC_BETA_WINDOW,
    BTC_BETA_BOOST, BTC_BETA_BLOCK,
)
from metrics import aggregate_positions, position_stats, exit_distribution


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


# ── Single-symbol backtest ────────────────────────────────────────────────────

def run_symbol(symbol: str, days: int, balance: float = INITIAL_BALANCE,
               btc_df: pd.DataFrame | None = None,
               df: pd.DataFrame | None = None) -> dict:
    """Run backtest for one symbol.

    df     : pre-fetched OHLCV DataFrame (skips network fetch if supplied)
    btc_df : BTC reference data for macro gate + beta
    """
    if df is None:
        df = fetch_history(symbol, days)
    warmup  = 65    # need ≥65 bars for all indicator warm-up

    # ── Vectorised bulk indicator pre-computation (major speedup) ─────────
    df = precompute_indicators(df)

    # ── FAZ 1: per-bar regime labels (BULL/NEUTRAL/BEAR) for long throttling ──
    regimes = regime.backtest_regimes(df, btc_df)

    # Build BTC timestamp → row-index lookup for fast alignment
    btc_ts_index: dict[int, int] = {}
    if btc_df is not None:
        btc_ts_index = {int(ts): i for i, ts in enumerate(btc_df["ts"])}

    # Pre-cache Hurst exponent every 5 bars (regime changes on ~25min timescale)
    hurst_cache: dict[int, float] = {}
    closes_arr = df["close"]
    for hi in range(warmup, len(df), 5):
        hc = closes_arr.iloc[max(0, hi - 39): hi + 1]
        hurst_cache[hi] = hurst_exponent(hc)

    sym_state    = SymbolState(symbol=symbol)
    bal          = balance
    peak         = balance
    daily_start  = balance
    daily_day    = ""
    daily_freeze = False

    full_trades    = []      # only FULL-position trades for metrics
    equity_curve   = [balance]
    tp1_count      = tp2_count = sl_count = trail_count = timeout_count = 0
    confirm_ok     = confirm_fail = 0
    daily_sl_count = 0       # SL hits today (reset each day)
    btc_gate_blocks = 0      # (obsolete — replaced by regime gate)
    regime_counts   = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}

    # ── FAZ 2: mean-reversion sleeve (parallel to momentum) ──────────────────
    mr_state   = MRState(symbol=symbol)
    mr_trades  = []                       # MR-sleeve closed trades (for metrics)
    mr_tp = mr_sl = mr_tmo = 0

    # ── FAZ 3: short sleeve (strict price-action shorts) ─────────────────────
    short_state  = ShortState(symbol=symbol)
    short_trades = []                     # short-sleeve closed trades
    s_tp1 = s_tp2 = s_sl = s_trail = s_tmo = 0

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        high  = float(row["high"])
        low   = float(row["low"])
        close = float(row["close"])
        ts    = int(row["ts"])
        dt    = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        day   = dt.strftime("%Y-%m-%d")
        hour  = dt.hour

        # ── Daily reset ───────────────────────────────────────────────────
        if day != daily_day:
            daily_day      = day
            daily_start    = bal
            daily_freeze   = False
            daily_sl_count = 0

        # ── Peak DD / hard stop ───────────────────────────────────────────
        if bal > peak:
            peak = bal
        peak_dd = (bal - peak) / peak if peak > 0 else 0.0
        if peak_dd <= PEAK_DD_LIMIT:
            break

        # ── Daily DD freeze ───────────────────────────────────────────────
        intraday_dd = (bal - daily_start) / daily_start if daily_start > 0 else 0.0
        if intraday_dd <= DAILY_DD_LIMIT and not daily_freeze:
            daily_freeze = True

        # ── Session filter ────────────────────────────────────────────────
        in_session = SESSION_START_UTC <= hour < SESSION_END_UTC

        # ── FAZ 1: regime gate (replaces crude BTC -1.5% block) ──────────────
        # Long entries are ALLOWED in every regime but THROTTLED to 35% size in
        # NEUTRAL/BEAR (vs 100% in BULL). Shorts come in Faz 3.
        _btc_row  = btc_ts_index.get(ts)   # O(1) lookup, reused for snapshot
        reg       = regimes[i]
        # BT_NO_REGIME=1 → ablation baseline (no throttle, mimics pre-Faz-1)
        size_mult = 1.0 if os.getenv("BT_NO_REGIME") == "1" \
                    else regime.LONG_SIZE_MULT.get(reg, 1.0)
        if sym_state.state == IDLE:
            regime_counts[reg] = regime_counts.get(reg, 0) + 1

        # Build snapshot (pass pre-looked-up btc_row + cached Hurst for speed)
        _hurst = hurst_cache.get(i - (i % 5), 0.5)
        snap      = build_snapshot(df, i, symbol, btc_df=btc_df,
                                   hurst_override=_hurst, btc_row=_btc_row)
        rsi_val   = snap["indicators"]["rsi_14"]
        vol_ratio = snap["volume"]["volume_ratio"]
        beta_val  = snap["meta"].get("beta_vs_btc", 1.0)

        # ── Entry gate: freeze / out-of-session blocks NEW entries only ───
        block_new = daily_freeze or not in_session

        # ── Momentum sleeve (Faz 1 regime-throttled) ──────────────────────
        if not (sym_state.state == IDLE and block_new):
            events = sym_state.process_bar(snap, high, low, rsi_val, vol_ratio,
                                           size_mult=size_mult)
            for ev in events:
                bal += ev.pnl

                if ev.exit_type == "OPEN":
                    continue   # position opened, fee already deducted
                if ev.kind == "TEST":
                    if ev.exit_type == "CONFIRM_OK":
                        confirm_ok += 1
                    elif ev.exit_type == "CONFIRM_FAIL":
                        confirm_fail += 1
                    continue

                # FULL trade completed
                full_trades.append(ev)
                if ev.exit_type == "TP1":    tp1_count   += 1
                elif ev.exit_type == "TP2":  tp2_count   += 1
                elif ev.exit_type == "SL":
                    sl_count       += 1
                    daily_sl_count += 1
                    if daily_sl_count >= DAILY_SL_LIMIT:
                        daily_freeze = True   # too many SL → freeze symbol today
                elif ev.exit_type == "TRAIL":   trail_count   += 1
                elif ev.exit_type == "TIMEOUT": timeout_count += 1

        # ── FAZ 2: range mean-reversion sleeve (parallel, NEUTRAL-only) ───
        if MR_ENABLED:
            mr_events = mr_state.process_bar(snap, high, low,
                                             regime=reg, block_new=block_new)
            for ev in mr_events:
                bal += ev.pnl
                if ev.exit_type == "OPEN":
                    continue
                mr_trades.append(ev)
                if   ev.exit_type == "TP":      mr_tp  += 1
                elif ev.exit_type == "SL":      mr_sl  += 1
                elif ev.exit_type == "TIMEOUT": mr_tmo += 1

        # ── FAZ 3: short sleeve (strict price-action, regime-gated) ───────
        if SHORT_ENABLED:
            s_events = short_state.process_bar(snap, high, low,
                                               regime=reg, block_new=block_new)
            for ev in s_events:
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

    # ── Metrics ───────────────────────────────────────────────────────────────
    full_closed = [t for t in full_trades if t.exit_type not in ("OPEN",)]
    closed      = full_closed + mr_trades + short_trades   # combined system
    n           = len(closed)
    n_full      = len(full_closed)
    n_mr        = len(mr_trades)
    n_long      = sum(1 for t in full_closed if t.direction == "LONG")
    n_short     = len(short_trades)
    short_wins  = sum(1 for t in short_trades if t.pnl > 0)
    short_pnl   = sum(t.pnl for t in short_trades)

    wins   = sum(1 for t in closed if t.pnl > 0)
    wr     = wins / n * 100 if n > 0 else 0.0
    tpd    = n / days

    # Position-based truth: TP1 emits its own record and the post-TP1 leg cannot
    # lose, so record-based `wr`/`pf` above double-count every winner. Judge arms
    # on these instead — see metrics.py for the derivation.
    positions  = aggregate_positions(closed)
    pos_stats  = position_stats(positions, risk_per_trade=RISK_PER_TRADE_USD)

    total_return = (bal - balance) / balance * 100
    avg_pnl      = (bal - balance) / n if n > 0 else 0.0

    eq   = np.array(equity_curve)
    pk   = np.maximum.accumulate(eq)
    dd   = (eq - pk) / pk
    maxdd= float(dd.min()) * 100

    pnls   = [t.pnl for t in closed]
    gross_w= sum(p for p in pnls if p > 0)
    gross_l= abs(sum(p for p in pnls if p < 0))
    pf     = gross_w / gross_l if gross_l > 0 else float("inf")

    return {
        "symbol":          symbol,
        "days":            days,
        "trades":          n,
        "trades_per_day":  tpd,
        "win_rate":        wr,
        "profit_factor":   pf,
        "pos_stats":       pos_stats,
        "pos_exits":       exit_distribution(positions),
        "_positions":      positions,   # for pooled cross-symbol stats (not printed)
        "total_return":    total_return,
        "final_balance":   bal,
        "max_dd":          maxdd,
        "avg_pnl":         avg_pnl,
        "monthly_est":     total_return / days * 30,
        "tp1":             tp1_count,
        "tp2":             tp2_count,
        "sl":              sl_count,
        "trail":           trail_count,
        "timeout":         timeout_count,
        "confirm_ok":      confirm_ok,
        "confirm_fail":    confirm_fail,
        "btc_gate_blocks": btc_gate_blocks,
        "regime_counts":   regime_counts,
        "trades_full":     n_full,
        "trades_mr":       n_mr,
        "n_long":          n_long,
        "n_short":         n_short,
        "short_wins":      short_wins,
        "short_pnl":       short_pnl,
        "s_tp1":           s_tp1,
        "s_tp2":           s_tp2,
        "s_sl":            s_sl,
        "s_trail":         s_trail,
        "s_tmo":           s_tmo,
        "mr_tp":           mr_tp,
        "mr_sl":           mr_sl,
        "mr_tmo":          mr_tmo,
        "mr_wins":         sum(1 for t in mr_trades if t.pnl > 0),
        "_df":             df,      # keep fetched data for beta reuse (not printed)
    }


# ── Report ────────────────────────────────────────────────────────────────────

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
  BREAKOUT BOT — {r["symbol"]}  ({r["days"]}d)
════════════════════════════════════════════════════
  Trades (mom)     : {r.get("trades_full", r["trades"])}  (TP1={r["tp1"]} TP2={r["tp2"]} SL={r["sl"]} TRAIL={r["trail"]} TMO={r["timeout"]})
  Trades (SHORT)   : {r.get("n_short",0)}  (TP1={r.get("s_tp1",0)} TP2={r.get("s_tp2",0)} SL={r.get("s_sl",0)} TRAIL={r.get("s_trail",0)} TMO={r.get("s_tmo",0)})  WR {(r.get("short_wins",0)/r["n_short"]*100) if r.get("n_short") else 0:.0f}%  PnL ${r.get("short_pnl",0):+.2f}
  Trades (MR)      : {r.get("trades_mr", 0)}  (TP={r.get("mr_tp",0)} SL={r.get("mr_sl",0)} TMO={r.get("mr_tmo",0)})  WR {(r.get("mr_wins",0)/r["trades_mr"]*100) if r.get("trades_mr") else 0:.0f}%
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
  ──
  Total Return     : {r["total_return"]:+.2f}%  {ret_ok}
  Monthly estimate : {r["monthly_est"]:+.2f}%
  Final Balance    : ${r["final_balance"]:.2f}
  Max Drawdown     : {r["max_dd"]:+.2f}%  {dd_ok}
  Avg PnL/trade    : ${r["avg_pnl"]:+.3f}
  Confirm rate     : {confirm_rate:.0f}%  ({r["confirm_ok"]} ok / {r["confirm_fail"]} fail)
  Regime (IDLE bar): {regime_str}
════════════════════════════════════════════════════
  VERDICT: {"✅ DEPLOY" if (ps.get("profit_factor") or 0) >= 1.3 and (ps.get("expectancy") or 0) > 0 and r["max_dd"] >= -20 else "❌ NEEDS TUNING"}   (position-based PF≥1.3 + positive expectancy + MaxDD≥-20%)
""")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BreakoutBot Backtest")
    parser.add_argument("--tokens", nargs="+", default=["SOLUSDT"],
                        help="Token(s) to test (Binance USDM format, e.g. SOLUSDT ETHUSDT)")
    parser.add_argument("--days",   type=int,   default=30)
    parser.add_argument("--balance",type=float, default=INITIAL_BALANCE)
    parser.add_argument("--all",    action="store_true",
                        help="Run all 30 tokens from config.TOKENS")
    parser.add_argument("--beta",   action="store_true",
                        help="Print beta ranking vs BTC after backtests")
    parser.add_argument("--no-btc-gate", action="store_true",
                        help="Disable BTC macro gate (compare vs gated run)")
    args = parser.parse_args()

    tokens = TOKENS if args.all else args.tokens

    # ── Fetch BTC reference data once ─────────────────────────────────────
    btc_df: pd.DataFrame | None = None
    if BTC_GATE_ENABLED and not args.no_btc_gate:
        print("📡 Fetching BTC reference data for macro gate…")
        try:
            btc_df = fetch_history("BTCUSDT", args.days)
        except Exception as e:
            print(f"  ⚠️  BTC fetch failed ({e}) — gate disabled for this run", file=sys.stderr)
            btc_df = None
    else:
        print("ℹ️  BTC macro gate disabled.")

    all_results: list[dict] = []
    prefetched:  dict[str, pd.DataFrame] = {}   # reuse for beta ranking

    for tok in tokens:
        try:
            # Skip second BTC fetch — reuse already-fetched gate data
            pre_df = btc_df if (tok == "BTCUSDT" and btc_df is not None) else None
            r = run_symbol(tok, args.days, args.balance, btc_df=btc_df, df=pre_df)
            print_report(r)
            prefetched[tok] = r.pop("_df")      # stash, don't print in report
            all_results.append(r)
        except Exception as e:
            print(f"\n  ❌ {tok} failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    if len(all_results) > 1:
        n_ok          = sum(1 for r in all_results if r["total_return"] > 0)
        total_monthly = sum(r["monthly_est"] * args.balance / 100 for r in all_results)
        total_tpd     = sum(r["trades_per_day"] for r in all_results)
        avg_wr        = sum(r["win_rate"] for r in all_results) / len(all_results)
        total_gates   = sum(r.get("btc_gate_blocks", 0) for r in all_results)
        print(f"\n{'═'*52}")
        print(f"  COMBINED ({len(all_results)} symbols, {n_ok} profitable)")
        print(f"  Total trades/day : {total_tpd:.1f}")
        print(f"  Avg Win Rate     : {avg_wr:.1f}%")
        print(f"  Monthly est ($)  : ${total_monthly:+.2f}")
        print(f"  BTC gate blocks  : {total_gates} total across all symbols")
        print(f"{'═'*52}")

    # ── Optional beta ranking (reuses fetched data — no extra network calls) ──
    if args.beta and btc_df is not None:
        print_beta_ranking(tokens, args.days, btc_df, prefetched=prefetched)
