"""
BreakoutBot — Binance Futures Testnet order execution (RETIRED PATH).

Nothing in the running system imports this module's behaviour: the live bot is
a pure local simulation (`paper_bb.py` with no --testnet), no API key is loaded,
and no order ever reaches an exchange. It is kept, isolated in its own module,
because the retired v1 ran against the testnet and the execution details are
the only record of how that worked.

It is reachable only via `paper_bb.py --testnet`, which additionally requires a
`secrets_local.py` that is gitignored and absent on the server.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests as _requests  # type: ignore[import-untyped]  # stubs not installed

from config import LEVERAGE


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

    def _get(self, path: str, params: dict | None = None) -> Any:
        p = self._sign(params or {})
        r = _requests.get(f"{self.BASE}{path}", params=p,
                          headers=self._hdr(), timeout=8)
        return r.json()

    def _post(self, path: str, params: dict) -> Any:
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

