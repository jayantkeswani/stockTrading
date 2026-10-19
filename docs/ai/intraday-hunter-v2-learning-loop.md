# Intraday Hunter v2: Learning Loop and Change Approval

**Audience:** any Claude Code session asked to review, approve, refine or apply an IH v2 change.
Read this first, then `docs/ai/intraday-hunter-v2.md`, which describes the strategy, data and jobs.

**Status:**
- The daily loop runs in production.
- The weekly proposal runs every Saturday 10:00 (first run Sat 2026-10-10).
- The approval flow in §4 is built: a per-proposal "Weekly review" card on the IH v2 tab (desktop + phone), the apply agent, challengers scored in the nightly grade, and Promote / Retire.

---

## 1. Principles (do not break these)

1. **Paper only.** No learning-loop change may touch real-money execution.
2. **Nothing is auto-applied.** Every change to how v2 trades is approved by the user.
3. **Changes run as challengers.** An approved change runs beside the current champion, never replacing it in place.
4. **Promotion needs evidence.** A challenger is promoted only after **≥ 20 trading days** with **both halves positive** on real paper P&L (BID_ASK fills), compared on the same days.
5. **v1 is the control group.** Never change v1's prompts, timing or exits as part of v2 learning. Changes to v1 itself are made only on the user's explicit decision, outside the learning loop, and reset the v1-vs-v2 comparison from their effective date (recorded in §6). The last reset: 2026-10-16 (v1 basket exits + both BANKNIFTY legs).
6. **Judge on real premiums, not index direction.** Index direction ≠ option P&L (the Strategy 7 lesson).
7. **State n and t.** Small samples are labelled as such. A single day is never evidence.

## 2. The daily loop (automatic)

| Time (IST) | Job | Writes | Feeds |
|---|---|---|---|
| 00:00 / 06:00 / 08:00 | Teacher plan pull (YouTube "Prediction For") | `ih_teacher_days.plan` | Call 1, `plan_side` arm, plan gate |
| 08:45 | v2 Call 1 (thesis) | `intraday_hunter_runs` (variant `v2`).call1_json | Call 2 |
| 09:15–10:45, plus while in position | Minute logger | `ih_minute_log` (features + arms) | Grading, ledger |
| 09:16–09:45 | v2 Call 2 (decision, basket entry) | `call2_history`, signals, trades | Grading |
| 15:45 / 17:30 | Teacher live pull (his side, entry/exit clocks, P&L) | `ih_teacher_days.live` | Grading |
| 16:00 | **Grade the day** (`grading.grade_day`) | `ih_day_grades` (+ lesson) | Ledger, Call 1 memory |

**Arms** are logged every minute, whether or not anything trades:
- `rule_a`: ride a PDH/PDL break on ≥2 of 3 indices
- `plan_side`
- `oi_flow_side`
- `v1_state`
- `v2_llm`
- `gates`: plan / OI verdicts, shadow-only
- `jev`: OpenRouter `typesafe/jev-1.13`, shadow-only

**The grade** records:
- the market's clean side, by index first-touch at several barrier sets;
- the teacher's side and P&L;
- v1's and v2's real paper P&L;
- each arm's **counterfactual basket P&L**, replayed on the captured ATM±2 premium candles with v2's basket exit rules;
- v2's P&L with each gate enforced.

**The lesson** (`grading.build_lesson`, a deterministic template) is a compact record: opening, pools broken, teacher side/result, v2 side/result, arms that were right, and a one-line takeaway. v2's Call 1 receives the last `lessons_n` (8) lessons.

## 3. The weekly proposal (Saturday 10:00)

`review.run_weekly_review`:
- **Reads:** the arm ledger (20 and 60 days: n, right-side %, mean counterfactual P&L, t, first vs second half), the last 10 grades and the current params.
- **Writes:** one `ih_weekly_reviews` row per week with `status = PROPOSED`.
- **Notifies:** a Telegram summary.
- **Read it via:** `GET /api/v1/intraday-hunter/v2/reviews`.

Proposal schema (`review.PROPOSAL_SCHEMA`):

```json
{ "summary": "...",
  "proposals": [{ "change": "...", "kind": "param | prompt | gate",
                  "evidence": "n, t, ledger numbers", "expected_effect": "...", "risk": "..." }],
  "challenger_variant": "...", "do_not_change": "..." }
```

Calibration (an isotonic confidence → win-rate map) is stored once there are ≥ 30 v2 trades. It is not used for sizing.

## 4. Approval workflow

Code: `backend/app/services/intraday_hunter_v2/apply.py` (lifecycle, agent, challengers), table `ih_v2_proposals` (one row per proposal), UI `frontend/src/components/intraday-hunter/v2/WeeklyReviewCard.tsx` (IH v2 tab and the phone's Hunter → v2 view).

**Status chain, per proposal (not per week):**

```
PROPOSED ──Approve──▶ APPROVED ──apply agent──▶ ANALYSED ──▶ APPLIED ──Promote──▶ PROMOTED
   └──Reject──▶ REJECTED        └──▶ NEEDS_REVIEW            (code: stops at     └──Retire───▶ RETIRED
                                     (Re-analyse / Reject)    ANALYSED + brief)
```

Every transition is appended to the row's `history` (status, time, note). The UI shows only the buttons the backend allows for the current status (`apply.allowed_actions`); each takes an optional note. Approve, Promote and Retire have an inline Confirm step.

**Weekly review** (`review.run_weekly_review`, Sat 10:00) stores the review and one `ih_v2_proposals` row per proposal (`PROPOSED`). Re-running the same week replaces only rows still `PROPOSED`.

**On Approve, the apply agent runs in the background** (`apply.run_apply_agent`; Claude via `llm_cli`, Opus 5.5; ~30–90s; Telegram confirmation when done):
1. **Re-checks the evidence** against the latest ledger (n, t, halves). If it no longer holds → `NEEDS_REVIEW` with its reasoning; nothing applied. **Re-analyse** re-runs the agent (also the recovery for a proposal left `APPROVED` by a restart).
2. **Classifies and plans** (`kind`, `params_override`, `prompt_addendum`, `build_brief`, `reasoning`, `recheck`). A deterministic check (`apply.validate_plan`) then decides:
   - **param / gate → `counterfactual` challenger** when every override key is in `params.COUNTERFACTUAL_KEYS` (exits, basket T, round-hold, leg structure, enforced gates). The nightly grade re-scores **v2's own decision** with the override on the captured ATM±2 premiums (an enforced gate that would have blocked v2 → no trade). No LLM cost, no live risk.
   - **prompt (or a Call 2 input such as timing/model) → `shadow_call2` challenger.** The prompt text goes in the `call2_prompt_addendum` param. `watcher.challenger_watcher` runs the challenger's **own Call 2** each minute on the champion's cadence (run rows variant `v2c1`…, the champion's Call 1, the challenger's params) and **never emits signals**. The grade scores its decision on the captured premiums with its params. Cost: one extra Call 2 per minute in the 09:16–09:30 window.
   - **code → no challenger.** The plan carries a build brief (status stays `ANALYSED`); a session implements it via a normal PR.
   - Refused (→ `NEEDS_REVIEW`): unknown keys, wrong value types, keys that shape the shared data pipeline (`apply.NON_CHALLENGER_KEYS`: minute-log window, capture width, OI window, grade barriers, lessons, Call 1 model), keys that are inert under the merged champion+override params (`apply.inert_keys`: `basket_tp_sl_pct` when the effective `basket_t_mode` is `rupees`, `rupees_per_lot` when it is `pct`, `round_hold_*` tunables when the effective `round_hold_enabled` is false — the reason names them), or an override identical to the champion.
3. **Applies:** challenger id `c<n>`, mode, override and `started_on` (the next trading day) are stored on the row → `APPLIED`.

**Scoring.** Each live challenger is ledger arm **`ch_<id>`** in every nightly grade. Both challenger modes and the champion (`v2_llm`) are scored the same way — counterfactual basket P&L on real captured premiums, 0 on days without a trade — so the comparison is like for like on the same days. Live paper execution of a prompt challenger on its own YOLO profile is **not built** (it needs execution isolation: own strategy routing, shadow/dedup/basket grouping); counterfactual scoring is used instead.

**Promote / Retire** (`apply.challenger_stats`): unlock after **≥ 20 graded trading days** since `started_on`.
- **Promote** is offered only when the challenger is ahead of the champion in **both halves** of those days. The click merges `params_override` into the champion's `strategy_configs.parameters` (live from the next poll; the previous values are kept in the history entry) → `PROMOTED`.
- **Retire** is offered when it is not ahead in both halves → `RETIRED` (the challenger stops; its past arms stay in old grades).

**API** (`/api/v1/intraday-hunter`): `GET /v2/proposals` (status, plan, challenger `stats`, allowed `actions`) and `POST /v2/proposals/{id}/{approve|reject|analyse|promote|retire}` with `{note?}` (409 when not allowed).

## 5. Procedure for a session helping the user

1. **Read:** `GET /api/v1/intraday-hunter/v2/proposals`, `GET /v2/ledger` and recent `/v2/grades`.
2. **Check against §1:** is n large enough, t meaningful, both halves positive? Which mode would it run as?
3. **The user clicks** Approve / Reject / Promote / Retire on the IH v2 tab (or explicitly asks you to call the same endpoint). Never act without that.
4. **A `code` proposal** (ANALYSED with a build brief) goes through a feature branch + PR (follow `CLAUDE.md`: docs ship with code, tests green, no deploys during market hours).
5. **A change outside the weekly flow** (e.g. the user asks for a direct param change): read the current params from `GET /api/v1/strategies`, merge only the changed keys, `PUT /api/v1/strategies/intraday_hunter_v2` with the **full** merged `parameters` dict (the PUT replaces the whole dict). The kill switch is `is_active` on the same row.
6. **Record** what changed and why (date, change, evidence, n/t, approver) in the **Change log** below. Promotions through the UI are also recorded on the proposal's `history`.

## 6. Change log (applied changes)

| Date | Change | Evidence | Approved by |
|---|---|---|---|
| 2026-10-09 | `basket_tp_sl_pct` 0.20 → 0.15; `round_hold_enabled` → false | Teacher exit study, 72 days: rupee-amount 1:1 exits (median win ₹2.54L / loss ₹2.63L), exits at 97% of peak, no round-number waiting. Study in `~/Downloads/ih_research/exit_study/FINDINGS.md` | User (overnight go-ahead) |
| 2026-10-09 | Jev shadow arm enabled in prod (`deploy.yml`) | Live API check: HTTP 200, ~600ms | User |
| 2026-10-09 | `basket_t_mode` → `rupees` (`rupees_per_lot` BN 3915 / NIFTY 1640 / SENSEX 862), applied ~15:50 IST after the v1.34.2 deploy as a direct PUT on the champion params (predates the approval flow) | Same exit study | User |
| 2026-10-15 | Champion Call 1/Call 2 prompts rewritten in place (plan side first; never enter on the candle after an impulse; override only on closes, 2+ candles on 2 of 3 indices; WAIT names its trigger; stop-hunting demoted to vocabulary); level facts gain closed-beyond vs wick-only and the impulse flag; new grade arm `skip_cost` (plan side at the deadline on v2 SKIP days) to measure what skipping costs; `call2_deadline` 09:25 → 09:30 (migration `e2a7c5d9f310`). Same strategy, profile and run rows — no challenger | 72-day entry-timing + blind-coded narration study (`~/Downloads/ih_research/psych_study/FINDINGS.md`); 5-day replay Oct 9–15 (+₹46K vs −₹1K previous prompt, 1-lot grade basket) — a design check, not out-of-sample | User |
| 2026-10-16 | **v1 (control) reset.** v1 YOLO + SHADOW exit as a basket (MTM ±T, v1's own `rupees_per_lot` copy BN 3915 / NIFTY 1640 / SENSEX 862, 11:30 backstop, no per-leg exits) instead of per-leg ±60% premium SL/target + 15:25; v1 dedup made strike-aware so both BANKNIFTY legs (ITM-2 + ITM-1) trade — before this, the ITM-1 leg was folded into the ITM-2 signal on every v1 ENTER day since 07-02 (3 legs, BN 2 lots); the per-leg card's multi-select close replaced by "Close v1 basket". **Split every v1 / v1-vs-v2 comparison on this date.** | Per-leg exits did not match the teacher's all-in/all-out basket; 65 of 101 v1 YOLO legs were hand-closed (MANUAL), so the "control" was mostly discretion | User (2026-10-15 session: convert YOLO + SHADOW, rupee T, no per-leg exits, 11:30, basket button, fix the BN leg) |
| 2026-10-19 | `call2_deadline` 09:30 → 09:45 (migration `f5b2d8c1a946`), decision check 09:34 → 09:49; Call 2 prompt: an override must also meet Call 1's `override_case`, and the deadline line no longer reads as "act now" (a skip costs nothing; the deadline is never a reason to enter). Same strategy, profile and run rows — no challenger | 3 of the teacher's 6 live entries since v2 went live came after 09:30 (09:32, 09:33, 09:41). On 2026-10-16 v2 waited 14 checks for the CE plan side, then flipped to PE at the 09:30 deadline citing "after 09:30 a WAIT becomes a SKIP", with its own Call 1 override case unmet (−₹19.6K; teacher CE at 09:41, +₹4.5L). One day — a design fix, not out-of-sample | User |
