"""Read FULL OPEN entries and the position book out of the bot's trade log.

Everything here is derived from `state_paper.json` → `trade_log`, which the bot
appends in bar order. Only legs stamped at or before a candidate's bar count as
known at that moment; later legs are ignored, so nothing the bot learned after
the entry can leak into the review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

BAR_MS = 300_000
TERMINAL_EXITS = frozenset({"TP2", "SL", "TRAIL", "TIMEOUT"})


@dataclass(frozen=True)
class Candidate:
    signal_id: str
    symbol: str
    direction: str
    cut_ms: int        # close of the entry bar = the latest moment data may come from
    cut_iso: str
    entry: float
    entry_fee: float   # the OPEN leg's pnl (negative)
    balance: float     # account balance right after the entry fee


@dataclass(frozen=True)
class ClosedPosition:
    symbol: str
    direction: str
    opened: str
    closed: str
    exit_type: str
    pnl: float         # entry fee + every partial + the final exit


def ts_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_full_open(leg: dict[str, Any]) -> bool:
    return (leg.get("sleeve") == "MOMENTUM" and leg.get("kind") == "FULL"
            and leg.get("exit_type") == "OPEN")


def full_opens(trade_log: Iterable[Any]) -> list[Candidate]:
    """Every FULL OPEN leg, oldest first. Malformed legs are skipped, not guessed."""
    out: list[Candidate] = []
    for leg in trade_log:
        if not isinstance(leg, dict) or not _is_full_open(leg):
            continue
        try:
            ts = str(leg["ts"])
            # The bot stamps legs on the 5m boundary; flooring only ever removes data.
            cut = ts_ms(ts) // BAR_MS * BAR_MS
            out.append(Candidate(
                signal_id=f"{leg['symbol']}@{iso_utc(cut)}",
                symbol=str(leg["symbol"]), direction=str(leg["direction"]),
                cut_ms=cut, cut_iso=iso_utc(cut), entry=float(leg["entry"]),
                entry_fee=float(leg["pnl"]), balance=float(leg["balance"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def book_at(trade_log: Iterable[Any], cut_ms: int,
            exclude: str | None = None) -> tuple[list[ClosedPosition], list[dict[str, Any]]]:
    """Momentum positions closed by `cut_ms`, and those still open at it.

    `exclude` is the candidate's own signal_id, so it is not listed as an
    already-open position next to itself.
    """
    open_by_symbol: dict[str, dict[str, Any]] = {}
    closed: list[ClosedPosition] = []
    for leg in trade_log:
        if not isinstance(leg, dict) or leg.get("sleeve") != "MOMENTUM":
            continue
        try:
            leg_ms = ts_ms(str(leg["ts"]))
            symbol, pnl = str(leg["symbol"]), float(leg["pnl"])
        except (KeyError, TypeError, ValueError):
            continue
        if leg_ms > cut_ms:
            continue
        if _is_full_open(leg):
            open_by_symbol[symbol] = {"symbol": symbol, "direction": str(leg.get("direction", "")),
                                      "opened": iso_utc(leg_ms // BAR_MS * BAR_MS),
                                      "entry": leg.get("entry"),
                                      "pnl": pnl}
        elif symbol in open_by_symbol:
            pos = open_by_symbol[symbol]
            pos["pnl"] += pnl
            if leg.get("exit_type") in TERMINAL_EXITS:
                closed.append(ClosedPosition(symbol, pos["direction"], pos["opened"],
                                             iso_utc(leg_ms), str(leg["exit_type"]),
                                             round(pos["pnl"], 4)))
                del open_by_symbol[symbol]
    still_open = [
        {k: v for k, v in pos.items() if k != "pnl"}
        for pos in open_by_symbol.values()
        if f"{pos['symbol']}@{pos['opened']}" != exclude
    ]
    return closed, still_open
