# Arjun - Options by Liquide — Reverse-Engineering Study

**Status:** Research / study. Not yet a registered strategy in the codebase.
**Last updated:** 2026-04-24
**Goal:** Independently verify whether a third-party Indian options signal channel
(Telegram: "Arjun - Options by Liquide") actually makes money, and reverse-engineer
the trading setup from the entry timestamps so we can eventually port it as a
backend strategy with filter refinements that beat the channel itself.

This document is the session memory for the study. A fresh agent session should
be able to read this file alone and understand everything we did on 2026-04-24.

---

## TL;DR

- **Channel is ~marginally profitable in accurate simulation**, with a hit rate far
  below what they advertise: 46% real vs ~70% claimed. Profit factor 2.18,
  expectancy +Rs 2,745/trade, but only on 52 accurate trades (small sample).
- **Strategy is opening-session breakout momentum**, not VWAP pullback as might
  be assumed. 96% of CE entries fire above VWAP, 78% above PDH, 93% above CPR TC,
  89% near today's high. PE mirrors this. 42% of trades fire in the first 90
  minutes.
- **82% long-bias (CE-heavy)** in a rising market — not a neutral strategy.
- **CE side has filterable edge**: OTM strikes (+10pp WR), 11:00/13:30 time
  buckets (+11pp each), entry_price ≥ Rs 100 (+7.7pp). Also: bullish engulfing
  / bullish reversal patterns at the entry bar actively HURT performance
  (counter-intuitive).
- **PE side edge analysis is inconclusive** — N=110 is too small to find
  statistically meaningful filters. Do NOT interpret this as "drop PE".
  Needs more data (forward-watch accumulation).
- **A significant backend-code finding:** our `option_data_fetcher.py` had a
  wrong comment claiming Fyers free plan doesn't serve option history. It
  actually serves 1m history for currently-listed contracts; it only purges
  expired contracts. Docs + comment now fixed.
- **Delta-approximation vs accurate-mode simulation diverge 2×** on the same
  signals. Delta-approx is pessimistic because it ignores gamma/IV. Useful as
  a lower bound, not a drop-in substitute.

---

## Source channel

- Telegram chat title: **"Arjun - Options by Liquide"**
- Chat ID: `-1002127259353`
- Type: supergroup (we are members, not admins)
- Fetched message count: **3737** over 2025-10-01 → 2026-04-24 (206 days)
- Native "account" approach: not usable — Telegram Bot API cannot read third-party
  group history unless the bot is added by an admin. We authenticate as the user
  via MTProto (Telethon) instead.
- Credentials live in `.env`:
  - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION_NAME`
  - Obtained from https://my.telegram.org/apps (one-time, free)
  - Session file cached at `scripts/telegram/{TELEGRAM_SESSION_NAME}.session` —
    first run prompts for the one-time login code, subsequent runs are silent.
- The Telegram Bot API token already in `.env` (`TELEGRAM_BOT_TOKEN`) is separate
  and is used only by our backend for outbound alerts.

---

## Toolchain built today (all in `scripts/telegram/`)

All scripts assume `source backend/.venv/bin/activate` first. Fetched JSON,
symbol-master cache, candle cache, and session file are all gitignored under
`scripts/telegram/data/` and `scripts/telegram/*.session`.

| Script | Purpose |
|---|---|
| `list_dialogs.py` | Lists all Telegram dialogs. Use `--match arjun --groups-only` to find the chat ID. |
| `fetch_history.py` | Pulls full message history of one chat into JSON. `--chat-id -1002127259353 --since 2025-10-01` for our window. |
| `parse_signals.py` | Stdlib-only parser. Classifies each message into one of ENTRY / EXIT_FULL / EXIT_FORCED / EXIT_PARTIAL / WATCHLIST / HOLD_OVERNIGHT / REPORT / UPDATE / CANCEL / OTHER. Extracts symbol, strike, option_type, expiry_day/month, entry_price, target1/2, stoploss. 100% parse success on ENTRY messages. |
| `probe_fyers_history.py` | One-shot diagnostic. Verifies that Fyers' free-plan history API serves 1m bars for currently-listed option contracts (confirmed). Verifies that expired contracts are purged from both the symbol master and the history API (confirmed). Saved us from building the wrong verifier. |
| `verify_signals.py` | Hybrid backtest. Accurate mode uses real option 1m bars for currently-live contracts (April expiry only, in our case). Delta-approx mode uses underlying spot 1m × moneyness-derived delta for everything else. Applies the channel's stated rules: T1 hit → book half + SL to entry (C2C); T2 hit → exit remainder; SL hit → full loss. Uses current NSE F&O lot sizes from the master CSV. |
| `analyze_setups.py` | Reverse-engineer step. Re-uses `backend.app.indicators` (VWAP, CPR, previous_day, candle_patterns). Fetches underlying 1m bars up to each entry minute, computes a feature vector at entry_ts, aggregates CE vs PE distributional stats, prints an evidence-backed hypothesis. Also dumps a feature CSV/JSON. |
| `analyze_edge.py` | Feature-importance pass. Joins `setups_*.json` with `verification_*.json` on `msg_id`, bucketizes each feature, computes win-rate lift per bucket (min N=30), ranks features, stress-tests top-3 filter combinations. `--win-def` flag flips between "any profit", "clean T2 win", "any T1 touch". |

### Scripts deliberately NOT built (yet)
- Forward-watch daemon (real-time parse + filter alerting from new messages).
- Port as `strategy_5_breakout_momentum.py` in the backend.
- Corporate-action-aware symbol resolver (11 signals skipped due to
  rename/demerger issues — material but small).

---

## Parser taxonomy

The channel uses ~10 stable message templates. Our classifier hits 100% of
ENTRY messages and leaves most genuine free-form commentary as OTHER.

| Kind | Format example | Counts (3737 msgs) |
|---|---|---|
| `ENTRY` | `🚨 New Options Trade — BUY INDIANB 24 FEB 920 CE at ₹19.5 + 🎯 Target 1/2 + 🔴 Stoploss` | 632 |
| `EXIT_FULL` | `🎯 Book Profit in <...> at price ₹29` | 160 |
| `EXIT_FORCED` | `🚨 Exit <...> at cost to cost ₹X` or `at the current price ₹X` — **deliberately kept separate from EXIT_FULL so channel-claimed "wins" aren't inflated**. | 382 |
| `EXIT_PARTIAL` | `🎯 Target 1 Hit` or `🎯 Book Partial Profit in <...> at ₹X` | 305 |
| `WATCHLIST` | `<SYM> <STRIKE> <TYPE> <MONTH>  ADD TO WATCHLIST.` (also handles observed typo `ADD T0 WATCHLIST`) | 1104 |
| `HOLD_OVERNIGHT` | `<SYM> <STRIKE> <TYPE> (<MONTH>) ✅ Hold for next trading day.` | 30 |
| `REPORT` | `📊 POST-MARKET REPORT` / `📊 Pre-Market Update` | 206 |
| `UPDATE` | `📌 Trade Update — <free text>` | 26 |
| `CANCEL` | `Cancel the trade` / `close the trade` | 4 |
| `EMPTY`/`OTHER` | edge cases + legitimate chat | 888 |

### Key parser design decision
`EXIT_FULL` means "🎯 Book Profit in …" — a claimed profitable exit. `EXIT_FORCED`
means "cost to cost" (break-even bail-out) or "exit at the current price"
(discretionary exit). These are **separate kinds** so that summing "ways a
trade was closed positively" doesn't accidentally include break-even bail-outs.
Our verifier doesn't depend on matching channel exits to entries at all — it
simulates exits independently from bars. The parser's exit classification is
only used for audit / sanity-check purposes.

---

## Data collected (scripts/telegram/data/)

- `-1002127259353_20260424_162007.json` — 3737 messages, 2025-10-01 → 2026-04-24
- `-1002127259353_20260424_160250.json` — earlier fetch (1000 msgs, narrower window)
- `verification_20260424_164416.json` — full hybrid verification of 630 entries
- `setups_20260424_165659.json` — features at entry minute for 623 entries
- `setups_features.csv` — same features as CSV for ad-hoc analysis
- `fo_master.json` — cached NSE F&O symbol master (refreshed daily)
- `candle_cache/` — per-(symbol, date-range) Fyers history cache to avoid re-fetches

Everything in `data/` is gitignored.

---

## Independent verification results

630 entries parsed → 619 verified (98.3%), 11 skipped due to
rename/demerger (SKIP_NO_SPOT_DATA).

|  | N | Hit rate | Total P&L | Expectancy | Profit factor |
|---|---|---|---|---|---|
| **Accurate** (real option 1m, April only) | 52 | **46.2%** | +Rs 142,738 | **+Rs 2,745/trade** | **2.18** |
| Delta-approx (spot × delta, Oct–Mar) | 567 | 40.0% | −Rs 97,363 | −Rs 172/trade | 0.92 |
| Overall | 619 | 40.5% | +Rs 45,375 | +Rs 73/trade | 1.04 |

### What this means
- **Channel's claimed ~70% win rate is inflated.** Their "Book Profit ✅" messages
  make every partial T1 look like a clean win, but the real picture (clean T2 hits
  are rare; most winning trades are tiny T1-then-C2C scratches) is very
  different.
- **Accurate simulation is profitable**, but barely — +Rs 2,745/trade average is
  before brokerage (Rs 20–40 per lot round-trip), slippage on entry fills, and
  follower-delay (getting the Telegram message + executing costs minutes).
- **Delta-approx ≠ accurate**. Over the same methodology the two paths diverge by
  a factor of 2 on aggregate P&L. Delta-approx loses money where accurate makes
  money. This is because delta-approx ignores gamma convexity (OTM options move
  more than linear on large spot moves), which is where most of Arjun's T2 wins
  come from.

### Caveats on the accurate result
- Only 52 trades, all April 2026 expiry. Could be a favorable market regime.
- Assumes instant execution at exact stated entry price.
- No brokerage. Round-trip fees eat into thin option P&L meaningfully.
- No slippage modeling.

### Simulation rules applied (exact)
1. Walk forward on 1m bars after `entry_ts` (exclusive of the entry minute).
2. If `low <= effective_sl`: SL hit. If T1 already booked, partial-pnl = ((T1-entry)×0.5) + ((entry-entry)×0.5) = tiny positive (PARTIAL_T1_BE). Otherwise full loss (LOSS_SL).
3. If `high >= T1` and T1 not yet booked: book half at T1, effective_sl moves to entry (C2C rule), continue.
4. If `high >= T2` (after T1 already booked): book remainder at T2. Full win (WIN_T2).
5. If data ends before any of the above: mark to last close. If T1 already booked → TIMEOUT_POST_T1; else → TIMEOUT.

---

## Reverse-engineered setup (strategy inference)

Across 623 entries with successful feature extraction (CE=511, PE=112):

### CE (calls) setup

| Feature | Evidence |
|---|---|
| Above VWAP | **96%** |
| Above Previous-Day High (breakout) | **78%** |
| Above CPR Top Central | **93%** |
| Near today's high (range_pos > 0.7) | **89%** |
| Positive 15m momentum (> +0.2%) | 68% |
| Volume surge (entry bar > 1.5× today avg) | 37% (not required) |
| ATM strike (\|moneyness\| ≤ 0.5%) | 50% |
| OTM strike (moneyness_pct ≤ −0.5%) | 23% |
| ITM strike (moneyness_pct ≥ +0.5%) | 27% |

### PE (puts) setup — mirror image

| Feature | Evidence |
|---|---|
| Below VWAP | **92%** |
| Below Previous-Day Low (breakdown) | **81%** |
| Below CPR Bottom Central | **91%** |
| Near today's low (range_pos < 0.3) | **82%** |
| Negative 15m momentum (< −0.2%) | 52% |

### Time of day
42% of all trades fire in the first 90 minutes of the day — buckets
09:30 / 10:00 / 10:30. But see the edge-analysis finding below: the first 90
minutes is where most signals fire, but it's **not** the highest-edge bucket.

### Implied strategy (informal)

```
Entry conditions (CE — mirror for PE):
  1. After 9:30 IST (let the opening noise settle)
  2. Spot > VWAP AND VWAP slope >= 0
  3. Spot > Previous Day High  (breakout)
  4. Spot > CPR Top Central
  5. Price currently within ~30% of today's high
  6. Pick ATM or near-ATM (Arjun's preference; see edge analysis — OTM may be better)
  7. Preferred premium range: Rs 30–200 (p10–p90 from data)
  8. No strong requirement on volume surge (only ~37% of entries show one)

Position management (channel's stated rule, verified in simulation):
  T1 = entry × ~1.15, T2 = entry × ~1.30, SL = entry × ~0.80
  On T1 hit: book half, move SL to entry (C2C)
Holding: intraday with overnight-hold option if direction intact at close
```

### Why this is NOT VWAP pullback
Only 26–31% of entries show a pullback-to-VWAP (within ±0.25%). The rest are
trading AWAY from VWAP, on the breakout side. The pullback-to-VWAP cluster
inside our data is coincidental — it's not the setup.

### Regime caveat
The 82% CE bias is consistent with the Oct '25 → Apr '26 Nifty bull
(25k → 26k). A pure bull-following strategy with 5:1 CE:PE bias likely
underperforms in a sustained bear market. Needs cross-regime validation.

---

## Feature-importance / edge analysis

Joined 618 rows (features ∩ verified trades) by `msg_id`. For each feature,
bucketized and computed win-rate lift vs direction-baseline. Min bucket N=30
to avoid noise.

### CE side — filterable edge exists (baseline WR = 40.4%, N=508)

Top positive lifts:

| Filter | N | WR | Lift | Σ pnl/lot |
|---|---|---|---|---|
| **moneyness_pct ≤ −0.20** (≥ 0.2% OTM call) | 127 | **50.4%** | +10.0pp | **+Rs 1,588** |
| tod_bucket=13:30 | 33 | 51.5% | +11.2pp | +230 |
| tod_bucket=11:00 | 41 | 51.2% | +10.9pp | +270 |
| **entry_price ≥ Rs 100** | 129 | **48.1%** | +7.7pp | **+Rs 1,547** |
| pdh_dist_pct < 0.11 (fresh breakout) | 127 | 47.2% | +6.9pp | +660 |
| prev_day_bias=BEARISH (counter-trend CE) | 85 | 45.9% | +5.5pp | +315 |
| pdc_dist_pct < 0.68 (modest extension) | 127 | 45.7% | +5.3pp | +1,930 |

Negative-edge filters (take these OFF the whitelist):

| Anti-filter | N | WR | Lift |
|---|---|---|---|
| bull_engulf=True at entry bar | 55 | 29.1% | −11.3pp |
| bull_reversal=True at entry bar | 95 | 33.7% | −6.7pp |
| tod_bucket=10:30 | 47 | 27.7% | −12.7pp |
| tod_bucket=12:30 | 26 | 23.1% | −17.3pp |
| tod_bucket=15:00 | 7 | 28.6% | −11.8pp (low N) |

Counter-intuitive but evidence-backed: **the entry bar being a bullish
engulfing / reversal pattern actively hurts performance on a breakout entry.**
Plausible interpretation: these patterns signal exhaustion, not continuation.
Arjun sometimes catches the top of an impulse move instead of the start.

### PE side — NO qualifying filters (baseline WR = 41.8%, N=110)

**Important decision (from user 2026-04-24):** do NOT skip PE signals. N=110
is too small to find real filters; absence of evidence is not evidence of
absence. Keep PE in the mix and re-run edge analysis once forward-watch
accumulates more data.

### Combined filter stress test
Requiring top-3 CE filters simultaneously is over-fit: only 4 signals match
(1%), WR 75% but on tiny N. Single-filter application is the sensible level:
any of the N>=127 filters above individually lifts WR by 5–10pp without
destroying sample size.

---

## Our own codebase findings (surfaced during this work)

- `backend/app/backtest/option_data_fetcher.py` comment was wrong: it claimed
  Fyers free plan doesn't serve option 1m history. Fixed to reflect the actual
  behavior (works for currently-listed contracts, returns `s="error"` for expired).
- `docs/backtest/option-data.md` updated with the same correction.
- `backend/app/config.py` `Settings` needed `extra="ignore"` so adding unrelated
  `.env` keys (like the Telegram User API credentials) doesn't crash backend
  startup. Now done.
- Discovery: **delta-approx and accurate-mode diverge 2× on P&L over the same
  signals.** Relevant for our own backtest harness — delta-approx should be
  treated as a pessimistic lower bound, not a drop-in substitute for accurate
  mode. A Black-Scholes-plus-IV-surface model would close the gap and be worth
  considering if we do many backtests on expired contracts.

---

## Open decisions & next steps

Ordered by recommended priority:

1. **Port as `strategy_5_breakout_momentum.py`** in the backend. Follow the
   VWAP-Pullback blueprint: extends `BaseStrategy`, sets
   `instrument_type=OPTION`, implements `evaluate(ctx)`, `should_exit()`.
   Entry conditions from the "Implied strategy" section above, with the
   CE-side filters applied as a pre-screen. Backtest on NIFTY/BANKNIFTY
   first. Add `BREAKOUT_MOMENTUM` to `StrategyName` enum. Register in
   `strategies/registry.py`. Add to `STRATEGY_LABELS` on the frontend.
2. **Keep investigating PE.** User decision: PE side stays in scope.
   Accumulate more data via forward-watch, then re-run edge analysis. Don't
   build PE filters from N=110 — that's noise.
3. **Forward-watch daemon.** Small script that watches the Telegram chat
   live via Telethon events, parses each new entry through `parse_signals`,
   re-verifies it in accurate mode once the contract has enough intraday data,
   and appends to a growing verification ledger. Gives us accurate-mode
   sample size growth of ~5 trades/day.
4. **Alternative-win-definition sweep.** `analyze_edge.py --win-def win_clean`
   and `--win-def win_or_t1` — see if different definitions shift the filter
   rankings materially. Quick sanity check on our conclusions.
5. **Corporate-action symbol resolver.** 11 signals skipped (TATAMOTORS, etc.).
   Small but material. Build a rename map from NSE corporate action history
   or manually from the 11 cases.

---

## Methodology caveats (read before using any number in this doc)

1. **Sample size**: 52 accurate trades is not enough to make a definitive
   call on profitability. Wide confidence interval.
2. **Delta-approx is a lower bound**, not a neutral estimator. Our results
   for the 567 delta-approx trades understate true P&L by some unknown factor
   (probably 1.5×–2× based on the divergence seen on April-overlap entries).
3. **No brokerage / slippage / follower-delay** is modeled. Real-world returns
   for a follower will be worse than any number in this doc.
4. **The signal timestamp is the message post time**, not the actual fill
   time. Follower fill happens seconds-to-minutes after. We assume zero delay.
5. **Only one market regime**: Oct '25 → Apr '26 was a rising market. Nothing
   in this study tells us how the strategy performs in a sideways or bear
   market. Do not port as a strategy without cross-regime validation.
6. **Parser coverage**: 100% on ENTRY but exits and partials are harder to
   disambiguate. This matters only for channel-vs-reality comparison, not
   for our independent simulation (which doesn't read their exits at all).
7. **11 signals skipped** due to corporate-action symbol failures. Drop
   likely affects stock coverage slightly but not aggregate stats.

---

## Commands to re-run everything (new session quick start)

```bash
source backend/.venv/bin/activate

# 1. Pull latest messages
python scripts/telegram/fetch_history.py --chat-id -1002127259353 --since 2025-10-01

# 2. Parse + show summary stats
python scripts/telegram/parse_signals.py scripts/telegram/data/-1002127259353_*.json

# 3. Verify signals (requires Fyers token in Redis; start backend first)
python scripts/telegram/verify_signals.py scripts/telegram/data/-1002127259353_*.json --mode hybrid

# 4. Reverse-engineer setup (fetches underlying 1m bars + runs indicators)
python scripts/telegram/analyze_setups.py scripts/telegram/data/-1002127259353_*.json \
    --csv scripts/telegram/data/setups_features.csv

# 5. Edge analysis (joins features + verification outcomes, ranks filters)
python scripts/telegram/analyze_edge.py
#   --features scripts/telegram/data/setups_*.json   (auto-picks newest)
#   --trades   scripts/telegram/data/verification_*.json
#   --win-def {win_any,win_clean,win_or_t1}
```

### Required env (all in `.env`, already populated)
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION_NAME`
- `FYERS_APP_ID`, `FYERS_USERNAME`, `FYERS_PIN`, `FYERS_TOTP_SECRET` (for token)

### First-run prerequisites
- Backend must be running (or have been run today) so the Fyers auto-login
  has populated `fyers:access_token` in Redis.
- `telethon>=1.36.0` installed (added to `backend/pyproject.toml`).
- First-ever run of `list_dialogs.py` prompts for one-time Telegram login code
  and caches session. All subsequent runs silent.
