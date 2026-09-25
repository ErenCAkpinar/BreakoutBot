"""The "With AI" twin's reviewer (DEFTER Tur 15, H15.2): same arm as the shadow, but it acts.

Called by strategy._resolve_probe through paper_bb, once per confirmed probe,
right before the FULL position would open. Returns True only for a clean
VETO_RECOMMENDED; every other path — error, timeout, refusal, budget, a bar
that is already too old — returns False, so the twin can only ever trade less
than the bot without AI, never more.

The input is built exactly as the shadow worker builds it (same prompt, schema,
model, effort, candle windows and look-ahead guard), so the twin applies the
policy H15.1 measures rather than a cousin of it.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any, Callable

from .candidates import Candidate, iso_utc
from .features import ROUND_TRIP_FEE
from .llm import Outcome, decide
from .market import FetchJSON, LookaheadError, http_json
from .snapshot import build_input
from .worker import PROMPT, SCHEMA, RECORD_VERSION, _code_version, _sha, append_record, \
    load_records, month_to_date_usd

EST_CALL_USD = 0.30


def _anthropic_client(timeout: float) -> Any:
    import anthropic
    # No retries: the bar loop is waiting, and a retry could outlive the deadline.
    return anthropic.Anthropic(timeout=timeout, max_retries=0)


class LiveVeto:
    def __init__(self, path: str | Path, *, model: str, effort: str, budget_usd: float,
                 timeout_s: float, bar_deadline_s: float, log: Callable[[str], None],
                 client_factory: Callable[[float], Any] = _anthropic_client,
                 fetch: FetchJSON = http_json, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.runs_dir = self.path.parent / "ai_runs"
        self.model, self.effort = model, effort
        self.budget_usd, self.timeout_s, self.bar_deadline_s = budget_usd, timeout_s, bar_deadline_s
        self.log, self.client_factory, self.fetch, self.clock = log, client_factory, fetch, clock
        self.records = load_records(self.path)

    def review(self, prospective: dict[str, Any], *, bar_dt: datetime,
               trade_log: list[dict[str, Any]], balance: float) -> bool:
        try:
            return self._review(prospective, bar_dt, trade_log, balance)
        except Exception as exc:  # noqa: BLE001 — the trading loop must never see an AI error
            self.log(f"  🤖 AI ERROR {prospective.get('symbol')} {type(exc).__name__} → ALLOW (fail-open)")
            return False

    def _review(self, p: dict[str, Any], bar_dt: datetime,
                trade_log: list[dict[str, Any]], balance: float) -> bool:
        cut_ms = int(bar_dt.timestamp() * 1000)
        fee = round(p["full_notional"] * ROUND_TRIP_FEE / 2, 4)
        cand = Candidate(signal_id=f"{p['symbol']}@{iso_utc(cut_ms)}", symbol=p["symbol"],
                         direction=p["direction"], cut_ms=cut_ms, cut_iso=iso_utc(cut_ms),
                         entry=p["full_entry"], entry_fee=-fee, balance=round(balance - fee, 4))
        # The prospective levels stand in for the state the shadow reads after the fact.
        state = {"trade_log": trade_log, "sym_states": {p["symbol"]: dict(p)}}
        rec: dict[str, Any] = {
            "v": RECORD_VERSION, "mode": "active", "signal_id": cand.signal_id,
            "symbol": cand.symbol, "direction": cand.direction, "cut_utc": cand.cut_iso,
            "entry": cand.entry, "model": self.model, "effort": self.effort,
            "prompt_sha256": _sha(PROMPT), "schema_sha256": _sha(SCHEMA), "code": _code_version(),
        }
        remaining = self.bar_deadline_s - (self.clock() - cut_ms / 1000)
        outcome: Outcome
        if remaining < self.timeout_s + 5:
            outcome = Outcome("no_data", "bar_time_budget")
        elif month_to_date_usd(self.records, int(self.clock() * 1000)) + EST_CALL_USD > self.budget_usd:
            outcome = Outcome("no_data", "budget")
        else:
            try:
                user, meta = build_input(cand, state, self.fetch)
            except LookaheadError:
                outcome = Outcome("no_data", "lookahead_guard")
            except Exception as exc:  # noqa: BLE001 — market data failure is a recorded miss
                outcome = Outcome("no_data", f"market_data_{type(exc).__name__}")
            else:
                rec.update(meta)
                self.runs_dir.mkdir(exist_ok=True)
                stem = cand.signal_id.replace(":", "")
                (self.runs_dir / f"{stem}.input.md").write_text(user)
                timeout = min(self.timeout_s, remaining - 5)
                outcome = decide(self.client_factory(timeout), model=self.model, effort=self.effort,
                                 system=PROMPT.read_text(), user=user,
                                 schema=json.loads(SCHEMA.read_text()))
                if outcome.raw_text is not None:
                    (self.runs_dir / f"{stem}.output.json").write_text(outcome.raw_text)

        vetoed = outcome.status == "decided" and \
            (outcome.decision or {}).get("recommendation") == "VETO_RECOMMENDED"
        decided_ms = int(self.clock() * 1000)
        rec.update({
            "status": outcome.status, "no_data_reason": outcome.reason,
            "recommendation": (outcome.decision or {}).get("recommendation", "NO_DATA"),
            "applied": "VETO" if vetoed else "ALLOW",
            "decision": outcome.decision, "decided_utc": iso_utc(decided_ms),
            "latency_s": round((decided_ms - cut_ms) / 1000, 1),
            "api_seconds": round(outcome.api_seconds, 2), "stop_reason": outcome.stop_reason,
            "request_id": outcome.request_id, "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens, "cost_usd": outcome.cost_usd,
        })
        append_record(self.path, rec)
        self.records.append(rec)

        conf = (outcome.decision or {}).get("confidence")
        if vetoed:
            self.log(f"  🤖 AI VETO   {cand.symbol} {cand.direction} @ {cand.entry:.5g} "
                     f"| conf={conf:.2f} | ${outcome.cost_usd:.3f}")
        elif outcome.status == "decided":
            self.log(f"  🤖 AI ALLOW  {cand.symbol} {cand.direction} @ {cand.entry:.5g} "
                     f"| conf={conf:.2f} | ${outcome.cost_usd:.3f}")
        else:
            self.log(f"  🤖 AI NO_DATA {cand.symbol} ({outcome.reason}) → ALLOW (fail-open)")
        return vetoed
