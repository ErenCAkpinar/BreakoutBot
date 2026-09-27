# BreakoutBot

**English** · [Türkçe](README.tr.md)

> A regime-aware **breakout momentum** crypto bot.
> A 24/7 **simulation**: real Binance Futures price data and a local virtual wallet;
> **no orders ever reach an exchange** (no API key is even loaded). Retired versions
> sent orders to the Binance Futures Testnet; in this repo, "testnet" only refers to them.
> Live record: **[breakoutbot.dev](https://breakoutbot.dev)**

This repo is not "here is my winning bot". The story is more honest:
**I built the system and measured it; when the strategy lost, the risk layer stopped the bot;
I wrote a postmortem, fixed the measurement errors, and I test every change against rules
written down in advance.** Incident analysis: [REPORT.md](REPORT.md) · experiments:
[BENCHMARKS.md](BENCHMARKS.md), [experiments/DEFTER.md](experiments/DEFTER.md)
(these three documents are in Turkish; there, phases are "Faz" and rounds are "Tur").

---

## Current state (25 September 2026)

| | |
|---|---|
| **Mode** | Pure simulation: real prices, virtual wallet, no orders |
| **System** | Current version ("v2" on the site), on record since 29 July 2026 |
| **Strategy** | 4h BTC regime gate (longs only in BULL) + 5-minute breakout; the short and mean-reversion arms are switched off |
| **Exit set** | Phase 8, the `config.py` default since 27 August 2026: SL 2.25×ATR · TP1 3×ATR (no partial close) · TP2 6×ATR · trailing 3.75×ATR · 96-bar timeout |
| **Universe** | 4 coins: ADA, INJ, NEAR, UNI (since 22 September 2026; why POL was dropped is explained in [BENCHMARKS.md](BENCHMARKS.md), Phase 9) |
| **Result** | As of 25 September: 115 closed positions, $1,000 → $946.68 (−5.3%), worst drawdown −13.5%. Current figures: [breakoutbot.dev](https://breakoutbot.dev/without-ai.html) |
| **Active experiment** | AI veto (since 25 September 2026): Claude Opus 5.5 reviews every full entry ([ai_shadow/](ai_shadow/)). The site publishes the account that follows the rules alone next to the copy that skips vetoed entries. The decision rules were written before any data: a first check at 100 closed positions, a decision at 300, against a random veto at the same rate ([DEFTER.md](experiments/DEFTER.md), Tur 15). |

The live result is currently below the backtest expectation (see [Validation](#validation));
the gap is an open research question.

---

## How it works

```
market_data.py (Binance 5m + 4h candles, closed bars only)
        │
indicators.py ──► math_engine.py          regime.py
(RSI, BB, ATR,    (Wave 11 composite      (BTC 200-MA:
 ADX, Hurst)       score 0–100)            BULL/NEUTRAL/BEAR)
        │                 │                     │
        └────────┬────────┘─────────────────────┘
                 ▼
          strategy.py  ◄── mean_reversion.py (MR in the NEUTRAL regime, currently OFF)
   (signal → decision, ◄── short_sleeve.py  (tested, no edge → OFF)
    regime gates)
                 │
                 ▼
           paper_bb.py
   (execution state machine, virtual wallet,
    risk limits, state persistence, 24/7 under systemd)
                 │  read-only
     ┌───────────┼─────────────────────┐
     ▼           ▼                     ▼
  watch.py    ai_shadow/            monitoring/
  (terminal   (AI veto record,      (15-minute AI
   monitor)    Tur 15)               observation report)
```

### Entry architecture: Test → Confirm → Scale

The bot never enters a full position directly; a breakout is first probed with a small amount:

1. **TEST OPEN** — a $20 probe position (is the signal real?)
2. **CONFIRMED / CONF FAIL** — one bar later, a price/volume/RSI confirmation; if it fails, the probe is cancelled
3. **FULL OPEN** — the real position, sized by risk
4. **Exit** — ATR-based SL (2.25×) / TP2 (6×) / trailing (3.75×) / timeout (96 bars)

> The exit geometry was widened on 2026-08-27 (see [BENCHMARKS.md](BENCHMARKS.md),
> Phase 8). The partial exit was switched off: closing 50% at TP1 capped a winner at
> about 0.7R while a loss still cost a full 1R. Because risk is a fixed dollar amount,
> a wider stop means a **smaller** position, so less goes to fees for the same risk. Over
> the 665-day window the number of hard stops fell from 7 to 3.

In the current record (29 July – 25 September), 471 probes were opened and 102 of them
were confirmed and became full positions; the probe layer cost −$13.48 in total.

### Position sizing

Not a fixed notional but a **fixed dollar risk**: every full position is sized to lose about
`RISK_PER_TRADE_USD` ($10 ≈ 1% of the balance) if its SL is hit. A volatile coin gets a
small position and a calm one a large position. In the retired testnet version the average
loss came out at $10.34, a check of this mechanism in the field.

### Risk layers (kill switches)

| Limit | Threshold | Action |
|---|---|---|
| `DAILY_DD_LIMIT` | −5% (intraday) | New entries are frozen |
| `EQUITY_THROTTLE_DD` | −7% (from peak) | Position size is halved |
| `PEAK_DD_LIMIT` | −15% (from peak) | **Hard stop**: the bot stops itself |
| `DAILY_SL_LIMIT` | 2 SLs / symbol / day | That symbol is frozen for the day |
| `MAX_OPEN` | 2 | At most 2 full positions at a time |

(The first build, which tripped on 14 June, ran with a −20% limit; the current code uses −15%,
see [config.py](config.py).)

---

## Live monitor (`watch.py`)

A monitor you open in a second terminal while the bot runs; it refreshes every few seconds.
It **only reads** `state_paper.json` and never touches the running bot. One screen shows how
far the drawdown is from the throttle and hard-stop thresholds, the open positions, the
regime table and the latest trades. The monitor's labels are in Turkish.

```console
$ python watch.py --demo          # örnek veriyle dene (bot gerekmez)

══════════════════════════════════════════════════════════════════
  BREAKOUTBOT — LIVE WATCH   ◆ DEMO
  2026-07-08 10:28:36 UTC   ·   Gün 2026-07-08   ·   Bar #6,821
══════════════════════════════════════════════════════════════════
  Bakiye  $978.42   Getiri -2.16%   zirve $1,000.00

── DRAWDOWN ──────────────────────────────────────────────────────
  DD -2.16%  ███████··············┊························
  0%      throttle -7%                                hard -15%
  Throttle'a kalan: $48.42   Hard-stop'a: $128.42

── BUGÜN ─────────────────────────────────────────────────────────
  Günlük P&L $-6.68 (-0.68%)   Giriş: açık   SL bugün: 1
  günlük freeze eşiği -5%

── AÇIK POZİSYONLAR ──────────────────────────────────────────────
  SOLUSDT   TRAIL LONG  giriş 148.2  SL 149.4  TP1✓152.1  TP2 158  $620 · 22 bar
  UNIUSDT   PROBE LONG  giriş 9.905  SL 9.71   (yoklama) · 1 bar
  Full: 1/2

── REJİM (BTC 200-MA) ────────────────────────────────────────────
    SOL:BULL     UNI:BULL    AVAX:NEUT    NEAR:NEUT
    ADA:NEUT     INJ:BEAR     POL:BEAR     LDO:NEUT

── SON TRADE'LER ─────────────────────────────────────────────────
  ▲ 07-08 06:20 NEARUSDT  MR    TP1    $+4.05
  ▼ 07-08 08:55 POLUSDT   LONG  SL     $-10.02
  ▲ 07-08 10:30 UNIUSDT   LONG  TP1    $+6.02

── OTURUM ────────────────────────────────────────────────────────
  Pozisyon 5  ·  WR 60% (3W/2L)  ·  Net $+0.64  ·  PF 1.03
══════════════════════════════════════════════════════════════════
  read-only · botu etkilemez · simülasyon (gerçek para değil)
```

> The screen above is **sample data** from `state_paper.sample.json` (it shows the UI without
> the bot and reflects the 8-coin universe and the old exit structure from July).
> For real results → [breakoutbot.dev](https://breakoutbot.dev).

```bash
python watch.py                 # live, refreshes every 5 s (real state)
python watch.py --interval 2    # refresh more often
python watch.py --demo          # with sample data
python watch.py --once          # a single frame (screenshot / CI)
```

---

## Validation

Every experiment runs on **the same pinned data**, so a difference in the metrics is purely a
difference in the code. A parameter change is adopted only if it beats the baseline in the
240-day **and** the 665-day window ([experiments/](experiments/), [BENCHMARKS.md](BENCHMARKS.md)).
Metrics are counted per position ([metrics.py](metrics.py)); partial exits do not count as
separate wins.

The current exit set (Phase 8) in backtests:

| Window | Result | Max DD | Hard stops |
|---|---|---|---|
| 240 days (to August 2026) | $1,000 → $1,435 (PF 2.00) | −6.14% | 0 |
| 665 days (October 2024 – August 2026) | $1,000 → $1,085 (PF 1.18) | −37.6% | 3 (assuming a restart after each) |

The live record (−5.3%, 115 positions) is currently below the 240-day expectation. The gap
between backtest and live results, and the risk of selection bias, are tracked in
[DEFTER.md](experiments/DEFTER.md); this table is not a return expectation.

---

## Run locally

```bash
pip install -r requirements.txt   # ccxt, pandas, numpy, requests

python paper_bb.py                    # simulation (no keys needed)
python paper_bb.py --resume           # continue from the saved state
python paper_bb.py --status           # summary of the current state

python watch.py                       # live monitor (read-only)
python watch.py --demo                # with sample data (no bot needed)

python bench.py fazN                  # phase backtest (see BENCHMARKS.md)
./experiments/run_arm.sh <arm> <days> ENV=VAL…   # experiment arm (see experiments/DEFTER.md)
```

The `--testnet` flag and [testnet_orders.py](testnet_orders.py) are left over from the
retired version; the running system does not use them.

The live system runs on a VM as the `breakoutbot-test` systemd service. The old `breakoutbot`
(MAIN) service, which sent testnet orders, was retired on 22 August 2026. Changes are deployed
with [deploy_test.sh](deploy_test.sh) (the target server is read from the `BREAKOUTBOT_SERVER`
environment variable). The AI veto record and the observation report are separate, read-only
services (see the [ai_shadow/](ai_shadow/) and [monitoring/](monitoring/) READMEs).

---

## History

| Period | What happened | Outcome |
|---|---|---|
| **31 May – 14 Jun 2026** · first version, 23 coins, testnet orders | 45 full positions | WR 33%, profit factor 0.52 → **negative edge**. At a −20.3% peak drawdown **the hard stop fired and the bot stopped itself.** |
| **Incident** · 14–15 Jun | After the hard stop, the systemd service went into a restart loop of about 245 restarts (the exit code did not match `RestartPreventExitStatus`) | No further losses (the bot stopped again each time); the service setting was fixed. Postmortem: [REPORT.md §5](REPORT.md) |
| **15 Jun – 22 Aug 2026** · regime-aware version ("v1" on the site), testnet orders | The state was reset to $1,000 and the universe cut to 8 coins. **0 full positions** in the first 3.2 weeks (an overcorrection). Then a measurement bug was found: TP1 was counted as a separate win (reported WR 57.7%, real 43.6%) | Retired on 22 August at $896.38 (−10.4%) |
| **29 Jul 2026 – ongoing** · current version ("v2" on the site), simulation | Started as a live A/B test on the test service (wider trailing stop, no partial exit) and won on 10 August. The universe went to 5 coins on 22 August, the Phase 8 exit set was adopted on 27 August, and the universe went to 4 coins on 22 September | Live record: [breakoutbot.dev](https://breakoutbot.dev) |

A position's lifecycle from the retired testnet version's log (old exit structure; half of
the position was closed at TP1):

```
TEST OPEN   JUPUSDT LONG @ 0.1877  | bal=$989.25
CONFIRMED   JUPUSDT LONG pnl=$+0.049
FULL OPEN   JUPUSDT LONG @ 0.1883  | notional=$900
CLOSE FULL  JUPUSDT [TP1]   entry=0.1883 exit=0.1901   pnl=$+3.95
CLOSE FULL  JUPUSDT [TRAIL] entry=0.1883 exit=0.18965  pnl=$+2.90
```

In that period, 308 probes cost a net −$7.65 in total and filtered out 228 weak signals before
they became full positions. The PF of 3.29 reported by that period's Phase 4c backtest was
inflated by counting TP1 as a separate win; the corrected pooled PF is 1.95
([BENCHMARKS.md](BENCHMARKS.md), Phase 5).

---

## Repo map

| File | What it is |
|---|---|
| [paper_bb.py](paper_bb.py) | Main loop: bar processing, risk gates, state persistence |
| [market_data.py](market_data.py) | Binance public 5m/4h candle fetching (closed bars only) |
| [strategy.py](strategy.py) | Signal → decision; regime gates, confirmation logic |
| [math_engine.py](math_engine.py) | Wave 11 composite signal score (0–100) |
| [indicators.py](indicators.py) | RSI, Bollinger, ATR, ADX, Hurst and more |
| [regime.py](regime.py) | BTC 200-MA regime classification (BULL / NEUTRAL / BEAR) |
| [metrics.py](metrics.py) | Per-position metrics (expectancy, payoff, break-even WR): the single source of truth |
| [mean_reversion.py](mean_reversion.py) | Mean-reversion arm for ranging markets (currently off) |
| [short_sleeve.py](short_sleeve.py) | Short experiment: no edge found in backtests, off but documented |
| [config.py](config.py) | Every parameter in one file, with comments explaining each choice |
| [testnet_orders.py](testnet_orders.py) | **Retired** testnet order executor, not used by the running system |
| [backtest.py](backtest.py) / [bench.py](bench.py) | Backtest replay engine + fixed-data phase comparison harness |
| [backtest_data.py](backtest_data.py) | Historical OHLCV fetching + window cache (for reproducible experiments) |
| [backtest_report.py](backtest_report.py) | Backtest output: progress, reports, run dumps |
| [experiments/](experiments/) | Experiment arms, the `DEFTER.md` hypothesis notebook, `ledger.py` |
| [ai_shadow/](ai_shadow/) | AI veto record (Claude Opus 5.5, Tur 15): a separate service that does not touch the bot |
| [monitoring/](monitoring/) | 15-minute AI observation report service (read-only) |
| [watch.py](watch.py) | Live monitor; reads the state (read-only) |
| [dashboard.py](dashboard.py) | Go/no-go control board (one-off checklist) |
| [REPORT.md](REPORT.md) | Testnet report + postmortem (31 May – 7 Jul 2026) |
| [BENCHMARKS.md](BENCHMARKS.md) | Phase-by-phase backtest comparison and decisions |
| [ROADMAP.md](ROADMAP.md) | Design document for the multi-regime evolution |

---

## What's next

- [x] Fix the restart loop after a hard stop (June 2026, [REPORT.md §5](REPORT.md))
- [x] Signal funnel telemetry and per-position metrics (July 2026, [BENCHMARKS.md](BENCHMARKS.md), Phase 5)
- [x] Exit structure experiments → the Phase 8 exit set (August 2026)
- [ ] AI veto experiment: Phase 0 (25 September – 2 October 2026), then the pre-registered checks at 100 and 300 closed positions
- [ ] Walk-forward validation (to measure the selection bias in the chosen exit arm)
- [ ] Measure whether the probe layer is needed

---

## Disclaimer

No figure here was produced with real money. The running bot is **a pure simulation**: real
price data, a local virtual wallet, no orders sent to an exchange. Retired versions sent real
orders to the Binance Futures **Testnet** (still with a fake balance). The distinction matters,
because the slippage and execution costs of a system that sends orders do not show up in a
simulation. `BENCHMARKS.md` states which figure comes from which source.

This project is a research and engineering exercise; it is **not investment advice** and is not
designed for use with real money.
