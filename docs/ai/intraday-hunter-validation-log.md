# Intraday Hunter Agent — Validation Log (session memory)

**Status:** **BUILT (2026-06-24)** — the live feature (backend services + autorun + API + `/intraday-hunter`
page) is implemented; variant C is the baseline (`build_system_prompt("C")`). The two validation gates
(non-determinism, real-option P&L) are DEFERRED to live paper — every Call 2 is logged to
`intraday_hunter_runs.call2_history` for scoring. This doc + `intraday-hunter-agent.md` (As-built notes)
are the continuity reference. The IMPLEMENTATION CHECKLIST below is DONE (kept for history).
**Last updated:** 2026-06-24

## ⭐ RESUME HERE — DECISION: go straight to IMPLEMENTATION
- **Adopt variant C** (validated; beat A across bull+bear — see "C RESULT"). Use `build_system_prompt("C")`
  everywhere in the backend. A/B/C live in `prompts.py`; A=`f443449` revert point, C=`57b8347`.
- **DECISION (2026-06-24): build the backend + UI now (design-spec), MANUAL-alert only.**
  The two validation gates are **DEFERRED to live paper-trading**, not done offline:
  1. Non-determinism — observe decision stability live; add majority-vote later only if it's a problem.
  2. Real option P&L — measured live from the paper book (the page logs every suggestion); index
     direction ≠ option win-rate (the S7 trap) — so do NOT put capital behind it until the paper book
     confirms real-premium profitability. Ship MANUAL-alert (suggestion only), never YOLO.
- **Build order** (from `intraday-hunter-agent.md` §file map): table+migration → thesis.py (Call 1) →
  decision.py (Call 2) → watcher.py → autorun (08:45 task + candle-close hook) → API → /intraday-hunter
  page → validation logging → docs registries → simulated-mode E2E. Reuse the existing pure modules
  (prompts/charts/context/llm_cli) — they're done and validated.

## IMPLEMENTATION CHECKLIST (new session: recreate this as your own TaskList via TaskCreate)
Task numbers from the prior session were ephemeral; this is the canonical, durable list. Build in order;
reuse the DONE pure modules (prompts.py, charts.py, context.py, llm_cli.py — validated, variant C is final).
1. `intraday_hunter_runs` table + Alembic migration (schema in `intraday-hunter-agent.md` §Data model).
2. `thesis.py` — Call 1 (~08:45): build context (per-index prev-day structure, multi-day structural memory
   from prior run rows, India VIX, calendar/expiry) → render prev-day charts → call Claude (variant C) → persist.
3. `decision.py` — Call 2: live open/gap/first-candles + prior decisions → opening chart → call Claude →
   parse full-plan JSON → persist (append call2_history).
4. `watcher.py` — 09:18 start, WAIT→recheck 1–2 min capped 09:30, prior decisions fed back, single-direction basket.
5. Autorun — 08:45 scheduled task + hook the watcher into the existing 1m candle-close loop (no cron; respect
   simulated-mode + market-open gates).
6. API — GET /today, GET /history, POST /run-call1, POST /run-call2, GET /chart/{run_id}/{which} + schemas +
   frontend api.ts/types.ts.
7. `/intraday-hunter` Next.js page + components (Today view: status chip, Call 1 thesis, Call 2 decision with
   confidence/basket/excluded/rationale/charts; + history timeline). Polled REST ~15–30s. Sidebar nav link.
8. Validation logging — every Call 2 persisted; outcome scored later from the live paper book.
9. Docs (MANDATORY) — backend/CLAUDE.md + frontend/CLAUDE.md + ARCHITECTURE.md registries; flip
   `intraday-hunter-agent.md` status DESIGN→BUILT.
10. End-to-end test in simulated mode (local DB 5433 + market simulator). `make test`.

## ⚠ PRODUCTION PREREQUISITES (do NOT forget — the LLM call won't work in prod without these)
- The `claude` CLI must be **installed in the production backend container/VM** (it's only on the dev
  machine today). Add it to the backend Docker image build (`infrastructure/docker/`) — pull the binary
  from github.com/anthropics or `npm i -g @anthropic-ai/claude-code` in the image.
- `CLAUDE_CODE_OAUTH_TOKEN` must be present as a **container env var in prod**, sourced from a **GitHub
  Actions secret** (the deploy pipeline injects it). Generate via `claude setup-token` on a browser
  machine; it draws on the Pro/Max subscription (not API credits). Rotate the dev token in
  `scripts/claude_oauth_test.py` (gitignored) — never ship it.
- `llm_cli.py` already reads `CLAUDE_CODE_OAUTH_TOKEN` from env and drops `ANTHROPIC_API_KEY`. mplfinance
  must also be in the image (added to `backend/pyproject.toml`? — verify; it was pip-installed in dev).
- Deploy BEFORE market open (no backend restarts during 9:15–15:30 IST market hours).

## C RESULT (2026-06-20) — the 2 fixes worked, adopt C
Graded by "right side" = the side that actually paid (from his verified result), 2-hour hold window.
- **Bear (9 down days, Mar 23–30 + Mar 6–13 + May 8–12):** A bought PUT 2 / skip 6 / wrong-CALL 1  →
  **C bought PUT 4 / skip 3 / wrong-CALL 2.** Gap-down→PE-continuation fix confirmed (Mar 30 skip→PUT✓).
- **Bull (June 1–19):** A right-side 4 / wrong 1 / skip 10  →  **C right-side 7 / wrong 1 / skip 7**, and
  C still **correctly skipped the genuine chop/loss days (Jun 2, 16)** AND took the **correct contrarian
  PUT on Jun 19** (where the human LOST buying CALL). Discipline held.
- **Combined: A 8 entries (6 right/2 wrong, 75%) → C 14 entries (11 right/3 wrong, 79%).** ~2× the correct
  trades at the same accuracy, both directions, no erosion of the chop-skip.
- Caveats unchanged: single C run (non-determinism), index-direction not option P&L, his self-reported size.

## What exists (code)
- `backend/app/services/intraday_hunter/`: `prompts.py` (system prompt + 7 few-shot exemplars +
  Call 1/Call 2 templates + JSON schemas + variant addenda), `charts.py` (mplfinance prev-day +
  opening charts), `llm_cli.py` (Claude CLI wrapper — **inline base64 images, stream-json, single
  turn**), `context.py` (pure context builders: prev-day structure, gap, VIX→expected range).
- `scripts/intraday_hunter/prototype_agent.py` (single-day prompt-review tool),
  `scripts/intraday_hunter/simulate.py` (day-by-day backtester + grader).
- Branch: `research/intraday-hunter-validation`. Token: `scripts/claude_oauth_test.py` (gitignored).

## Architecture decisions locked this session
- **Model: `claude-opus-4-8`** via the `claude` CLI on the subscription OAuth token (not API credits).
- **Inline base64 images = the latency fix.** Read-tool round-trips took ~20 min/call (6 images);
  inline single-turn = ~40s. Recipe in `llm_cli.py` (`--input-format stream-json`, piped stdin,
  `--allowed-tools ""`). Opus chosen (faster here than Sonnet + preferred).
- **No option-selling** — pure directional buyer (CE/PE or SKIP). (Selling helped the June backtest
  but diverges from his pure-directional style; removed per user.)
- **Watcher:** Call 1 ~08:45 thesis; Call 2 from 09:18, WAIT→recheck 1–2 min capped 09:30, prior
  decisions fed back. Single-direction basket. Two-call flow.
- **Grading:** he holds ~2 hours → grade on the **09:15+→+2h window MFE**, NOT whole-day o→c.
  "Right side" = the side that actually paid (from his verified result).

## His verified results (read off his Zerodha screen in other Claude sessions)
- **May 15–30:** +₹12.4L, 7W/3L (70%). Loss days: May 19/20/27.
- **June 1–19:** +₹29.5L, 12W/3L (80%). Loss days: Jun 2/16/19.
- Two-month: 19W/6L (76%). **Caveats:** huge non-retail size (~₹50L–1Cr margin); screen P&L, can't
  tell funded vs demo; losses ≈ wins in size and cluster (May 19+20 = −6.6L) — edge is hit-rate, not
  loss control. Direction is the transferable signal, NOT the ₹ magnitude.

## Agent backtest findings (Opus, no-sell)
- **Over-conservative (the one robust, regime-independent flaw):** enters ~1 of 3 tradable days;
  in May+June caught only ~2–5 of his ~7–12 winners per window; in bear windows caught 2 of 9 down days.
- **Direction is GOOD when it commits (~70–80% right side), BOTH ways.** This differentiates it from
  S2/S5/S6/S7 (mechanical signal + LLM confidence overlay — confidence came out **inverted/uninformative**
  on real validation; no directional signal). Here the LLM *reads the chart and decides* — a different
  modality that produced a real two-way signal.
- **Not bull-biased.** May/June it entered 22×, all CALL — but that was the bull regime offering CALL
  setups, not inability: the **bear test proved it buys PUT** (Mar 13 + Mar 27, both right, 2/2) and
  otherwise *skips* down days rather than forcing CALLs. So it's "over-cautious but directionally sound,"
  not a CALL reflex.
- **Specific blind spot:** on a gap-down it only hunts a CE *reversal*; when the reclaim fails it SKIPS
  instead of buying **PE continuation** (missed Mar 23, Mar 30 — both gap-downs).
- **⚠ NON-DETERMINISM (big unsolved issue):** the *identical* prompt re-run gave **3 vs 7 entries** on the
  same May days. Opus is stochastic; `temperature` is removed on 4.8 so it can't be forced. Run-to-run
  variance is LARGER than the A-vs-B prompt difference → single runs are not reproducible. Candidate
  mitigation: majority-vote N=3/5 per day (3–5× calls).

## Bear-test result (9 down days, 3 windows: Mar 23–30, Mar 6–13, May 8–12)
bought PUT (right) **2** · skipped **6** · bought CALL (wrong) **1**. → real but **low-frequency** two-way
skill; the misses are over-conservatism + the gap-down/PE-continuation blind spot.

## Verdict going in to the fix
Engine is NOT the dead-end the overlay strategies were (it has a real two-way direction signal), but the
edge is **thin (low frequency) + non-deterministic**, so "real but not yet bankable." Fix the two specific,
evidence-backed gaps, then re-validate; also still need real-option-P&L (not index direction) + a
non-determinism plan before trusting it.

## The 2 changes under test (kept as a NEW variant; baseline A preserved for revert)
1. **Cut over-conservatism** — take a clean directional morning (UP→CALL or DOWN→PUT) on the gap+thesis OR
   the first ~5–6 candles; SKIP only genuine two-sided chop.
2. **Gap-down → PE-continuation path** — if a gap-down fails to reclaim and keeps making new lows, BUY PUT
   (ride the breakdown); CE-reversal only if the low holds/reclaims.
Implemented as prompt variant **C** (= A core + few-shot + a FIX addendum). Baseline **A** is unchanged so
we can compare/revert. Run: `simulate.py --variant C --tag C ...`.

## Reproduce / data / caches
- Data: PROD index+VIX 1m copied to local for May 15–Jun 19 (idempotent staging-table load). All 25 days
  verified to have full 09:15–09:30 windows.
- Per-day decision caches: `/tmp/ih_sim_<TAG>/<date>_claude-opus-4-8.json` (A, B, BEAR, BEAR2, BEAR3, C…).
- Run a window: `DATABASE_URL=…local python scripts/intraday_hunter/simulate.py --variant A --start … --end … [--refresh] [--tag …]`.
- ⚠ Don't run two variants in parallel — it trips the **subscription 5-hour rate limit** (failed days cache
  as "call failed -> SKIP"; re-run with `--refresh`).

## Open decisions
- Non-determinism: majority-vote, or accept variance?
- Validate on **real option P&L** (theta + tight stop — killed S7), not index direction.
- Build the backend now vs. more validation. Current lean: finish the fix+re-validate, then decide.
