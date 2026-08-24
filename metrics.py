"""
Position-based performance metrics — the single source of truth.

WHY THIS EXISTS
---------------
The trade log records each exit leg separately. A momentum position that reaches TP1
closes 50% there and the remainder later (TP2 / TRAIL / TIMEOUT), so ONE position
emits TWO records. Both are profitable by construction — after TP1 the trailing
stop already sits above entry — so counting records instead of positions
double-counts every winner and inflates the win rate.

Measured on 44 days of live testnet data (52 records):
    record-based : WR 57.7%  payoff 0.60      <- what we used to report
    position-based: WR 43.3%  payoff 1.08      <- reality (break-even needs 48.2%)

The identity TP1 == TP2 + TRAIL held exactly (13 == 2 + 11), which is the
fingerprint of the double count.

Everything that reports performance — the live bot, the backtest, the public
dashboard — must aggregate to positions first. That is what this module does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# Legs that OPEN a partial exit sequence (position stays alive after them).
PARTIAL_EXITS = {"TP1"}
# Legs that terminate a position.
FINAL_EXITS = {"TP2", "TRAIL", "SL", "TIMEOUT", "TMO"}
# Mean-reversion sleeve exits are always single-leg positions.
MR_EXITS = {"MR_TP", "MR_SL", "MR_TIMEOUT", "MR_TMO"}
# Probe legs: the $20 test position that either failed confirmation, stopped out
# before it could confirm, or was rolled into a full position. Real closed
# round-trips with real cost, but NOT momentum positions — keeping them in their
# own sleeve stops them diluting momentum expectancy while still making the
# filter's cost visible (T0).
PROBE_EXITS = {"CONFIRM_FAIL", "CONFIRM_OK", "PROBE_SL"}


def _get(rec: Any, *names: str, default=None):
    """Read a field from either a dataclass/object or a dict record."""
    for n in names:
        if isinstance(rec, dict):
            if n in rec and rec[n] is not None:
                return rec[n]
        elif hasattr(rec, n):
            v = getattr(rec, n)
            if v is not None:
                return v
    return default


@dataclass
class Position:
    """One round-trip position, with all of its exit legs folded in."""
    symbol: str
    sleeve: str                       # "MOMENTUM" | "MR"
    pnl: float                        # net across every leg
    exit_type: str                    # the FINAL leg's exit type
    legs: list[str] = field(default_factory=list)
    partial: bool = False             # True if it scaled out at TP1 first

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


def aggregate_positions(trades: Iterable[Any]) -> list[Position]:
    """Fold a chronological sequence of exit records into positions.

    Accepts strategy.Trade objects, paper_bb trade_log dicts, or track-record
    JSON dicts — any record exposing symbol / exit_type / pnl.

    A TP1 leg opens a pending position for that symbol; the next terminating leg
    on the same symbol closes it. Everything else is a single-leg position.
    Records with exit_type "OPEN" (position-opened markers) are ignored.

    Note the SL-after-TP1 case is handled even though the current strategy makes
    it unreachable (the breakeven stop is dead code in the TRAILING branch) — if
    that gets fixed, this aggregator stays correct.
    """
    positions: list[Position] = []
    pending: dict[str, Position] = {}     # symbol -> partially closed position

    for rec in trades:
        exit_type = str(_get(rec, "exit_type", default="") or "")
        if exit_type == "OPEN" or not exit_type:
            continue

        symbol = str(_get(rec, "symbol", default="?"))
        pnl = float(_get(rec, "pnl", default=0.0) or 0.0)
        # paper_bb's live trade_log tags MR legs as {"direction": "MR"} and writes
        # their exit types as plain TP/SL/TIMEOUT, so neither `kind`/`sleeve` nor
        # MR_EXITS matches on live data — every MR leg used to read as MOMENTUM,
        # and MR "TP" matched no exit set at all. Reading `direction` too fixes
        # both. ("LONG"/"SHORT" never equal "MR", so momentum is unaffected.)
        kind = str(_get(rec, "kind", "sleeve", "direction", default="") or "").upper()
        if kind == "PROBE" or exit_type in PROBE_EXITS:
            sleeve = "PROBE"
        elif kind == "MR" or exit_type in MR_EXITS:
            sleeve = "MR"
        else:
            sleeve = "MOMENTUM"

        if sleeve == "PROBE":
            positions.append(Position(symbol, "PROBE", pnl, exit_type, [exit_type]))
            continue

        if sleeve == "MR":
            positions.append(Position(symbol, "MR", pnl, exit_type, [exit_type]))
            continue

        if exit_type in PARTIAL_EXITS:
            # Scale-out leg: hold the position open, keep accumulating.
            if symbol in pending:            # defensive: two TP1s without a close
                positions.append(pending.pop(symbol))
            pending[symbol] = Position(symbol, "MOMENTUM", pnl, exit_type,
                                       [exit_type], partial=True)
            continue

        if exit_type in FINAL_EXITS:
            pos = pending.pop(symbol, None)
            if pos is not None:              # completes an earlier TP1
                pos.pnl += pnl
                pos.exit_type = exit_type
                pos.legs.append(exit_type)
                positions.append(pos)
            else:                            # straight-to-exit, never scaled out
                positions.append(Position(symbol, "MOMENTUM", pnl, exit_type, [exit_type]))
            continue

        # Unknown exit type — count it as its own position rather than dropping it.
        positions.append(Position(symbol, sleeve, pnl, exit_type, [exit_type]))

    # A position still scaled-out at the end of the window is genuinely open;
    # excluding it keeps closed-trade statistics honest.
    return positions


def _expectancy_r(positions: list[Position], risk_by_sleeve: dict[str, float]) -> float:
    """Mean R across positions, each converted at its OWN sleeve's dollar risk."""
    if not positions:
        return 0.0
    total = 0.0
    for p in positions:
        risk = risk_by_sleeve.get(p.sleeve)
        if not risk:
            continue                      # unknown sleeve: cannot express in R
        total += p.pnl / risk
    return total / len(positions)


def _by_sleeve(positions: list[Position],
               risk_by_sleeve: dict[str, float]) -> dict[str, dict]:
    """Per-sleeve n / expectancy / expectancy_r, so a blended number is never
    mistaken for a single strategy's edge (M2)."""
    out: dict[str, dict] = {}
    for sleeve in sorted({p.sleeve for p in positions}):
        sub  = [p for p in positions if p.sleeve == sleeve]
        risk = risk_by_sleeve.get(sleeve)
        exp  = sum(p.pnl for p in sub) / len(sub)
        out[sleeve] = {
            "n": len(sub),
            "risk_per_trade": risk,
            "expectancy": round(exp, 2),
            "expectancy_r": round(exp / risk, 3) if risk else None,
            "win_rate": round(100.0 * sum(1 for p in sub if p.is_win) / len(sub), 1),
            "pnl": round(sum(p.pnl for p in sub), 2),
        }
    return out


def position_stats(positions: list[Position], risk_per_trade: float | None = None,
                   risk_by_sleeve: dict[str, float] | None = None) -> dict:
    """Win rate, payoff, expectancy and profit factor — computed per POSITION.

    `risk_per_trade` (the fixed dollar risk, e.g. RISK_PER_TRADE_USD) turns the
    dollar figures into R-multiples, which is the scale-free way to compare arms.

    `risk_by_sleeve` (M2) maps sleeve -> that sleeve's dollar risk. When supplied,
    every position is converted at ITS OWN risk before averaging, so a pooled
    momentum($10) + MR($5) set yields a real R-multiple instead of dollars-over-ten
    across two systems. The per-sleeve breakdown lands in `by_sleeve`.
    """
    n = len(positions)
    if n == 0:
        return {"n_positions": 0, "win_rate": 0.0, "payoff": None, "expectancy": 0.0,
                "profit_factor": None, "breakeven_wr": None, "avg_win": 0.0,
                "avg_loss": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
                "expectancy_r": None, "partial_rate": 0.0}

    wins = [p.pnl for p in positions if p.is_win]
    losses = [p.pnl for p in positions if not p.is_win]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    avg_w = gross_w / len(wins) if wins else 0.0
    avg_l = gross_l / len(losses) if losses else 0.0
    wr = len(wins) / n * 100.0
    expectancy = sum(p.pnl for p in positions) / n

    payoff = (avg_w / avg_l) if avg_l > 0 else None
    # Win rate at which expectancy turns zero for this payoff.
    breakeven = (avg_l / (avg_w + avg_l) * 100.0) if (avg_w + avg_l) > 0 else None

    return {
        "n_positions": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(wr, 1),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(-avg_l, 2),
        "payoff": round(payoff, 2) if payoff is not None else None,
        "expectancy": round(expectancy, 2),
        "expectancy_r": (round(_expectancy_r(positions, risk_by_sleeve), 3)
                         if risk_by_sleeve
                         else (round(expectancy / risk_per_trade, 3) if risk_per_trade else None)),
        "by_sleeve": _by_sleeve(positions, risk_by_sleeve) if risk_by_sleeve else None,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "breakeven_wr": round(breakeven, 1) if breakeven is not None else None,
        "gross_win": round(gross_w, 2),
        "gross_loss": round(-gross_l, 2),
        "partial_rate": round(100.0 * sum(1 for p in positions if p.partial) / n, 1),
    }


def exit_distribution(positions: list[Position]) -> dict[str, int]:
    """How positions ended, keyed by their FINAL leg."""
    out: dict[str, int] = {}
    for p in positions:
        out[p.exit_type] = out.get(p.exit_type, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def format_report(positions: list[Position], risk_per_trade: float | None = None) -> str:
    """Compact block for the backtest/live report."""
    s = position_stats(positions, risk_per_trade)
    if not s["n_positions"]:
        return "  Positions        : 0"
    dist = " ".join(f"{k}={v}" for k, v in exit_distribution(positions).items())
    be = s["breakeven_wr"]
    verdict = "✅" if (be is not None and s["win_rate"] > be) else "❌"
    lines = [
        f"  Positions        : {s['n_positions']}  ({dist})",
        f"  Win Rate (pos)   : {s['win_rate']}%   [break-even {be}%]  {verdict}",
        f"  Avg win / loss   : ${s['avg_win']:+.2f} / ${s['avg_loss']:+.2f}   payoff {s['payoff']}",
        f"  Expectancy/pos   : ${s['expectancy']:+.2f}"
        + (f"  ({s['expectancy_r']:+.3f} R)" if s["expectancy_r"] is not None else ""),
        f"  Profit Factor    : {s['profit_factor']}   (position-based)",
    ]
    return "\n".join(lines)
