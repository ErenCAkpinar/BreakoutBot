"""Closed Binance Futures candles up to a cut time — the look-ahead guard lives here.

Klines are requested with `endTime = cut - 1`, then every candle whose close is
after the cut (a still-forming 1h/4h bar, for instance) is dropped, and the
result is asserted once more. The review may run minutes after the entry; the
data it sees must still end at the entry bar.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
import urllib.parse
import urllib.request

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
SPAN_MS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000}

FetchJSON = Callable[[str], Any]


class LookaheadError(RuntimeError):
    """A candle that closes after the cut reached the snapshot."""


@dataclass(frozen=True)
class Candle:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def http_json(url: str, timeout: float = 15.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "breakoutbot-ai-shadow/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed https host
        return json.loads(response.read())


def assert_closed(candles: list[Candle], interval: str, cut_ms: int) -> None:
    span = SPAN_MS[interval]
    late = [c.open_ms for c in candles if c.open_ms + span > cut_ms]
    if late:
        raise LookaheadError(f"{interval}: {len(late)} candle(s) close after the cut")


def fetch_closed(symbol: str, interval: str, cut_ms: int, limit: int,
                 fetch: FetchJSON = http_json) -> list[Candle]:
    """The last `limit` candles of `interval` that had closed by `cut_ms`."""
    span = SPAN_MS[interval]
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval,
                                    "endTime": cut_ms - 1, "limit": min(limit + 1, 1500)})
    rows = fetch(f"{KLINES_URL}?{query}")
    if not isinstance(rows, list):
        raise ValueError(f"{symbol} {interval}: unexpected kline payload")
    candles = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
               for r in rows]
    candles = sorted((c for c in candles if c.open_ms + span <= cut_ms), key=lambda c: c.open_ms)
    candles = candles[-limit:]
    assert_closed(candles, interval, cut_ms)
    if len(candles) < min(limit, 30):
        raise ValueError(f"{symbol} {interval}: only {len(candles)} closed candles")
    return candles
