"""
BreakoutBot — backtest output: progress, reports, run dumps.

Everything here formats a result; nothing here decides one. Kept apart from the
replay engine so a change to how a number is PRINTED can never be mistaken for
a change in how it was COMPUTED.

The all-in / exit-only distinction that runs through these reports is the whole
point of them: aggregate_positions ignores "OPEN" legs, so a report that quotes
only exit legs hides the entry fee — the drag that was ~54% of the live loss.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from backtest_data import fetch_history
from config import (BTC_BETA_BLOCK, BTC_BETA_BOOST, BTC_BETA_WINDOW,
                    INITIAL_BALANCE, MAX_OPEN, PEAK_DD_LIMIT)
from indicators import token_beta_vs_btc

# ── Beta ranking ──────────────────────────────────────────────────────────────

def print_beta_ranking(tokens: list[str], days: int, btc_df: pd.DataFrame,
                       prefetched: dict[str, pd.DataFrame] | None = None) -> None:
    """Compute and print each token's beta vs BTC over the test period.

    prefetched : dict of {symbol → DataFrame} from the backtest run (no re-fetch).
    """
    print(f"\n{'═'*56}")
    print(f"  TOKEN BETA vs BTC  ({days}d, {BTC_BETA_WINDOW}-bar rolling)")
    print("  β>2 = amplifies BTC 2×   β<0 = moves opposite to BTC")
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

def print_progress(done: int, total: int, t0: float, bal: float, extra: str = "") -> None:
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


def window_line(r: dict) -> str:
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


def sleeve_line(ps: dict) -> str:
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
  Window           : {window_line(r)}
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
  Window: {window_line(r)}
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
