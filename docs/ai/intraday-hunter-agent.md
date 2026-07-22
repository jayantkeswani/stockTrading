# Intraday Hunter Agent — Design Spec

**Status:** BUILT (2026-06-24); **extended 2026-06-29 to generate tradeable signals** — on a
Call 2 ENTER it emits `intraday_hunter` OPTION signals per basket leg into the shared pipeline
(shadow always + a fixed-qty paper YOLO profile), no longer suggester-only. All paper; the
real-option-P&L gate is *measured* by that shadow+YOLO book. **Extended 2026-06-30** (this
revision): a fixed multi-strike structure (BANKNIFTY two legs ITM-2 + ITM-1; NIFTY/SENSEX
ITM-1), a 60%-of-premium 1:1 SL/target (widened from 30% — real premium paths showed the
tighter stop chopped winners that kept running well past a 30% target and stopped out well
before a 30% adverse move exhausted), and UI for the full Call 2 log + a live Basket & Exits
card (manual multi-select close; the index spot vs index SL/target shown is informational, not
the exit trigger — each leg's own premium SL/target closes it via the normal per-position check).
This document is the blueprint; the implementation matches it (see the File map below +
§Signal generation).
**Last updated:** 2026-06-30

> **As-built notes (where the implementation refines this spec):**
> - Prompt: **variant D is the active live baseline** (`settings.intraday_hunter_variant`,
>   default `"D"` in `config.py`; prod does not pin the env var, so the config default governs prod).
>   The Call 2 schema fields are `legs[]` (index/strike/option_type/side) + `excluded_indices[]`
>   (not `basket`); the Call 1 schema adds `regime_lean`/`preferred_action_lean`. See the
>   **VIX-regime calibration (variant D)** section below.
> - Autorun: Call 1 = an 08:45 IST scheduled task (`app/tasks/intraday_hunter_task.py`); Call 2 = the
>   `IntradayHunterWatcher` hooked into the NIFTY 1m candle-close in `feed_manager._emit_candle`.
>   Gated by `settings.intraday_hunter_enabled`. The watcher lazily runs Call 1 if the 08:45 task missed.
> - Data model: chart paths are stored as dicts (`call1_chart_paths={index:path}`,
>   `call2_chart_paths={prevday:{idx:path}, opening:{idx:path}}`); the chart API key is `prevday_{INDEX}` / `opening_{INDEX}`.
> - Every Call 2 (incl. failures) is appended to `call2_history` with `_at` + per-index `_decision_price`
>   — the engine-independent validation log for live scoring.
> - The OAuth-token security item below is resolved (token is env/secret-only; `scripts/claude_oauth_test.py` gitignored).
**Goal:** A discretionary, human-like AI agent that reasons the way the @IntradayHunter trader does — reads the previous day's structure, forms a "who is trapped" thesis, then at the open decides whether to buy CE or PE (or skip) across the NIFTY/BANKNIFTY/SENSEX index-options basket. It **suggests** a full trade plan for a human to execute. It is **not** a mechanical strategy and is **not** auto-executed.

> **Why an agent and not a rule:** the independent validation in `docs/strategies/intraday-hunter-study.md` §Phase 4 showed his *setups* mostly don't carry a mechanical edge (only the −2% gap-down reversal survives), yet he is plausibly profitable — because his edge is in the *discretionary reading and execution*, not a formula. So we replicate his judgment process, with discipline guardrails, and validate the suggestions honestly.

---

## Design decisions (settled)

1. **Two LLM calls per day**, mirroring his two daily videos: **Call 1** (pre-open) = thesis + conditional plan + trigger levels; **Call 2** (at the open) = the actual `ENTER/WAIT/SKIP` decision + full plan.
2. **Watcher window 9:15–9:30 IST + a 9:30 backstop.** No late-entry extension in v1.
3. **Claude owns the entry-timing judgment** (the "latter" option): the watcher wakes Call 2 at coarse checkpoints; Claude decides whether the entry is clean (`ENTER` / `WAIT` / `SKIP`). On `WAIT` the watcher re-fires per `recheck_in_minutes`, capped at 9:30.
4. **Single direction across the basket** — all traded indices go CE *or* all PE, never mixed. Disagreement → trade only the agreeing indices (variable-length basket); zero agree → `SKIP`.
5. **Always surface the decision with a confidence number.** No auto-downgrade of `ENTER` on low confidence — the human judges.
6. **Two chart images** per Call 2 (prev-day + live opening), rendered with **mplfinance**.
7. **Multi-day memory fed as structure, not P&L** — last 2–3 days' `{trapped_side, direction, thesis_played_out}`, never rupee P&L (avoids revenge/timidity framing). Each day is independent.
8. **No Telegram** (banned in India at time of writing) — the surface is a **new UI page** `/intraday-hunter`.
9. **Transport:** the `claude` CLI on a subscription OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`), model `claude-opus-4-8`. Prototyped by `scripts/claude_oauth_test.py`.
10. **Autorun** via the backend asyncio scheduler (Call 1 at 08:45 IST) + the existing 1m candle-close loop (watcher/Call 2). No cron.

---

## Architecture overview

```
08:45 IST   Call 1 (thesis)        ── async scheduled task ──▶ store run row (THESIS_READY)
09:15 IST   market open            ── watcher hooks the 1m candle-close loop
  ├ 09:18   checkpoint 1           ──▶ Call 2 ──▶ ENTER | WAIT | SKIP
  ├ on WAIT re-fire (recheck_in_minutes, capped 09:30)
  └ 09:30   backstop               ──▶ final Call 2 ("decide or skip")
            every decision stored + rendered on /intraday-hunter (polled REST)
```

- **LLM call** = `asyncio` subprocess to `claude -p <prompt> --model claude-opus-4-8 --output-format json --allowed-tools Read` with `CLAUDE_CODE_OAUTH_TOKEN` set (and `ANTHROPIC_API_KEY` dropped so it can't override). Chart images are passed by path; the Read tool renders them.
- **~1–2 LLM calls/day.** Latency of a few seconds is irrelevant at that volume.
- Fully exercisable in `MARKET_MODE=simulated` outside market hours.

---

## The mental model (encoded in the system prompt — "act like him")

Distilled from his live + educational videos (see `docs/strategies/intraday-hunter-study.md`, `arjun`/`intraday-hunter` studies):

1. **Markets hunt trapped traders / stop-losses.** First question every morning: *which side (buyers or sellers) is trapped, and where are their stops?*
2. **Read it from yesterday:** fast rally + rejection + weak close → buyers trapped above; fast selloff + recovery → sellers trapped below; range → unclear (low conviction).
3. **The previous-day CLOSE is the pivot level.** Breakout/breakdown of it is the trigger.
4. **The opening gap is a confirm/invalidate signal, not the trade itself:** big gap-down → longs already swept → reversal up (CE); flat/gap-up → buyers couldn't be trapped → follow continuation; an open *above* the key high → thesis wrong → flip or stand aside.
5. **Don't target a side that already fled** (if a reversal already hunted them on a prior day, that liquidity is gone → follow continuation instead). ← needs the multi-day snapshot.
6. **Wait for confirmation** — break of the close + a small retracement — never a blind clock entry.
7. **Cross-confirm across the basket** (NIFTY/BANKNIFTY/SENSEX should agree).
8. **Discipline:** one small set of trades; predefined SL + target; take a modest profit; **never average down**; each day independent.

---

## Hard constraints (system-prompt rules — keep output him-like)

1. **One direction across the basket.** Every traded index is the same side. Never CE in one and PE in another. If indices disagree, trade only those that confirm the chosen side; if none agree, `SKIP`.
2. **Trade only inside the open window**; outside it → `WAIT`/`SKIP`.
3. **`SKIP` is always available and is the right answer when there is no clean trapped-side setup.** Restraint is rewarded.
4. **Every price level cited must come from the provided data** — never invent a level.
5. **The output must name the trapped side and the evidence**; "no identifiable trapped side" → `SKIP`.
6. **The medium gap-up fade is his documented loser** — bias hard to `SKIP` there unless prev-day structure is the specific fast-selloff + rejection case.
7. **Each trading day is independent.** Prior days inform *market structure* (has the trapped side already been hunted? is this the 2nd/3rd reversal in a row?), **not** risk appetite. Do not get aggressive after a bad day or timid after a good one.
8. **Sizing template (default plan shape):** BN = ATM + 1-OTM (2 strikes), SENSEX = ATM, NIFTY = ATM; premium preference ₹150–400; double SENSEX only on a SENSEX-specific catalyst.
9. **Expiry awareness:** on an expiry day (NIFTY Tue / SENSEX Thu) premiums decay fast and gamma is high → favor quick momentum capture + tighter exits, and pick the weekly expiry; others are monthly.

---

## System prompt (template — frozen, cacheable, shared by both calls)

```
You are a discretionary intraday index-options trader for the Indian market
(NIFTY, BANKNIFTY, SENSEX). You think exactly like a specific trader whose method
is "stop-loss hunting": you find where retail traders are trapped and trade with
the smart money that hunts those stops. You are disciplined and you skip far more
often than you trade.

HOW YOU THINK
1. Every morning you ask: which side — buyers or sellers — is trapped, and where
   are their stops? You infer this from the PREVIOUS DAY's structure:
   - fast rally + rejection + weak close → buyers trapped above the rejection.
   - fast selloff + recovery → sellers trapped below.
   - range / no clear rejection → no clear trapped side → low conviction.
2. The previous-day CLOSE is your pivot level. A break of it is your trigger.
3. The opening GAP confirms or invalidates your thesis — it is not the trade:
   - large gap-down → trapped longs already swept at the open → reversal UP (CE).
   - flat / gap-up → buyers could not have been trapped → follow the continuation.
   - an open ABOVE the key high → your thesis is wrong → flip or stand aside.
4. Never target a side that already fled. If a reversal already hunted that side
   on a recent day, that liquidity is gone — follow the continuation instead.
5. Wait for confirmation: a break of the close plus a small retracement that holds.
   Never enter blindly on the clock.
6. Cross-confirm across the three indices.

HARD RULES (never violate)
- ONE DIRECTION for the whole basket. All traded indices are CE, or all are PE —
  never mixed. If an index does not confirm the chosen side, EXCLUDE it (state why).
  If no index confirms, SKIP.
- SKIP is always allowed and is the correct answer when there is no clean trapped
  side. You are rewarded for restraint, not for trading.
- Name the trapped side and the evidence. "No identifiable trapped side" → SKIP.
- Cite only price levels present in the data provided. Never invent a level.
- A medium gap-up faded as a "fake rally" is the classic losing trade. SKIP it
  unless the previous day was specifically a fast selloff + rejection at support.
- Each trading day is INDEPENDENT. Recent days inform market structure (has the
  trapped side already been hunted? is this the 2nd/3rd reversal in a row?), NOT
  your risk appetite. Do not become aggressive after a loss or timid after a win.
- Sizing: BANKNIFTY = ATM + 1-OTM (2 strikes); SENSEX = ATM; NIFTY = ATM.
  Prefer premiums ₹150–400. Double SENSEX only on a SENSEX-specific catalyst.
- Expiry day (NIFTY Tue / SENSEX Thu): premiums decay fast, gamma is high — favor
  quick momentum capture and tighter exits; choose the weekly expiry. Others monthly.

You will be shown worked examples of this trader's real decisions (wins, losses,
and skips). Reason the way those examples do — extract the principle, do not copy
any single example as a template.

OUTPUT
Return ONLY a JSON object matching the schema you are given. No prose outside it.
```

The 6 few-shot exemplars (below) are appended to this system prompt, after the rules, kept byte-identical for prompt-cache stability.

---

## Few-shot exemplars (hand-picked, contrasting — appended to the system prompt)

Chosen to teach the *principle*, not a template: both directions, a win **and** a loss, the danger pattern shown **twice** (so it generalizes rather than anchors), and a skip. Source: `docs/strategies/intraday-hunter-study.md` Phase 3.

| # | Day | Prev-day structure | Gap | Decision | Outcome | Lesson |
|---|-----|--------------------|-----|----------|---------|--------|
| 1 | Mar 19 | (implied bearish) | BN −3.34% | ENTER CE (reversal) | Win +₹4.36L, 5 min | High-conviction big gap-down reversal; move is front-loaded into candle 1 |
| 2 | Feb 12 | V-recovery, support held | BN ~flat, NIFTY −0.36%, SENSEX −0.37% | ENTER CE (reversal) | Win +₹106K, but −49K drawdown first; SENSEX CE lost −24.9% while BN won | Hold through retracement; intra-basket divergence is normal |
| 3 | Apr 30 | Fast rally to 56,000 + rejection, closed ~55,000 | gap-down below 54,930 support | ENTER PE | Win +₹4.96L, 14 min | The bearish side — not everything is CE |
| 4 | Mar 20 | (not the selloff+rejection case) | medium gap-up | ENTER PE (fade) | **Loss −₹3.07L** | The danger pattern |
| 5 | Apr 24 | (not the selloff+rejection case) | medium gap-up | ENTER PE (fade) | **Loss −₹3.27L** | Same trap again → it's a rule, not a one-off (anti-anchor pair to #4) |
| 6 | Apr 22 | unclear / gap opposite to plan | — | SKIP | No trade | Skipping is the correct move |

Each exemplar is rendered in the prompt as a compact block: `previous_day_structure → thesis (trapped side) → actual gap → decision + direction + basket → what happened`. Exact per-day JSON to be lifted from the Phase-3 section when implementing.

---

## VIX-regime calibration (variant D) — promoted to live default 2026-06-29

**Symptom (from the first ~4 live paper days):** the agent (variant C) skipped clean low-VIX
mornings the real trader took. The Call 2 rationales cited the VIX number directly — e.g. Jun 25:
*"India VIX 12.95 gives only a ~0.82% expected move, so there is no room for a bought option to
recover from theta."*

**Root cause:** the **core** system prompt told the model that *"genuinely low India VIX → no buy
edge → SKIP"*, and `context.py` surfaces `expected_daily_move_pct = VIX/15.87` prominently in both
calls. At VIX ~13 that prints 0.82%, which the model read as "no room" — a misread, because that is
a **full-day 1-sigma** and a ~0.5% intraday move fits comfortably inside it. VIX 12–14 is the
trader's normal, profitable regime (the agent's own Jun 24 CE win was at VIX 13).

**The fix (variant D):** `VIX_REGIME_FIX_D` (appended only in variant D, so C is byte-identical:
`FIX_ADDENDUM_D = FIX_ADDENDUM_C + VIX_REGIME_FIX_D`) reframes VIX from an **entry gate** into an
**execution-management input** — low VIX is the normal/tradable regime; SKIP stays **tape-driven**
(two-sided chop / no thrust after ~5–6 candles), never the VIX number alone. Point 4 adds book-early
discipline: on a low-VIX/small-expected-move entry, set a tight target and tell the human in the
`rationale` (+ an `execution_note`) to *expect a small option move, book early, cut fast* — mirroring
how the real trader takes a small CE/PE profit on a quiet day rather than skipping or over-holding
into theta.

**June A/B (offline, engine-independent index first-touch / ≥0.5% favorable in the 2h hold —
`scripts/intraday_hunter/simulate.py --variant C` vs `--variant D` over Jun 1–29, 20 trading days,
both 19/19+ clean):**

- **Faithful harness:** sim-C reproduced the live decisions on Jun 22–25 exactly (ENTER, SKIP, ENTER, SKIP).
- **Surgically safe:** D made the **same decision as C on 17/20 days** — the fix is dormant above ~VIX 15.
- **D differs on exactly 3 low-VIX days, all C-SKIP → D-ENTER-CE:** Jun 11 (correct side, +0.48% scalp),
  Jun 25 (correct side, +0.23% scalp — matched the real trader), **Jun 29 (WRONG side, index fell
  −0.66% — a real loss C avoided).** Behaviorally D commits at the **first checkpoint (09:18)** on these
  days while C waits and skips by 09:20–09:28.
- **Honest read:** D's three extra trades = 2 correct-side scalps + 1 wrong-side loss → roughly
  neutral-to-slightly-negative on the *index proxy*, and the scalp value is the one thing the index
  gate **cannot** measure (index direction ≠ option P&L — the standing caveat that killed S7).

**Decision:** promoted D to the live default anyway (it is a paper SUGGESTER — no capital at risk —
and D is safe + better-aligned to the real trader, fixing the exact Jun-25 over-skip). **The standing
validation gate still applies:** the decisive test is **real option P&L** on the divergence days,
tracked live. Variant C is retained byte-identical so the A/B can be re-run / reverted at any time
(`INTRADAY_HUNTER_VARIANT=C`).

---

## Signal generation + YOLO execution (2026-06-29)

The agent feeds the existing signal/execution pipeline so the basket is paper-traded automatically
(shadow + a fixed-qty YOLO profile) — the real-option-P&L validation gate, run live rather than
deferred. It remains a discretionary LLM agent, **not** a candle-evaluated `BaseStrategy`.

**Flow:** `decision.run_call2` → on `ENTER` calls `signals.emit_signals_for_enter(session, run, live)`
(isolated; an emission failure never undoes the decision record). For each traded index leg it
opens the fixed strike structure `_IH_LEG_STRUCTURE` — **BANKNIFTY = two legs (ITM-2 + ITM-1);
NIFTY = ITM-1; SENSEX = ITM-1** (independent of the LLM's `strike` hint) — and per sub-leg:
1. reads the decision-time index spot from the Call 2 live context;
2. `option_resolver.resolve_option_details(..., itm_offsets=(depth, depth-1))` → the real strike at
   that ITM depth (one-strike fallback toward ATM if the exact strike has no premium) + expiry +
   **current premium**;
3. sizes a **symmetric 1:1** SL/target as `IH_SL_TGT_PCT` of the entry premium (default **60%** —
   widened from an initial 30% after real premium paths showed the tighter stop chopped winners
   that kept running well past a 30% target and got stopped out well before a 30% adverse move
   exhausted; a single knob that auto-scales the rupee move with the option price), and also
   converts that premium move to an **index-points distance via a delta approximation**
   (`_IH_DELTA_BY_DEPTH`: 0.50 ATM / 0.60 ITM-1 / 0.68 ITM-2): `index_move = (IH_SL_TGT_PCT ×
   premium) / delta`, then `index_sl = spot ∓ index_move`, `index_target = spot ± index_move` (CE
   stop below/target above, PE mirror) — stored as **informational context** (e.g. the Basket &
   Exits card) alongside the premium `stop_loss`/`target_price`, which are what the monitor
   actually exits on, via the normal per-position SL/target check (same as every other strategy);
4. **subscribes** the contract to the Fyers WS feed (IH bypasses `strategy_runner._resolve_option`,
   so it must subscribe here or the monitor gets no live option LTP / index ticks);
5. builds an OPTION `StrategySignal` (`strategy_name=intraday_hunter`, `confidence`=Call 2 conf,
   `fyers_option_symbol` set, `index_sl`/`index_target`/`ih_itm_depth` in `indicators`) and hands it
   to `strategy_runner._handle_signal(signal, executable=True)` (dedup → persist → broadcast →
   shadow + `on_new_signal`/YOLO). Idempotent per day (skips if an `intraday_hunter` signal already
   exists for the date; one structure per index even if the LLM repeats it).

**Strike-aware dedup:** because BANKNIFTY now opens two positions on the SAME index+strategy, the
per-symbol+strategy position-dedup guard in `shadow_executor` and `auto_executor` is made
**strike-aware for `intraday_hunter`** (also keyed on `fyers_option_symbol`) so the two BANKNIFTY
strikes co-exist; other strategies stay one-position-per-symbol+strategy.

**Exits — normal per-position, plus a manual whole-basket close:** each leg is exited individually
by `trade_monitor`'s standard per-position SL/target check (`_check_position`) on its own option
`stop_loss`/`target_price`, exactly like every other strategy — there is no automatic coordinated
basket exit. The `GET /intraday-hunter/basket` endpoint + the **Basket & Exits** UI card surface
each leg's live P&L and (informational) index spot vs `index_sl`/`index_target`, with a
select-all + "Close selected" control so the trader can close some or all of a book's legs
together by hand when the discretionary read calls for it.

**Why size the index distance from a premium %, not the LLM `invalidation_level`:** Call 2 emits
*one* `invalidation_level`, but the basket spans three indices at very different price scales, so one
number can't anchor all three. Sizing each leg from `IH_SL_TGT_PCT` of its own premium (then
delta-converting to that index's points for display) gives a per-leg, premium-scaled 1:1 that is
consistent across indices and option prices.

**Sizing — fixed basket, not capital-risk-scaled:** `lot_sizing.compute_lots_for_yolo` returns
`_IH_FIXED_LOTS = {BANKNIFTY: 2, NIFTY: 2, SENSEX: 2}` **lots PER LEG** for `intraday_hunter` (keyed
on `signal.symbol`) — BANKNIFTY's two legs = 4 lots total across two strikes; shadow stays 1 lot/leg.

**Wiring / seeds** (migration `a1c2e3f40b91`): a `strategy_configs` row (`intraday_hunter`,
`shadow_enabled`/`yolo_enabled` true, `is_active`/`auto_mode` false — not candle-evaluated) and the
`IntradayHunter` YOLO profile (`strategies=["intraday_hunter"]`, `min_confidence_for_execution=40` since
IH confidence runs ~60, `profit_cap=100000` ≈ non-binding so per-leg 1:1 targets do the booking,
`sort_order=100` to stay off the protected default slot — tune profit/loss caps in Settings → YOLO
Profiles). `intraday_hunter` is added to the `_INTRADAY_STRATEGIES` sets (strategy_runner today-scoped
dedup + `signal_expiry_task` 3:30 PM expiry).

**Validation caveat stands:** index direction ≠ option win-rate (the S7 lesson). The shadow book
(clean 1-lot real-premium P&L) + the fixed-qty YOLO book are how we now *measure* whether the agent's
baskets are actually profitable on premium before any capital.

---

## Call 1 — pre-open thesis (≈08:45 IST)

**Inputs (structured, fed as text + images):**
- Per index (NIFTY/BANKNIFTY/SENSEX): prev-day OHLC, **prev close**, day range, close-position-in-range (0=low,1=high), upper/lower wick fractions, one-line shape tag.
- Key levels: PDH, PDL, prev close, nearby round numbers.
- **Last 2–3 days** trend/sideways + the multi-day memory snapshot (see below).
- Calendar: is today an expiry day, for which index? upcoming holiday?
- Optional global cues: GIFT Nifty, US close, India VIX.
- **One prev-day chart image per index** (mplfinance).

**Output JSON:**
```json
{
  "trapped_side": "buyers | sellers | none",
  "thesis": "one-line in his idiom",
  "conditional_plan": {
    "if_gap_down": "e.g. CE reversal, target trapped longs",
    "if_flat_or_gap_up": "e.g. follow continuation"
  },
  "trigger_levels": {"BANKNIFTY": 55500, "NIFTY": 24000, "SENSEX": 77000},
  "invalidation": {"BANKNIFTY": 56000, "NIFTY": 24300, "SENSEX": 77900},
  "is_expiry": true,
  "expiry_index": "NIFTY | SENSEX | null",
  "notes": "anything notable (e.g. trapped side already fled yesterday)"
}
```

This object is stored and fed **verbatim** into every Call 2 that day.

---

## Call 2 — the decision (watcher-triggered, 9:15–9:30)

**Inputs:**
- The **Call 1 output, verbatim**.
- Live state: today's open + **gap% per index**; first N 1m candles (compact OHLC or stats); where price sits vs the prev-close pivot and vs PDH/PDL; current time; minutes since open.
- **Two chart images:** prev-day chart + live opening chart (mplfinance).
- Expiry reminder.

**Output JSON (full plan):**
```json
{
  "decision": "ENTER | WAIT | SKIP",
  "direction": "CE | PE | null",
  "trapped_side": "buyers | sellers | none",
  "thesis": "one-line",
  "basket": [
    {"index": "BANKNIFTY", "strikes": ["ATM", "ATM+1 OTM"], "option_type": "CE"},
    {"index": "NIFTY", "strikes": ["ATM"], "option_type": "CE"}
  ],
  "excluded_indices": [
    {"index": "SENSEX", "reason": "opened above key high; thesis invalid there"}
  ],
  "entry_trigger": "on pullback holding above 55,500",
  "invalidation_level": 55200,
  "target": "56,000 (prev-day rejection) or 1.5R",
  "is_expiry": true,
  "confidence": 72,
  "rationale": "plain-English, his voice — which side is trapped and why now",
  "recheck_in_minutes": null
}
```

- `direction` is a single field → the model literally cannot emit mixed sides.
- On `WAIT`, `recheck_in_minutes` is set (watcher re-fires, capped 9:30).
- A JSON parse failure is treated as `SKIP` (fail-safe).
- `confidence` is always shown; never used to auto-downgrade `ENTER`.

---

## Watcher logic (cadence)

```
at 09:15: compute gap% per index.
          if gap invalidates the Call-1 thesis (e.g. open above key high) → fire Call 2 (reassess/flip)
          else arm.
checkpoints: fire Call 2 at ~09:18.
          decision == ENTER → finalize + surface on UI (done for the day)
          decision == WAIT  → re-fire at now + recheck_in_minutes (capped 09:30)
          decision == SKIP  → done
at 09:30: if still un-decided → one final Call 2 ("no clean trigger appeared — decide or skip")
```

Target: ~1 LLM call on a normal day, occasionally 2.

---

## Multi-day memory snapshot (fed to Call 1)

Compact, **structural, no rupee P&L**:
```json
[
  {"date": "2026-06-18", "trapped_side": "buyers", "direction": "PE", "thesis_played_out": true},
  {"date": "2026-06-17", "trapped_side": "sellers", "direction": "CE", "thesis_played_out": false}
]
```
Built from the stored run rows. Powers the "trapped side already fled / Nth reversal in a row" reasoning. The human-facing P&L lives only on the UI history (below), never in the prompt.

---

## Chart rendering (mplfinance)

- Server-side from `market_data_1m` (and prev-day from `market_data_daily` or aggregated 1m). No browser.
- Call 1: one prev-day chart per index (candles + prev close line + PDH/PDL + round numbers).
- Call 2: prev-day chart + a live opening chart (9:15→now) with the prev-close pivot and key levels drawn — mirroring his red-resistance / green-support annotation style.
- Saved as PNGs to a temp/scoped dir; paths passed to the `claude` CLI; Read tool renders them.

---

## Data model

New table `intraday_hunter_runs` (one row per trading day, updated as the day progresses):

| Column | Notes |
|--------|-------|
| `id`, `trading_date` (unique), `created_at`, `updated_at` | |
| `status` | `PENDING / THESIS_READY / WATCHING / ENTER / WAIT / SKIP` |
| `is_expiry`, `expiry_index` | |
| `call1_json` | Call 1 output (thesis/plan/levels) |
| `call1_chart_paths` | prev-day chart image paths |
| `call2_json` | latest Call 2 output (full plan) |
| `call2_history` | array of all Call 2 outputs that day (audit) |
| `call2_chart_paths` | prev-day + opening chart paths |
| `decision`, `direction`, `confidence` | denormalized for fast list queries |
| `outcome_played_out` | filled post-hoc by the validation job (structural; for the memory snapshot) |
| `realized_outcome_note` | human-facing P&L/outcome (UI only — NOT fed to prompts) |

---

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/intraday-hunter/today` | Today's run row (thesis + latest decision + chart URLs + status) |
| GET | `/api/v1/intraday-hunter/history?limit=N` | Prior days (decision, direction, confidence, outcome) |
| GET | `/api/v1/intraday-hunter/basket` | Today's basket legs (open + closed-today): book, strike, P&L, live index spot vs index SL/target, exit reason — backs the Basket & Exits card |
| POST | `/api/v1/intraday-hunter/run-call1` | Manual re-run of Call 1 (testing) |
| POST | `/api/v1/intraday-hunter/run-call2` | Force a Call 2 (testing) |
| GET | `/api/v1/intraday-hunter/chart/{run_id}/{which}` | Serve a rendered PNG (`prev_day` / `opening`) |

---

## UI page `/intraday-hunter`

Next.js 15 / Tailwind v4 dark / Zustand, polled REST (~15–30s; decisions are sparse — no tick rate needed). Sidebar nav link added.

**"Today" view (at a glance):**
- Header: date, expiry badge, status chip (`PENDING → THESIS_READY → WATCHING → ENTER/WAIT/SKIP`).
- **Pre-market thesis (Call 1):** trapped side + evidence, conditional plan, per-index key levels, generated-at time.
- **Live decision (Call 2):** big decision + direction + confidence number; the basket (indices + strikes); excluded indices with reasons; entry trigger / invalidation / target; rationale in his voice; the two chart images; fired-at time. **+ a Decision Log** rendering every Call 2 in `call2_history` (each WAIT→…→ENTER/SKIP with its time, decision, direction, confidence, recheck, rationale) — re-running Call 2 no longer overwrites the verdict on screen.
- **Basket & Exits card** (`GET /intraday-hunter/basket`): today's emitted legs (open + closed-today) — book, index·strike·CE/PE·ITM-depth, entry/LTP, P&L, and the live **index spot vs index SL/target** (informational context, not the exit trigger — each leg's own premium SL/target closes it via the normal per-position check). Open real-book legs carry a checkbox + select-all + "Close selected" for a manual whole-basket close; shadow legs are read-only; closed legs show the exit reason + realized P&L.
- **History list:** prior days — decision, direction, confidence, and `thesis_played_out` / human P&L note (the part not fed to the model).

Keep it clean and crisp, matching the existing page/component conventions. Optional manual "re-run Call 1 / force Call 2" buttons for testing.

---

## Autorun

- Backend asyncio scheduled task → **Call 1 at ~08:45 IST**.
- Watcher hooks the existing **1m candle-close evaluation loop** for Call 2 (9:15–9:30 + backstop).
- LLM = `asyncio` subprocess to the `claude` CLI (`CLAUDE_CODE_OAUTH_TOKEN`, model `claude-opus-4-8`).
- **Prereqs on the prod VM:** `claude` CLI installed; OAuth token as a secret (run `claude setup-token` on a browser machine); deploy before market open (no backend restarts during market hours).
- Testable in `MARKET_MODE=simulated`.

---

## Validation plan (don't ship on vibes)

- Every Call 2 output is logged (`intraday_hunter_runs.call2_history`).
- Score the suggestions with the **same engine-independent first-touch harness** used for S2/S5/S7 (`analyze_strategy2_signal_accuracy.py` style): did the suggested direction reach a target before a stop, forward direction at +15/30/60 min + EOD, vs base rate.
- Run **paper** for several weeks before trusting it. Carry the standing caveat: **index direction ≠ option win rate** (theta + tight-stop whipsaw — the lesson that killed S7). A directional edge is necessary, not sufficient.

---

## Security / operational notes

- ⚠️ `scripts/claude_oauth_test.py` currently has a **real OAuth token committed** (line 33). Rotate it (`claude setup-token`), move to env/secret, and `.gitignore`.
- Subscription auth draws from Pro/Max limits, not API credits — mind subscription rate limits.
- Production DB is read-only from local; develop/verify against local + simulated mode.

---

## File map (to be created when building)

```
backend/app/services/intraday_hunter/
  thesis.py          # Call 1: build context, render prev-day charts, call claude, parse
  decision.py        # Call 2: build live context, render opening chart, call claude, parse
  watcher.py         # 9:15–9:30 cadence, WAIT re-fire, backstop
  llm_cli.py         # claude CLI subprocess wrapper (OAuth, JSON parse, image paths)
  charts.py          # mplfinance renderers (prev-day, opening)
  prompts.py         # system prompt + few-shot + Call 1/Call 2 templates
backend/app/api/v1/intraday_hunter.py   # the 5 endpoints
backend/app/models/  (intraday_hunter_runs)  + Alembic migration
backend/app/core/scheduler hook (08:45 task) + candle-close watcher hook
frontend/src/app/intraday-hunter/page.tsx
frontend/src/components/intraday-hunter/*
frontend/src/lib/api.ts + types.ts (endpoints + types)
scripts/intraday_hunter/prototype_agent.py   # offline prompt/chart prototype (review before wiring)
```

Docs to update when building: `backend/CLAUDE.md`, `frontend/CLAUDE.md`, `ARCHITECTURE.md`, root `CLAUDE.md` (strategy table / docs index).

---

## Open items (decide at build time)

- Exact few-shot per-day JSON (lift from Phase 3) and whether to add a 7th (a reassess/flip example like Feb 13) if it helps without bloating the cache.
- N = how many opening 1m candles to feed Call 2 (start ~5).
- Whether `SKIP`/`WAIT` surface any passive notification beyond the page (currently page-only).
- Confidence display thresholds / colour bands on the UI.
```
