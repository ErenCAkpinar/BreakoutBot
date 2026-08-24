# TODOS — BreakoutBot

Deferred work with the reasoning that produced it. Every phase decision and its
rationale lives in `BENCHMARKS.md`; this file holds work **not yet done**.

Created 2026-08-23 from `/plan-ceo-review`; revised 2026-08-24 after `/plan-eng-review`
(including an outside-voice pass that ran the harness and measured its assumptions).
Full analysis: `~/.gstack/projects/ErenCAkpinar-BreakoutBot/ceo-plans/2026-08-23-faz6-recuration.md`

---

## 🚨 P0 — DO FIRST

### S0 — Harden the VM and back up the evidence
Before any code work. `root` SSH by **password** on a public IPv4 (a failed auth attempt
was observed in-session), 19 pending OS updates + 3 ESM security updates, "System restart
required", track record served over plain HTTP, and the **only copy** of the dead bot's
`state_paper.json` (99 legs of `--testnet` evidence) sitting unbacked-up on that box.

Everything else here is measurement work on a testnet bot with no real money. This is the
only item with an irreversible downside.
`ssh-copy-id` → `PermitRootLogin prohibit-password` → `PasswordAuthentication no` →
`unattended-upgrades` → reboot → TLS. Under an hour.
**Effort:** S (human ~1h) · **Priority:** P0

### M1 — Regime warm-up fabricates a NEUTRAL month in every backtest window
`regime.py:82` sets `score = 0.0` while the 200-period **4h** MA warms (`MA_PERIOD=200`
+ `SLOPE_LOOKBACK=10` ≈ **33 days**). `regime.backtest_regimes` (`:116`) computes that
series from **the window's own 5m data only**, and `backtest.py:150` warms up just 65 **5m**
bars. Since `LONG_SIZE_MULT["NEUTRAL"] = 0.00`, **momentum trades zero positions for the
first ~33 days of every window** — and because MR is gated to NEUTRAL, that fabricated
month is handed entirely to the MR sleeve.

~37% of a 90d window, ~14% of 240d. **Every number in
`backtests/curation_2026-08-22_fresh90.txt` and `_fresh240.txt` is affected**, and so is
every fold of any walk-forward built on the current code.
**Fix:** compute regimes once on a superset series, then slice per window (~20 lines).
**Effort:** M (human ~1d / CC ~45min) · **Priority:** P0 · *found by outside voice, verified*

### M2 — `expR` is not an R-multiple
`backtest.py:309` pools `full_closed + mr_trades + short_trades` into `_positions`;
`curate.py:114` scores all of it at `risk_per_trade=RISK_PER_TRADE_USD` ($10) while MR
risks `MR_RISK_PER_TRADE_USD` ($5). The ranking number is therefore dollars-over-ten across
a roughly 50/50 blend of two strategies with different unit risk.

`config.py` cites NEAR's `n=48` as "the largest sample in the set" and the reason not to cut
it — roughly half of that n is mean-reversion.
**Fix:** split `_positions` by sleeve, score each at its own risk (~5 lines).
**Effort:** S (human ~4h / CC ~20min) · **Priority:** P0 · *found by outside voice, verified*

### T0 — Record OPEN-leg fees (re-scoped: this is not a mystery)
Every OPEN leg debits `notional × EXEC_COST_PER_SIDE` from `balance`, then
`paper_bb.py:717` `continue`s before the `trade_log.append` at `:759`. **Verified on the
local state file:** one leg, logged pnl −$9.3964, balance delta −$10.7336, gap −$1.3372;
implied notional exactly **$900.00** → entry fee **$0.6750** + test-open fee **$0.0150**
= **$0.69**, i.e. ~52% of that gap, with the remainder being other symbols' probe legs.

At ~$0.70–1.10 per full open, ~40–80 positions accounts for both bots' gaps
(−$53.41 old, −$33.35 test). **The earlier 3-hour log-parse plan is unnecessary** — sum the
notionals from the OPEN lines × 0.00075 and add `probe_cost`.

⚠️ **E0's reconciliation assert will hard-fail on all pre-fix history** — those fees were
never recorded per-trade and cannot be recovered. Scope the assert to trades after a
cutover marker, or the exporter dies on first run.
**Effort:** S (human ~2h / CC ~20min) · **Priority:** P0

---

## 🔴 P1 — Correctness bugs

### E7 — Adverse-fill convention (intrabar **and** same-bar trail)
Two separate optimisms in `strategy.py`:
1. `tp1_hit` is checked before `sl_hit`; `tp2_hit` before `trail_hit`. A bar touching both
   levels resolves in your favour.
2. The TRAILING branch raises `trail_best` from **the current bar's high**, then tests
   `trail_hit` against **that same bar's low**. Within one bar you cannot know which came
   first.

Under EXIT-E6 (`TP1_CLOSE_FRAC=0.0`) nearly every position terminates in TRAIL, so #2
prices the majority of the deployed system's PnL. Promoted from P2 after the outside voice.
**Fix:** within any bar where both levels are touched, the adverse one fills first.
**Note:** makes all new numbers non-comparable to every published figure including +0.249R —
mark the seam in `BENCHMARKS.md`.
**Effort:** M (human ~1d / CC ~1h) · **Priority:** P0/P1

### E16 — Universe shrink orphans open positions
`_load_state` skips tokens absent from `sym_states`, and `sym_states` is built from
`tokens`. Drop a coin with an open position and it vanishes: no closing leg in `trade_log`,
unrealized PnL never booked, and on the `--testnet` bot **the exchange position stays open**.
This already happened on the server on 2026-08-22 during the 8→5 shrink — it is a live
candidate for part of the test bot's −$33.35 gap.
**Effort:** S (human ~2h / CC ~15min) · **Priority:** P1

### T-M1 — `metrics.py` misclassifies every live MR leg as MOMENTUM
`metrics.py:88` reads `kind` via `_get(rec, "kind", "sleeve")`, but the live `trade_log`
writes `"direction": "MR"` (`paper_bb.py:809`). MR exit types are `TP`/`SL`/`TIMEOUT` while
`MR_EXITS` expects `MR_TP`/`MR_SL`, and MR `"TP"` matches neither `PARTIAL_EXITS` nor
`FINAL_EXITS` — those legs fall through entirely. The published site is unaffected because
the exporter adds `sleeve` first, which is exactly why this survived.
**Effort:** S (human ~1h / CC ~10min) · **Priority:** P1

### T-M2 — `close_market()` returns 0.0 on failure
`paper_bb.py:220` catches `Exception` and returns `0.0`, recording a close that never
happened while the exchange still holds the position.
**Effort:** S (human ~4h / CC ~30min) · **Priority:** P1

### E19 — Two more silent-default rescues
`paper_bb.py:437` state-load failure silently starts fresh at $1000 (with
`Restart=on-failure`, a truncated write destroys history). `:162` balance fetch returns
`0.0`, so a network blip is indistinguishable from a wiped account.
**Effort:** S (human ~2h / CC ~15min) · **Priority:** P2

---

## 🟠 P1 — Harness design constraints found by measurement

### E17 — Every pre-registered threshold is below the noise floor
~168 positions / 240d / 5 coins ≈ **42 per 60-day fold**; per-position R dispersion ≈ 1R
(WR 50%, payoff ~2.1) → **SE ≈ 0.15–0.2R per fold**. Rule 7A fires on a **0.05R**
difference. Resolving 0.05R at 1R noise needs on the order of 1,600 positions per arm.
**Run a power analysis before fixing any threshold**, and re-derive D9/7A after M1+M2 land —
pre-registering on a metric you already know is wrong just makes a wrong answer harder to
walk back.
**Effort:** S (human ~4h / CC ~30min) · **Priority:** P1 · *outside voice*

### E4 — Embargo is 96 bars, not 48
`bars_held` resets on the TP1 transition, so SCALE_OPEN(48) + TRAILING(48) = **96 bars**
max hold (measured max: 72). The 33-day regime-warm-up leakage horizon is fixed by M1, not
by the embargo.
**Effort:** included in E4 · **Priority:** P1

### E18 — The candidate pool was pre-filtered by the screen the repo calls anti-predictive
`curation_2026-08-22_fresh240.txt` ranks **14** symbols, not 23. The missing 9 were dropped
on their **90d** rank — while `config.py:64` states 90d-vs-240d rank correlation is
**−0.31** and "every candidate that screened well on 90d flipped negative on 240d."
So the deployed 5 are not the output of the rule the harness will test.
Report exclusions explicitly instead of silently omitting.
**Effort:** S (human ~2h / CC ~15min) · **Priority:** P2 · *outside voice*

---

## 🟠 P2 — Deferred from the Faz 6 review

### E1 — Re-curate the mean-reversion sleeve under EXIT-E6
**What:** Run the curation harness against the MR sleeve.
**Why:** `paper_bb.py:292` sets one `self.tokens` that **both** sleeves iterate;
`mean_reversion.py` has no universe of its own. Faz 6 therefore re-scoped MR from 8
coins to 5 as a side effect, without measuring it. MR was validated back in Faz 2 on
SOL/INJ/FET — SOL is now dropped, and FET tests −0.073R on 240d under EXIT-E6.
**Why deferred:** live MR is −$6.31 over 15 trades (test bot) — a mild drag, not a hole.
**⚠️ Gate:** that −$6.31 comes from the same export T0 shows is incomplete. **Re-evaluate
once T0 lands.** Original gate ("if probes attribute disproportionately to MR") was
invalid — probes are momentum-only (`CONFIRM_FAIL` exists solely in `strategy.py:170`;
MR has no probe stage). Re-gate numerically: *promote to P1 if MR reconciled expectancy
is below −0.1R over ≥25 positions.*
**Note:** the old bot's sleeve split **is** recoverable via `direction == "MR"`.
**Effort:** M (human ~1d / CC ~1h) · **Depends on:** T0

### E9 — Generalise fill pessimism beyond the intrabar case
**What:** Make every ambiguous fill resolve against you, so the backtest is a floor.
**Why:** beyond `tp1_hit`/`sl_hit`, the TRAILING branch checks `tp2_hit` before
`trail_hit`, and timeout exits use the bar midpoint `(high+low)/2`. Both lean optimistic.
**Why deferred:** Approach C already fixes the one instance that was measured.
**Effort:** M (human ~1d / CC ~1h)

### E7 — Adaptive risk budget approaching the hard stop
**What:** Scale `RISK_PER_TRADE_USD` down as peak DD approaches `PEAK_DD_LIMIT = −0.15`,
so the guard is asymptotically approached rather than hit.
**Why deferred:** changes live trading behaviour, and the −7% throttle demonstrably works
(4 `THROTTLE_ON` events, correct release 2026-08-23).
**Effort:** M (human ~1d / CC ~1h)

### UNI concentration risk
**What:** Decide whether a universe whose live PnL is carried by one coin is acceptable.
**Why:** of the kept 5, live PnL is +$64.59 — of which UNI is +$73.15, so the other four
are **−$8.56** combined. Only 2 of 5 were profitable live (UNI, POL). "Curation works" and
"we got one coin right" are currently observationally equivalent.
**Revisit under:** the D8 null-arm result (curated-5 vs equal-weight, out-of-sample).
**Effort:** S to decide · **Priority:** P2

### Old bot disposition
**What:** `breakoutbot.service` is `inactive`/`disabled` but its `~/BreakoutBot/config.py`
still holds the 8-coin universe **and** it has real testnet order authority. If it ever
restarts it resumes trading LDO/SOL/AVAX.
**Fix:** `systemctl disable --now breakoutbot` explicitly, or deploy the 5-coin config to
it too. Back up its state first — 99 legs of `--testnet` evidence exist nowhere else.
**Effort:** S (human ~30min / CC ~5min) · **Priority:** P2

---

## 🟡 P2 — Infrastructure & security

### VM hardening
- `root` SSH by **password** on a public IPv4 (a failed auth attempt was observed
  in-session — port 22 gets brute-forced continuously)
- 19 pending OS updates + 3 ESM security updates; "System restart required"
- track record served over **plain HTTP** on a bare IP — no TLS, no domain
- the only copy of the dead bot's `state_paper.json` is unbacked-up on that box

**Fix:** key-only auth, `PermitRootLogin prohibit-password`, `unattended-upgrades`,
reboot, TLS. Under an hour, and it gates any public release.
**Note:** repo hygiene is clean — `secrets_local.py` gitignored, VM IP **not** in git
history, `deploy_test.sh` parameterises the host via `$BREAKOUTBOT_SERVER`.
**Effort:** S (human ~1h / CC —, manual) · **Priority:** P2

### Production runs uncommitted code
**What:** `deploy_test.sh` `scp`s 8 files directly. The server's `config.py` has the
curated 5; the committed `config.py` still has 8. **No commit corresponds to what is
running.** If the VM dies, the deployed system cannot be reconstructed from the repo.
**Fix:** refuse to deploy from a dirty tree; record the deployed SHA on the server.
**Effort:** S (human ~2h / CC ~15min) · **Priority:** P2

---

## 🟢 P3 — Hygiene

- **DRY:** `curate.py:load()` duplicates `bench.py:load_data()`. `bench.py` already
  supports `BENCH_TOKENS` / `BENCH_DAYS`. Extract a shared `load_ohlcv()`.
- **Fill-ordering comments:** `strategy.py` encodes fill assumptions implicitly in
  `if`/`elif` order. That is how the intrabar optimism hid. Comment the convention.
- **Silent swallows:** `indicators.py:125` and `backtest.py:119` catch `Exception` with
  **no logging at all**.
- **Site states:** the track record page has only a `Loading…` state — nothing for fetch
  failure, stale data, or `hard_stopped=True`. The hard stop firing is the most important
  moment in the product's life and has no design.
- **`risk_events[].balance` is `None`** on all 10 entries, so the circuit-breaker
  timeline — the page's stated moat — cannot show the equity level at each trip.
