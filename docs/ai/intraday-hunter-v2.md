# Intraday Hunter v2 — parallel paper strategy + learning loop

Learning loop, weekly proposals and the change-approval procedure: see `docs/ai/intraday-hunter-v2-learning-loop.md`.

**Status:** BUILT (paper only). Runs **beside** v1 (`docs/ai/intraday-hunter-agent.md`) on its own run rows (`intraday_hunter_runs.variant='v2'`), its own strategy name `intraday_hunter_v2`, its own YOLO profile **`IH-v2`** and the shadow book. Code: `backend/app/services/intraday_hunter_v2/`, scheduler `backend/app/tasks/intraday_hunter_v2_task.py`, API `backend/app/api/v1/intraday_hunter_v2.py`, migration `c4d2e8f1a703`.

## Why v2 exists (evidence, 26 Jun – 1 Oct 2026, 67 trading days)

- **The teacher** (@IntradayHunter, read from his Zerodha positions screen, Jul–Sep) made +₹78L over 65 days, 47W/18L (72%). He trades **every day** with **one direction** across a 4-leg basket (BANKNIFTY ×2 strikes, NIFTY, SENSEX, all ~ATM).
  - He enters and exits the **whole basket together**. There is no fixed entry clock: median 09:18, about 4 in 10 by 09:17, a quarter after 09:21.
  - Median hold is 31 min. P&L is ~1:1 in rupees at the basket level: average winning day +₹2.65L, average losing day −₹2.59L.
- **v1** (paper YOLO `IntradayHunter`) is flat: −₹6.3K over 32 ENTER days (t ≈ −0.09). It skips about half the days.
  - Its direction is a coin flip and leans CE.
  - It is slow, with ~2 min from candle to signal.
  - It exits per leg.
  - It never learned: `outcome_played_out` was never written.
- **Breaks:** when a PDH/PDL had closed beyond and was still beyond at his entry, he went with it on 23 of 31 days (NIFTY). His talk about whose stops get hit does **not** mark his good days (see the entry-timing study below), so v2 uses levels to describe structure and targets, not as a "ride the break" default.
- **Offline signals:**
  - His **evening plan** as a filter on v1 split the book +₹79.9K vs −₹86.2K (t ≈ 1.4).
  - **Opening OI flow** matched the clean side on 61% of 33 days.
  - "Ride the PDH/PDL break" (Rule A) was 68% right, but not robust (73%→50% between the two halves).
  - "Plan agrees with the open" as a trigger was rejected.
- **Entry timing and pre-entry reasoning (72 live days, studied 2026-10-15; `~/Downloads/ih_research/psych_study/FINDINGS.md`):**
  - His edge is mostly the MOMENT of entry. On his own side, a 09:16 entry moved the right way about 60% of the time over 15 minutes; at the minute he chose, 71%. No fixed clock or mechanical pullback rule reproduced that.
  - He follows his evening plan's side on 54 of 67 days; when he leaves it he wins 11 of 13.
  - Blind-coded narration before each entry: the one habit that separates his wins is refusing to chase a big/fast candle — he waits for the counter-move to stall and enters with the larger move (15 of 16 days won vs 39 of 56). Stop/trap talk, waiting, two-case talk and confidence words do not separate wins from losses.
- **Conclusion:** no simple rule reproduces him. v2 trades a best-guess policy that uses all of the above, and **logs every input and every candidate arm each minute** so a learning loop can find what works. Research CSVs: `~/Downloads/ih_research/live_review_2026q3/` (calibration / fixtures only — nothing reads them at runtime).

## v1 vs v2

| | v1 | v2 |
|---|---|---|
| Strategy / book | `intraday_hunter` / YOLO `IntradayHunter` | `intraday_hunter_v2` / YOLO `IH-v2` (subscribes only to v2, exec confidence 0) + shadow (1 lot) |
| Evening input | — | teacher plan (00:00 / 06:00 / 08:00 jobs; 08:30 Telegram alert if missing → `teacher_plan_missing`) |
| Call 1 (08:45) | prev-day charts + VIX + structural memory | same base **+** teacher plan + drawn levels, pre-open stop pools, last N **graded lessons** (separate v2 prompt). A failed Call 1 does NOT SKIP the day |
| Call 2 | 09:18 with charts, WAIT re-checks to 09:30 | **09:16** (on the 09:15 candle close), **every minute** to a **09:30** deadline, **text-only**, model `call2_model` (default `claude-sonnet-5-5`); latency logged per call (~15s measured) |
| Direction logic | wait for break + hold | **plan side first, then the moment** (see Call 2 prompt below): start from the teacher's side for today's opening; override only on a level closed beyond for 2+ candles on 2 of 3 indices; never enter on the candle after an impulse — wait for the counter-move to stall, then enter with the larger move; WAIT is normal and must name what it waits for; CE/PE symmetric; SKIP when neither case confirms (required `skip_reason_code`) |
| Gates | — | plan gate + OI gate, **shadow-only** (`enforce_*_gate=false`): computed + logged, never block |
| Strikes | BN ITM-2 + ITM-1, NIFTY ITM-1, SENSEX ITM-1 | **BN ATM + OTM-1, NIFTY ATM, SENSEX ATM** (`leg_structure`; option_resolver depth −1 = OTM-1) |
| Size | fixed 2 lots/leg | same fixed 2 lots/leg (`lot_sizing`); shadow 1 lot |
| Exit | **basket-level 1:1**, all legs together — same `evaluate_basket` rules with v1's own params (rupees per lot, 11:30, no round-hold) | **basket-level 1:1** (below); all legs together |
| Manual close | one **"Close v1 basket"** emergency button (`MANUAL_BASKET`) | one **"Close v2 basket"** emergency button (`MANUAL_BASKET`) |

## Runtime controls

- **Kill switch, no redeploy:** set `strategy_configs.is_active` for `intraday_hunter_v2` to false. It is seeded true. Toggle it with `PUT /api/v1/strategies/intraday_hunter_v2 {"is_active": false}`, from Settings, or with SQL.
  - `params.v2_active()` re-reads the flag every ≤15s and **fails closed**.
  - When it reads false, the following stop: every v2 scheduler job, the Call 2 watcher, the minute log, the ATM±2 capture, and signal emission.
  - **Open v2 baskets are still exited** by the basket monitor.
  - The row is harmless to the candle evaluator: there is no registry class and `auto_mode=false`.
- **Hard off:** env `INTRADAY_HUNTER_V2_ENABLED=false`.
- **Tunables:** `strategy_configs.parameters['intraday_hunter_v2']`, with defaults in `params.INTRADAY_HUNTER_V2_DEFAULTS`. The keys are `basket_tp_sl_pct`, `basket_t_mode`, `rupees_per_lot`, `basket_time_exit`, `call2_prompt_addendum`, `round_hold_*`, `round_step`, `call2_first`, `call2_deadline`, `call2_model`, `call2_timeout_s`, `call1_model`, `enforce_plan_gate`, `enforce_oi_gate`, `oi_flow_deadband`, `opening_type_gap_pct`, `opening_range_end`, `leg_structure`, `minute_log_start/end`, `capture_strikes_each_side`, `lessons_n` and `grade_barriers`. They will be recalibrated from the exit-behaviour study.
- **Jev arm:** `JEV_ENABLED` + `OPENROUTER_API_KEY` + `JEV_MODEL` (pinned `typesafe/jev-1.13`). **ON in production:** `deploy.yml` writes `OPENROUTER_API_KEY` (GH secret) and `JEV_ENABLED=true` into the prod `.env`. It is shadow-only and never trades. The client never raises: a non-200, timeout (3s) or unparseable reply is logged as `arms.jev = {error, latency_ms}` and the rest of the minute-log row is written as usual (`tests/test_services/test_ih_v2_jev.py`). Verified live against OpenRouter on 2026-10-09: HTTP 200 in ~600ms with parsed answers.

## Basket-level exit (`basket.py`, `trade_monitor._check_ih_baskets`)

The same monitor check and pure rules also exit v1's baskets, grouped separately (the basket key includes the strategy) and with v1's own params — see `docs/ai/intraday-hunter-agent.md` §Exits.

Open v2 legs are grouped per **(trading day, book)**, where the book is the YOLO profile or SHADOW. ±T comes from `basket.basket_target`, picked by `basket_t_mode`:

- **`pct`** (default): `T = basket_tp_sl_pct × basket cost`, where cost = Σ entry premium × qty (default pct 0.20).
- **`rupees`** (config-gated, off by default): `T = Σ over the legs actually traded of lots × rupees_per_lot[index]`, a fixed rupee book like the teacher's. The exit study (72 days) found he books a fixed rupee amount ~1:1 (median win ₹2.54L, loss ₹2.63L; the rupee CV is half the % CV). `rupees_per_lot` is seeded at 15% of one lot's cost in the 2026-10-09 prod basket (BANKNIFTY ₹3,915, NIFTY ₹1,640, SENSEX ₹862), so the 2-lot YOLO basket gets ≈ ₹20.7K and the 1-lot shadow half. An excluded index contributes nothing. The live monitor, `/v2/basket`, the manual-close snapshot and grading's counterfactual replay all use the same function. MTM = Σ (exit-side value − entry) × qty, with the exit side being the **bid** under `fill_model=BID_ASK` (LTP fallback; LTP under the `LTP` fill model). A partial quote set never decides, except at the time backstop.

| Rule | Exit reason |
|---|---|
| MTM ≤ −T → close all legs | `BASKET_STOP` |
| MTM ≥ +T → close all legs | `BASKET_TARGET` |
| **Round-number hold**: at MTM ≥ 0.9T, if a strict majority of the traded indices sit within `round_hold_points` (NIFTY 10, BN/SENSEX 30) of the next round number (NIFTY 100s, BN/SENSEX 500s) **in the trade direction**, hold for the touch. Exit on the first touch, OR a giveback to 0.75T, OR after 5 min. One activation per basket; activation and result are logged as `IH_V2_ROUND_HOLD` agent logs | `BASKET_TARGET` (sub-reason `round_touch` / `round_giveback` / `round_timeout`) |
| Backstop at `basket_time_exit` (11:30) | `BASKET_TIME` |
| (Quotes) A leg whose WS tick is missing or older than 60s is valued from a Fyers REST LTP (throttled to one call per 5s per symbol), so MTM is never blank because of one missing tick | — |
| Last resort: if the basket check raises (logged, Telegram once per day), v2 legs still close at 15:25 | `TIME_EXIT` |
| User emergency close of the YOLO basket. The **shadow basket keeps running** under the system rules and is the counterfactual. The system's view at the close (MTM, T, round-hold state) goes into a `MANUAL_BASKET_CLOSE` log | `MANUAL_BASKET` |

The per-leg SL / per-lot stop / target / trailing / 15:25 checks are **bypassed** for v2 legs. Bypassing was chosen over setting the levels far away. The legs' `stop_loss`/`target_price` fields are informational: each leg's share of the ±T band. A v2 leg is also never stale-closed alone.

## Level facts (`levels.py`, pure)

For each index as of any minute, every pool gets its distance (pts and %) and its broken status since the open, plus `holding`. The pools are prev close, PDH, PDL, the round numbers above and below, the opening-range high/low (09:15–09:19, available from 09:20), the session high/low, and the teacher's drawn levels.

Breaking rules:
- PDH breaks when the open is above it or any high trades through it. PDL mirrors this.
- Every other level breaks on a cross relative to the open.
- `holding` = price is still beyond a broken level. False means it was swept and reclaimed: a failed break.
- `closes_beyond` = consecutive most-recent 1m closes beyond a broken level; `ever_closed_beyond` = any close beyond since the break. A break that never closed beyond is **wick-only** (a failed break), however far the wick went. `describe_facts` says "closed beyond for the last N candles (HOLDING)", "wick only, never closed beyond it (failed break)" or "closed beyond earlier, now back inside (failed break)".
- `last_candle` (`last_candle(candles)`): the latest candle's range, its ratio to the average range of the earlier candles today, its direction, and `impulse` — range ≥ `IMPULSE_MIN_PCT` (0.15%) of price and ≥ `IMPULSE_RATIO` (2×) the earlier average (the first candle needs only the 0.15%). Rendered as "Last candle (09:16): up, range 83 pts (0.37%), 1.5x the earlier average — IMPULSE candle." The Call 2 prompt never enters on the candle right after an impulse.

Also computed:
- **pools_taken**, and the nearest untaken pool ahead and behind for each side.
- **Opening type:** basket-average gap ≥ +0.15% is `gap_up`, ≤ −0.15% is `gap_down`, otherwise `flat`.
- **Rule A:** ride the PDH/PDL break when 2 of 3 indices broke the same way.

`describe_facts` renders the facts as plain words (no arithmetic left to do) for Call 2 and Jev.

## Call 2 prompt (`prompts.py`)

The system prompt states the teacher's behaviour as rules and keeps the study's statistics out (no clock anchors, no win rates for the model to quote):

1. Start from the plan side for today's opening type; keep two cases open (what confirms it, what would flip it).
2. Override the plan side only on CLOSES: a level closed beyond and held for ≥2 one-minute candles on 2 of the 3 indices.
3. Never enter on the candle right after an impulse; wait for the counter-move to stall, then enter WITH the larger move.
4. A wick through a level that closes back inside is a failed break.
5. WAIT is a normal answer and names the exact index / level / candle behaviour it waits for.
6. SKIP when neither case confirms by the deadline. One direction for the whole basket; exits are the system's.

Levels (prev close, PDH/PDL, round numbers, opening range, drawn levels) describe structure and targets. Opening OI flow does not exist before ~09:20 and its absence is not a signal.

Reply fields beyond decision/direction/confidence/thesis/rationale: `plan_side`, `following_plan`, `override_evidence` (required when not following the plan), `entry_type` (`after_counter_move | continuation | reversal`), `waiting_for` (required on WAIT), `pool_broken`, `legs`, `excluded_indices`, `next_pool_target`, `skip_reason_code`. The Call 1 plan returns, per opening type, the side and the counter-move wanted before entering, plus an `override_case`.

**Replay check (2026-10-15).** `scripts/intraday_hunter/v2_replay_*.py` re-ran Call 2 for Oct 9–15 with the previous prompt and this one, on the exact live inputs (candles, plan, OI flow, minute-log order flow), two runs each, priced through the grade's counterfactual basket (1 lot/leg): previous prompt −₹1.1K, this prompt +₹46.5K / +₹45.5K, live v2's own decisions −₹12.8K. It never entered at 09:16; it skipped Oct 14 (a day he won). Five days, and written knowing v2's Oct 12–14 failures — a design check, not out-of-sample evidence.

## Call 2 cadence (`watcher.py`, `decision.py`)

The NIFTY candle stamped 09:15 closes at ~09:16:00, so **decision time = candle minute + 1**.

- **Single-flight:** if a call is still in flight when the next minute arrives, that minute is skipped. The exception is the deadline: a deadline call then runs as soon as the in-flight call returns, so a day never ends on WAIT. The lazy background Call 1 is also single-flight per day.
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
- **Opening OI flow** (`oi_flow.py`): the per-index mean of ((ΔPE − ΔCE) / (CE+PE OI at 09:16)) from 09:16→09:19, nearest expiry. This reproduces the research `flow` column. Positive means CE. `fetch_oi_sums` filters on the raw `timestamp` range plus `expiry_date >= d`, so `idx_oi_snapshots_lookup` serves it (~6 ms); casting `timestamp` to IST date/time inside the WHERE clause would scan the symbols' whole history (~3 s per call).
- **Order flow** (`orderflow.py`): `fyers_ws_client` keeps `tot_buy_qty`, `tot_sell_qty`, `bid_size` and `ask_size` (Fyers SDK `map.json` names; verified on live NSE and BSE option ticks 2026-10-09).
  - They go into the Redis price cache.
  - Per-minute aggregates are computed for the index futures and **every captured contract** (the whole ATM±2 ladder). The minute log reads the captured strike nearest the live spot, which drifts off the capture-time ATM within minutes; tracking only that ATM left `atm_ce`/`atm_pe` null whenever spot sat nearer another strike (SENSEX for most of 2026-10-09). The minute log also re-registers the day's capture each minute, so a mid-day restart resumes order flow. The aggregates are: buy/sell imbalance, average spread, bid/ask size imbalance, tick-rule volume delta, and depth imbalance when `IH_V2_DEPTH_ENABLED` is set (depth is subscribed for the ATM CE/PE only). DepthUpdate messages are routed away from the price path.
- **`ih_minute_log`** (`minute_log.py`): one row per index per minute, 09:15–10:45, plus every minute while a v2 position is open. It is written from the NIFTY candle-close hook, after a ~4s settle so the other indices' candles persist.
  - `features`: OHLC, level facts, opening type, OI flow, order flow, VIX, the teacher's side for today's opening.
  - `arms`: `rule_a`, `plan_side`, `oi_flow_side`, `v1_state`, `v2_llm`, `gates` (for the v2 and rule_a sides), and `jev`.
  - It is failure-isolated and never raises into the feed.

## Teacher ingestion (`teacher/`)

- **Plan job** (21:00 / 23:00 the evening before, for the next trading day; then 00:00 / 06:00 / 08:00):
  - Finds "Prediction For <date>" on `@IntradayHunter/videos` with yt-dlp.
  - Fetches the Hindi auto-subs and grabs 30/60/90% keyframes with ffmpeg.
  - Claude (vision) returns `{gap_up_side, flat_side, gap_down_side ∈ CE|PE|none, bias, levels_onscreen, levels_audio, summary}`.
- **Live job** (15:45, retries 17:30 / 19:30 / 21:30, and a 07:30 next-morning catch-up for the previous trading day — all after market hours, so it never competes with the feed or monitor):
  - Finds the day's "Live Bank Nifty Option Trading" video by title plus IST `timestamp` date, else `upload_date`.
  - Downloads ≤720p (in practice the 360p progressive mp4, ~5 MB) and samples a frame every 5s, normalized to 1280×720. 360p is too small for tesseract, so OCR was dropped.
  - A cheap **PIL detector** finds the Kite positions screens: the table sits behind a large blue disclosure card, and real frames score about 0.68 vs 0 for charts. These frames are grouped into segments.
  - **Claude vision** reads about 14 representative frames, 6 per call: the taskbar clock (`decode_clock` handles 180°-rotated readings, so "81:60" → 09:18), open/closed state, legs, Total P&L, and the index prices in the tabs.
  - Entry = the earliest clock with positions open (the video opens with a teaser). Exit = the first all-closed clock after entry.
  - Each leg's qty/avg come from the first frame where *that* leg is open, since legs are often added over a few minutes, and are stored with a per-leg `entry_clock`. The leg P&Ls are sum-checked against the total.
  - **Validated on the real 2026-10-08 video:** PE basket (BN 54800/54700 PE, SENSEX 72500 PE, NIFTY 22550 PE), entry 09:19, exit 09:47, total +₹1,78,881.25, sum check OK.
- **YouTube access (`teacher/youtube.ytdlp_network_args`):** YouTube refuses the VM's datacenter IP outright — every player client gets "Sign in to confirm you're not a bot", subtitles included. In prod, yt-dlp goes through the **`st-warp` container** (Cloudflare WARP in proxy mode, `IH_YTDLP_PROXY=socks5h://warp:1080`, set in `docker-compose.prod.yml`). Behind the proxy the default player client is used: `web_embedded` (and mweb/ios/web_safari) return storyboard images only there ("Only images are available"), `tv` fails. Without a proxy (the Mac) the client stays `web_embedded`. `IH_YTDLP_PLAYER_CLIENT` overrides the client (`""` = yt-dlp default); `IH_YTDLP_COOKIES` adds `--cookies`. Every download stays inside yt-dlp: `--download-sections` hands the fetch to ffmpeg, which ignores `--proxy`.
- **Retries and alerts:** results go to `ih_teacher_days`. Every failure (video not found, blocked/429/403, parse failed, tool missing) is recorded on the day's `errors` with the job and error type, but only the **final** attempt Telegram-alerts it (plan: 08:00; live: the 07:30 catch-up, which also marks `LIVE_MISSING`). YouTube's bot check on the server's egress comes and goes — on 2026-10-15 WARP's IPv4 and IPv6 exits were both blocked from ~17:30 until ~22:45 for every video and player client, then cleared on their own — so the attempts are spread over hours rather than retried in place. A catch-up success re-grades that day (→ FINAL). The Mac fallback (`teacher_ingest_local.py` + POST `/teacher/ingest`) still works when every attempt is blocked; the plan jobs skip a day whose plan is already stored.
- **Mac fallback:** `scripts/intraday_hunter/teacher_ingest_local.py --date D [--plan] [--live] [--whisper] --push URL` runs the same pipeline locally and POSTs to `POST /api/v1/intraday-hunter/teacher/ingest` (body `{trading_date, plan, plan_video_id, live, live_video_id}`).
- **Runtime image:** ffmpeg (apt) and yt-dlp (pyproject floor). Locally, `IH_ALLOW_CLI_LOGIN=1` (dev only) lets `llm_cli` use the logged-in `claude` CLI when no `CLAUDE_CODE_OAUTH_TOKEN` is set. In prod the token is required, as before.

## Learning loop

1. **Nightly grade** (16:00; FINAL once the teacher's live trade lands) — `grading.grade_day` → `ih_day_grades`:
   - **market:** first-touch clean side from 09:16 at (+0.25/−0.20%) and (+0.30/−0.25%) per index, plus the 2-of-3 majority (same-candle tie = stop). Also the index move to 10:30 and to EOD.
   - **teacher:** side, entry/exit clocks, P&L, whether a PDH/PDL broke before his entry and whether he went with it.
   - **v1 / v2:** decision, side, decision time, real paper net P&L (YOLO and SHADOW), exit reasons, and the barrier label from their own entry.
   - **arms:** each arm's first side in the decision window, plus a **counterfactual basket P&L on the real captured premium candles**. The replay uses v2's basket shape, 1 lot per leg, and the same `evaluate_basket` rules: entry at the close of the candle before the decision, evaluated on each later candle close, LTP basis.
   - **skip_cost arm:** on a day v2 SKIPs and the teacher's plan has a side for today's opening, that side entered at the Call 2 deadline (`call2_deadline`), priced like the other arms. Positive = the skip left money on the table; negative = it saved money. Absent on days v2 entered. The ledger's `skip_cost` row counts skip days (`n`) and their mean cost — the check on whether the plan-side-first prompt skips too much.
   - **gates:** v2's P&L with each gate enforced.
   - Also fills **v1's `outcome_played_out`**: on ENTER, side == clean side; on SKIP, no clean side.
2. **Ledger** — `GET /v2/ledger`, per arm over the last 20 and 60 graded days: n, right-side %, win %, mean counterfactual P&L, t-stat, first-half vs second-half mean, and the gate what-ifs.
3. **Lessons** — each grade carries a compact lesson: `{date, opening, pools_broken, teacher, v2, arms_right, one_line_takeaway}`. The last `lessons_n` lessons feed v2's Call 1, replacing v1's empty `thesis_played_out` memory.
4. **Weekly review** (Saturday 10:00) — Claude reads the ledger and grades and proposes evidence-backed changes. These are stored in `ih_weekly_reviews` with a Telegram summary.
   - **Nothing is auto-applied.** Approved changes ship as a challenger variant on a new paper profile.
   - Promotion requires ≥20 trading days with both halves positive.
5. **Calibration** — once there are ≥30 v2 trades, an isotonic map from confidence to win rate is stored on the review row. It is recorded only, never used for sizing.

## Reliability alerts

- 09:34: Telegram if v1 or v2 has no ENTER/SKIP **status**. 09:34 rather than 09:30 so the check never races v1's 09:30 backstop call.
- A v2 ENTER that emits zero legs → status `ENTER_RETRY` plus a Telegram alert. Each later minute re-tries the emission (not a new LLM call) up to the deadline. Signal emission stops only on an **explicit** kill (`v2_active(fail_closed=False)`), so a transient DB error cannot drop a decided basket.
- Basket check failure → it runs inside a SAVEPOINT (rolled back, so it is never half-committed), Telegram once per day, and legs fall back to the 15:25 close.
- ATM±2 capture (09:10, 09:16:30): Telegram if the job crashes (`capture`) or if any index ends with **no** contracts (`capture_empty` — spot missing or strikes absent from the symbol master), since grading would then have no premium paths for it.
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

## Simulator E2E (wall-clock, MARKET_MODE=simulated)

The backend runs on wall-clock time, so the E2E runs live from 09:05 to 11:40 IST against the Market Simulator. The simulator rewrites `data/symbol_master/` with a **synthetic** master on every start, with option strikes ±20 around hardcoded index prices (`INDEX_SYMBOLS` / `BSE_INDEX_SYMBOLS` in `marketSimulator/app/engine/symbol_master_gen.py`: NIFTY 24500, BANKNIFTY 52000, SENSEX 80000). If the tape's prices fall outside that band, the ATM±2 capture resolves nothing (`capture_empty` alert).

- **Before the run:**
  1. Stage the previous trading day's index and `*_FUT` candles into the local DB.
  2. Start the simulator, then regenerate its symbol master around the tape's prev closes (patch the generator's base prices, then call `write_all()`). Delete the backend's Redis cache (`symbols:master`, `symbols:master:updated_at`) so the backend downloads the new master.
  3. Start the backend with `MARKET_MODE=simulated IH_ALLOW_CLI_LOGIN=1` before 08:45, so the scheduled Call 1 runs.
  4. Push the teacher plan via `POST /teacher/ingest`.
  5. Keep the Mac awake on AC power. On battery with the display off, macOS idle-sleeps and then returns to "Maintenance Sleep" after each DarkWake (`caffeinate -i` does not hold it), which freezes the stack and makes APScheduler skip jobs ("missed by …").
- **During the run:** `scripts/intraday_hunter/v2_sim_driver.py` injects the index tape (a gap through the PDL/PDH pool, then a ride) and model premiums for every captured contract.
- **What it checks:**
  - ATM±2 subscription and premium candles
  - 1-min `oi_snapshots` from 09:15 to 09:45
  - `ih_minute_log` rows every minute
  - Call 2 first at 09:16, with latency logged
  - IH-v2 YOLO and shadow positions opening together
  - the basket closing together
  - a forced `POST /v2/grade` producing an `ih_day_grades` row
  - `IH v2 ALERT` log lines (with `TELEGRAM_ENABLED=false`). To force one, pause the tape (stop the driver and `POST /sim/clock/pause`; injection alone stopping is not enough, as the simulator keeps ticking) for more than 10 min before a `candle_check_job` slot.

