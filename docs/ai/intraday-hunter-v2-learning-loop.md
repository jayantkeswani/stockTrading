# Intraday Hunter v2: Learning Loop and Change Approval

**Audience:** any Claude Code session asked to review, approve, refine or apply an IH v2 change.
Read this first, then `docs/ai/intraday-hunter-v2.md`, which describes the strategy, data and jobs.

**Status (2026-10-09):**
- The daily loop runs in production.
- The weekly proposal runs (first run Sat 2026-10-10).
- The approval UI and the apply agent in §4 are **not built yet**. Until they are, approvals follow the manual procedure in §5.

---

## 1. Principles (do not break these)

1. **Paper only.** No learning-loop change may touch real-money execution.
2. **Nothing is auto-applied.** Every change to how v2 trades is approved by the user.
3. **Changes run as challengers.** An approved change runs beside the current champion, never replacing it in place.
4. **Promotion needs evidence.** A challenger is promoted only after **≥ 20 trading days** with **both halves positive** on real paper P&L (BID_ASK fills), compared on the same days.
5. **v1 is the control group.** Never change v1's prompts, timing or exits as part of v2 learning.
6. **Judge on real premiums, not index direction.** Index direction ≠ option P&L (the Strategy 7 lesson).
7. **State n and t.** Small samples are labelled as such. A single day is never evidence.

## 2. The daily loop (automatic)

| Time (IST) | Job | Writes | Feeds |
|---|---|---|---|
| 00:00 / 06:00 / 08:00 | Teacher plan pull (YouTube "Prediction For") | `ih_teacher_days.plan` | Call 1, `plan_side` arm, plan gate |
| 08:45 | v2 Call 1 (thesis) | `intraday_hunter_runs` (variant `v2`).call1_json | Call 2 |
| 09:15–10:45, plus while in position | Minute logger | `ih_minute_log` (features + arms) | Grading, ledger |
| 09:16–09:25 | v2 Call 2 (decision, basket entry) | `call2_history`, signals, trades | Grading |
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

## 4. Approval workflow (TARGET DESIGN, not built yet)

**UI (IH v2 tab, plus phone):** a "Weekly review" card lists each proposal with:
- its evidence (n, t), expected effect and risk;
- **Approve** / **Reject** buttons, with an optional note;
- the status chain: `PROPOSED → APPROVED | REJECTED → ANALYSED → APPLIED (challenger live) → PROMOTED | RETIRED`.

**On Approve, an apply agent runs** (Claude via the CLI, Opus):
1. **Re-checks the evidence** against the latest ledger. If it no longer holds, the agent marks the proposal `NEEDS_REVIEW` with its reasoning and does not apply it.
2. **Classifies the change and turns it into a concrete apply plan:**
   - **param / gate** (e.g. `basket_t_mode`, `rupees_per_lot`, `basket_time_exit`, enforcing a gate): a params override applied to a **challenger variant**. Param-only challengers are first scored **counterfactually** in the nightly grade, on the captured premium data, at no extra cost and with no live risk. They are shown in the ledger as a new arm.
   - **prompt:** a challenger v2 variant with the modified prompt, on its own paper profile (e.g. `IH-v2-c1`). It costs one extra Call 2 per minute in the 09:16–09:25 window.
   - **code** (anything needing new logic): no auto-apply. The agent writes a build brief, and a session implements it via a normal PR.
3. **Records** the apply plan, the challenger id and the start date on the review row, then sends a Telegram confirmation.

**Promotion and retirement:** the ledger compares the challenger with the champion on the same days. After ≥ 20 trading days with both halves positive, the UI offers **Promote** (the user clicks). A challenger that is behind after 20 days is offered **Retire**.

## 5. Manual procedure (until §4 is built)

For a session helping the user with a proposal:
1. **Read the proposal:** `GET /api/v1/intraday-hunter/v2/reviews?limit=1`. Also read `GET /api/v1/intraday-hunter/v2/ledger` and recent `/v2/grades`.
2. **Check it against §1.** Is n large enough? Is t meaningful? Is the effect positive in both halves? Is it param, prompt or code?
3. **Get the user's explicit approval** before changing anything.
4. **Apply a param change:**
   - Read the current params from `GET /api/v1/strategies`.
   - Merge only the changed keys.
   - `PUT /api/v1/strategies/intraday_hunter_v2` with `{"parameters": {...full merged dict...}}`. The PUT replaces the whole dict, so never send a partial one.
   - Effect is immediate; the cache is cleared.
   - The kill switch is `is_active` on the same row.
5. **Code or prompt changes** go through a feature branch + PR (follow `CLAUDE.md`: docs ship with code, tests green, no deploys during market hours).
6. **Record what changed and why** (date, proposal, evidence, n/t) in the **Change log** below.

## 6. Change log (applied changes)

| Date | Change | Evidence | Approved by |
|---|---|---|---|
| 2026-10-09 | `basket_tp_sl_pct` 0.20 → 0.15; `round_hold_enabled` → false | Teacher exit study, 72 days: rupee-amount 1:1 exits (median win ₹2.54L / loss ₹2.63L), exits at 97% of peak, no round-number waiting. Study in `~/Downloads/ih_research/exit_study/FINDINGS.md` | User (overnight go-ahead) |
| 2026-10-09 | Jev shadow arm enabled in prod (`deploy.yml`) | Live API check: HTTP 200, ~600ms | User |
| 2026-10-09 | `basket_t_mode` → `rupees` (planned right after the v1.34.2 deploy) | Same exit study | User |
