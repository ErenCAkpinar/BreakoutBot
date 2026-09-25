"""One model call → a decision or a categorised NO_DATA. Never raises.

No server-side fallback model on purpose: a refusal answered by a different
model would silently change the pre-registered arm (DEFTER Tur 15). A refusal
is recorded as NO_DATA like any other miss.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import time
from typing import Any

import anthropic

# $ per million tokens (input, output). Thinking tokens bill as output.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
RECOMMENDATIONS = ("ALLOW", "VETO_RECOMMENDED")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return round((input_tokens * price_in + output_tokens * price_out) / 1e6, 6)


@dataclass
class Outcome:
    status: str                       # "decided" | "no_data"
    reason: str | None = None         # set when status == "no_data"
    decision: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str | None = None
    request_id: str | None = None
    api_seconds: float = 0.0
    raw_text: str | None = field(default=None, repr=False)


def _error_reason(exc: Exception) -> str:
    # Most specific first: APITimeoutError subclasses APIConnectionError.
    if isinstance(exc, anthropic.AuthenticationError):
        return "auth_401"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "permission_403"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit_429"
    if isinstance(exc, anthropic.BadRequestError):
        return "credits" if "credit" in str(exc).lower() else "bad_request_400"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "network"
    if isinstance(exc, anthropic.APIStatusError):
        return f"api_{exc.status_code}"
    return f"error_{type(exc).__name__}"


def validate(payload: Any) -> dict[str, Any] | None:
    """The schema is enforced by the API; this is the second, local check."""
    if not isinstance(payload, dict) or payload.get("recommendation") not in RECOMMENDATIONS:
        return None
    conf = payload.get("confidence")
    factors = payload.get("key_factors")
    reasoning = payload.get("reasoning")
    if (not isinstance(conf, (int, float)) or isinstance(conf, bool) or not math.isfinite(conf)
            or not isinstance(factors, list) or not all(isinstance(f, str) for f in factors)
            or not isinstance(reasoning, str)):
        return None
    return {"recommendation": payload["recommendation"],
            "confidence": min(1.0, max(0.0, float(conf))),
            "confidence_out_of_range": not 0.0 <= conf <= 1.0,
            "key_factors": [f[:500] for f in factors[:8]],
            "reasoning": reasoning[:2000]}


def decide(client: Any, *, model: str, effort: str, system: str, user: str,
           schema: dict[str, Any], max_tokens: int = 16_000) -> Outcome:
    started = time.monotonic()
    try:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": schema}},
        )
    except Exception as exc:  # noqa: BLE001 — every failure must become a NO_DATA record
        return Outcome("no_data", _error_reason(exc), api_seconds=time.monotonic() - started)

    usage = getattr(resp, "usage", None)
    tin = int(getattr(usage, "input_tokens", 0) or 0)
    tout = int(getattr(usage, "output_tokens", 0) or 0)
    out = Outcome("no_data", None, input_tokens=tin, output_tokens=tout,
                  cost_usd=cost_usd(model, tin, tout), stop_reason=getattr(resp, "stop_reason", None),
                  request_id=getattr(resp, "_request_id", None) or getattr(resp, "id", None),
                  api_seconds=round(time.monotonic() - started, 2))
    if out.stop_reason in ("refusal", "max_tokens"):
        out.reason = out.stop_reason
        return out
    text = next((b.text for b in getattr(resp, "content", []) if getattr(b, "type", "") == "text"), None)
    out.raw_text = text
    try:
        decision = validate(json.loads(text)) if text is not None else None
    except ValueError:
        decision = None
    if decision is None:
        out.reason = "invalid_output"
        return out
    out.status, out.decision = "decided", decision
    return out
