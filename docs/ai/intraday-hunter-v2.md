# Intraday Hunter v2 — parallel paper strategy + learning loop

**Status:** BUILT (paper only). Runs **beside** v1 (`docs/ai/intraday-hunter-agent.md`, unchanged) on its own run rows (`intraday_hunter_runs.variant='v2'`), its own strategy name `intraday_hunter_v2`, its own YOLO profile **`IH-v2`** and the shadow book. Code: `backend/app/services/intraday_hunter_v2/`, scheduler `backend/app/tasks/intraday_hunter_v2_task.py`, API `backend/app/api/v1/intraday_hunter_v2.py`, migration `c4d2e8f1a703`.

## Why v2 exists (evidence, 26 Jun – 1 Oct 2026, 67 trading days)

- **The teacher** (@IntradayHunter, read from his Zerodha positions screen, Jul–Sep) made +₹78L over 65 days, 47W/18L (72%). He trades **every day** with **one direction** across a 4-leg basket (BANKNIFTY ×2 strikes, NIFTY, SENSEX, all ~ATM).
  - He enters and exits the **whole basket together**, usually in by 09:16–09:20.
  - Median hold is 31 min. P&L is ~1:1 in rupees at the basket level: average winning day +₹2.65L, average losing day −₹2.59L.
- **v1** (paper YOLO `IntradayHunter`) is flat: −₹6.3K over 32 ENTER days (t ≈ −0.09). It skips about half the days.
  - Its direction is a coin flip and leans CE.
  - It is slow, with ~2 min from candle to signal.
  - It exits per leg.
  - It never learned: `outcome_played_out` was never written.
- **Stop-hunting:** the teacher **rides** the run into other traders' stops and does not fade sweeps. With a PDH/PDL broken before his entry, he went with the break 64% of the time. He won 74% going with it vs 46% going against it.
- **Offline signals:**
  - His **evening plan** as a filter on v1 split the book +₹79.9K vs −₹86.2K (t ≈ 1.4).
  - **Opening OI flow** matched the clean side on 61% of 33 days.
  - "Ride the PDH/PDL break" (Rule A) was 68% right, but not robust (73%→50% between the two halves).
  - "Plan agrees with the open" as a trigger was rejected.
- **Conclusion:** no simple rule reproduces him. v2 trades a best-guess policy that uses all of the above, and **logs every input and every candidate arm each minute** so a learning loop can find what works. Research CSVs: `~/Downloads/ih_research/live_review_2026q3/` (calibration / fixtures only — nothing reads them at runtime).

## v1 vs v2

| | v1 (unchanged) | v2 |
|---|---|---|
| Strategy / book | `intraday_hunter` / YOLO `IntradayHunter` | `intraday_hunter_v2` / YOLO `IH-v2` (subscribes only to v2, exec confidence 0) + shadow (1 lot) |
| Evening input | — | teacher plan (00:00 / 06:00 / 08:00 jobs; 08:30 Telegram alert if missing → `teacher_plan_missing`) |
| Call 1 (08:45) | prev-day charts + VIX + structural memory | same base **+** teacher plan + drawn levels, pre-open stop pools, last N **graded lessons** (separate v2 prompt). A failed Call 1 does NOT SKIP the day |
| Call 2 | 09:18 with charts, WAIT re-checks to 09:30 | **09:16** (on the 09:15 candle close), **every minute** to a **09:25** deadline, **text-only**, model `call2_model` (default `claude-sonnet-5-5`); latency logged per call (~15s measured) |
| Direction logic | wait for break + hold | stop-hunting: identify pools, **ride the break**, CE/PE symmetric, default = trade the morning; SKIP only for two-sided chop with a required `skip_reason_code` |
| Gates | — | plan gate + OI gate, **shadow-only** (`enforce_*_gate=false`): computed + logged, never block |
| Strikes | BN ITM-2 + ITM-1, NIFTY ITM-1, SENSEX ITM-1 | **BN ATM + OTM-1, NIFTY ATM, SENSEX ATM** (`leg_structure`; option_resolver depth −1 = OTM-1) |
| Size | fixed 2 lots/leg | same fixed 2 lots/leg (`lot_sizing`); shadow 1 lot |
| Exit | per leg ±60% premium, 15:25 | **basket-level 1:1** (below); all legs together |
| Manual close | multi-select per-leg card | v2 legs are not in that card; one **"Close v2 basket"** emergency button (`MANUAL_BASKET`) |

## Runtime controls

- **Kill switch, no redeploy:** set `strategy_configs.is_active` for `intraday_hunter_v2` to false. It is seeded true. Toggle it with `PUT /api/v1/strategies/intraday_hunter_v2 {"is_active": false}`, from Settings, or with SQL.
  - `params.v2_active()` re-reads the flag every ≤15s and **fails closed**.
  - When it reads false, the following stop: every v2 scheduler job, the Call 2 watcher, the minute log, the ATM±2 capture, and signal emission.
  - **Open v2 baskets are still exited** by the basket monitor.
  - The row is harmless to the candle evaluator: there is no registry class and `auto_mode=false`.
- **Hard off:** env `INTRADAY_HUNTER_V2_ENABLED=false`.
- **Tunables:** `strategy_configs.parameters['intraday_hunter_v2']`, with defaults in `params.INTRADAY_HUNTER_V2_DEFAULTS`. The keys are `basket_tp_sl_pct`, `basket_time_exit`, `round_hold_*`, `round_step`, `call2_first`, `call2_deadline`, `call2_model`, `call2_timeout_s`, `call1_model`, `enforce_plan_gate`, `enforce_oi_gate`, `oi_flow_deadband`, `opening_type_gap_pct`, `opening_range_end`, `leg_structure`, `minute_log_start/end`, `capture_strikes_each_side`, `lessons_n` and `grade_barriers`. They will be recalibrated from the exit-behaviour study.
- **Jev arm:** `JEV_ENABLED` + `OPENROUTER_API_KEY` + `JEV_MODEL` (pinned `typesafe/jev-1.13`). It is shadow-only and never trades.

## Basket-level exit (`basket.py`, `trade_monitor._check_ih_v2_baskets`)

Open v2 legs are grouped per **(trading day, book)**, where the book is the YOLO profile or SHADOW. `T = basket_tp_sl_pct × basket cost`, where cost = Σ entry premium × qty and the default pct is 0.20. MTM = Σ (exit-side value − entry) × qty, with the exit side being the **bid** under `fill_model=BID_ASK` (LTP fallback; LTP under the `LTP` fill model). A partial quote set never decides, except at the time backstop.

| Rule | Exit reason |
|---|---|
| MTM ≤ −T → close all legs | `BASKET_STOP` |
| MTM ≥ +T → close all legs | `BASKET_TARGET` |
| **Round-number hold**: at MTM ≥ 0.9T, if a strict majority of the traded indices sit within `round_hold_points` (NIFTY 10, BN/SENSEX 30) of the next round number (NIFTY 100s, BN/SENSEX 500s) **in the trade direction**, hold for the touch. Exit on the first touch, OR a giveback to 0.75T, OR after 5 min. One activation per basket; activation and result are logged as `IH_V2_ROUND_HOLD` agent logs | `BASKET_TARGET` (sub-reason `round_touch` / `round_giveback` / `round_timeout`) |
| Backstop at `basket_time_exit` (11:30) | `BASKET_TIME` |
| Last resort: if the basket check raises (logged, Telegram once per day), v2 legs still close at 15:25 | `TIME_EXIT` |
| User emergency close of the YOLO basket. The **shadow basket keeps running** under the system rules and is the counterfactual. The system's view at the close (MTM, T, round-hold state) goes into a `MANUAL_BASKET_CLOSE` log | `MANUAL_BASKET` |

The per-leg SL / per-lot stop / target / trailing / 15:25 checks are **bypassed** for v2 legs. Bypassing was chosen over setting the levels far away. The legs' `stop_loss`/`target_price` fields are informational: each leg's share of the ±T band. A v2 leg is also never stale-closed alone.

## Level facts (`levels.py`, pure)

For each index as of any minute, every pool gets its distance (pts and %) and its broken status since the open, plus `holding`. The pools are prev close, PDH, PDL, the round numbers above and below, the opening-range high/low (09:15–09:19, available from 09:20), the session high/low, and the teacher's drawn levels.

Breaking rules:
- PDH breaks when the open is above it or any high trades through it. PDL mirrors this.
- Every other level breaks on a cross relative to the open.
- `holding` = price is still beyond a broken level. False means it was swept and reclaimed: a failed break, the only case where fading can apply.

Also computed:
- **pools_taken**, and the nearest untaken pool ahead and behind for each side.
- **Opening type:** basket-average gap ≥ +0.15% is `gap_up`, ≤ −0.15% is `gap_down`, otherwise `flat`.
- **Rule A:** ride the PDH/PDL break when 2 of 3 indices broke the same way.

`describe_facts` renders the facts as plain words (no arithmetic left to do) for Call 2 and Jev.

## Call 2 cadence (`watcher.py`, `decision.py`)

The NIFTY candle stamped 09:15 closes at ~09:16:00, so **decision time = candle minute + 1**.

- **Single-flight:** if a call is still in flight when the next minute arrives, that minute is skipped. The exception is the deadline: a deadline call then runs as soon as the in-flight call returns, so a day never ends on WAIT.
- **Normalization:**
  - An LLM failure is WAIT before the deadline and `SKIP/OTHER` at it.
  - ENTER without CE/PE becomes WAIT.
  - WAIT at the deadline becomes `SKIP/NO_POOL_BROKEN_BY_DEADLINE`.
  - SKIP requires a code: `TWO_SIDED_CHOP` | `NO_POOL_BROKEN_BY_DEADLINE` | `DATA_MISSING` | `OTHER`.
- **Logged on each record:** `_latency_ms` (the model call) and `_hook_to_decision_ms` (the whole candle-hook→decision path).
- **Thesis:** if Call 1 never ran, a lazy Call 1 starts in the background and Call 2 decides on live facts meanwhile. A late Call 1 never clobbers a decision status.

## Data capture

- **ATM ±2 premium paths** (`capture.py`): at 09:10 and again at 09:16:30 (re-centred on the real open), subscribe ATM±2 CE+PE for NIFTY, BANKNIFTY and SENSEX, nearest expiry, about 30 contracts. This runs **every trading day, including skip days**.
  - `feed_manager` persists their 1m candles under the Fyers option symbol.
  - The list is stored in Redis `ih_v2:capture:{date}`, re-subscribed after WS reconnects (`_collect_dynamic_symbols`) and used by grading.
  - Prod sees ~170 WS symbols against the Fyers cap of 5000.
- **1-minute option chain 09:15–09:45** (`oi_snapshot_task.fetch_oi_snapshots_hf`) for NIFTY, BANKNIFTY and SENSEX into `oi_snapshots`. The 3-minute job is unchanged.
- **Opening OI flow** (`oi_flow.py`): the per-index mean of ((ΔPE − ΔCE) / (CE+PE OI at 09:16)) from 09:16→09:19, nearest expiry. This reproduces the research `flow` column. Positive means CE.
- **Order flow** (`orderflow.py`): `fyers_ws_client` keeps `tot_buy_qty`, `tot_sell_qty`, `bid_size` and `ask_size`. These are the Fyers SDK `map.json` names; a live-tick verification is pending.
  - They go into the Redis price cache.
  - Per-minute aggregates are computed for the index futures and the ATM CE/PE: buy/sell imbalance, average spread, bid/ask size imbalance, tick-rule volume delta, and depth imbalance when `IH_V2_DEPTH_ENABLED` is set. DepthUpdate messages are routed away from the price path.
- **`ih_minute_log`** (`minute_log.py`): one row per index per minute, 09:15–10:45, plus every minute while a v2 position is open. It is written from the NIFTY candle-close hook, after a ~4s settle so the other indices' candles persist.
  - `features`: OHLC, level facts, opening type, OI flow, order flow, VIX, the teacher's side for today's opening.
  - `arms`: `rule_a`, `plan_side`, `oi_flow_side`, `v1_state`, `v2_llm`, `gates` (for the v2 and rule_a sides), and `jev`.
  - It is failure-isolated and never raises into the feed.

## Teacher ingestion (`teacher/`)

- **Plan job** (00:00 / 06:00 / 08:00):
  - Finds "Prediction For <date>" on `@IntradayHunter/videos` with yt-dlp, using `player_client=web_embedded` to pass YouTube's bot check (with an optional cookies fallback via `IH_YTDLP_COOKIES`).
  - Fetches the Hindi auto-subs and grabs 30/60/90% keyframes with ffmpeg.
  - Claude (vision) returns `{gap_up_side, flat_side, gap_down_side ∈ CE|PE|none, bias, levels_onscreen, levels_audio, summary}`.
- **Live job** (13:00 / 15:00):
  - Finds the day's "Live Bank Nifty Option Trading" video and samples a frame every 2s.
  - Tesseract reads the taskbar clock (often 180° rotated, so "81:60" → 09:18) and the positions strip.
  - Entry = the earliest clock with positions open (the video opens with a teaser). Exit = the first all-closed clock after entry.
  - Claude vision reads the leg and total P&L, which are sum-checked.
- **Storage and alerts:** results go to `ih_teacher_days`. Every failure (video not found, blocked/429/403, parse failed, tool missing) is Telegram-alerted with the job, date and error type.
- **Mac fallback:** `scripts/intraday_hunter/teacher_ingest_local.py --date D [--plan] [--live] [--whisper] --push URL` runs the same pipeline locally and POSTs to `POST /api/v1/intraday-hunter/teacher/ingest` (body `{trading_date, plan, plan_video_id, live, live_video_id}`).
- **Runtime image:** ffmpeg + tesseract-ocr (apt) and yt-dlp (pyproject floor).

## Learning loop

1. **Nightly grade** (16:00; FINAL once the teacher's live trade lands) — `grading.grade_day` → `ih_day_grades`:
   - **market:** first-touch clean side from 09:16 at (+0.25/−0.20%) and (+0.30/−0.25%) per index, plus the 2-of-3 majority (same-candle tie = stop). Also the index move to 10:30 and to EOD.
   - **teacher:** side, entry/exit clocks, P&L, whether a PDH/PDL broke before his entry and whether he went with it.
   - **v1 / v2:** decision, side, decision time, real paper net P&L (YOLO and SHADOW), exit reasons, and the barrier label from their own entry.
   - **arms:** each arm's first side in the decision window, plus a **counterfactual basket P&L on the real captured premium candles**. The replay uses v2's basket shape, 1 lot per leg, and the same `evaluate_basket` rules: entry at the close of the candle before the decision, evaluated on each later candle close, LTP basis.
   - **gates:** v2's P&L with each gate enforced.
   - Also fills **v1's `outcome_played_out`**: on ENTER, side == clean side; on SKIP, no clean side.
2. **Ledger** — `GET /v2/ledger`, per arm over the last 20 and 60 graded days: n, right-side %, win %, mean counterfactual P&L, t-stat, first-half vs second-half mean, and the gate what-ifs.
3. **Lessons** — each grade carries a compact lesson: `{date, opening, pools_broken, teacher, v2, arms_right, one_line_takeaway}`. The last `lessons_n` lessons feed v2's Call 1, replacing v1's empty `thesis_played_out` memory.
4. **Weekly review** (Saturday 10:00) — Claude reads the ledger and grades and proposes evidence-backed changes. These are stored in `ih_weekly_reviews` with a Telegram summary.
   - **Nothing is auto-applied.** Approved changes ship as a challenger variant on a new paper profile.
   - Promotion requires ≥20 trading days with both halves positive.
5. **Calibration** — once there are ≥30 v2 trades, an isotonic map from confidence to win rate is stored on the review row. It is recorded only, never used for sizing.

## Reliability alerts

- 09:30: Telegram if v1 or v2 has no ENTER/SKIP decision.
- 09:20 and then every 30 min: Telegram if an index has no candles, or stale ones, for the session (the 2 Sep gap).
- Each alert fires once per day per key and is always logged (`IH v2 ALERT [...]`). That is visible locally with `TELEGRAM_ENABLED=false`.

## Isolation guarantees (tested in `tests/test_services/test_ih_v2_isolation.py`)

- **v1's run lookups** are `variant='v1'`-scoped (the store's default).
- **Open-position checks:** a v2 open position only blocks v2's own same contract. v2 positions never block v1 or any other strategy (`_has_open_position`).
- **Dedup:** v2 dedup is strike-aware, so BN ATM and OTM are distinct signals. v1 dedup ignores v2 signals.
- **Wrapped hooks:** every v2 hook in a shared path is wrapped: the candle hook, depth routing, order-flow tracker and basket check. A crashing basket check does not stop the per-position monitor.
- **Shadow gate:** the shadow executor skips its confidence gate for v2 only, so the shadow and YOLO books always open together.

## Data model

- `intraday_hunter_runs.variant`: `v1` | `v2`, unique (trading_date, variant).
- `ih_minute_log`: (trading_date, minute_ts, index, variant, features JSONB, arms JSONB), unique on the first four.
- `ih_teacher_days`: trading_date PK, plan, live, video ids, fetched_at, status, source, errors.
- `ih_day_grades`: trading_date PK, status PRELIM|FINAL, market, teacher, v1, v2, arms, gates, lesson.
- `ih_weekly_reviews`: week_ending, ledger, proposal, calibration, summary, status.
