"""Weekly-review approval flow — per-proposal lifecycle, the apply agent, challengers, promotion.

Nothing changes how v2 trades without the user's click (docs/ai/intraday-hunter-v2-learning-loop.md
§1, §4). Per proposal (`IhV2Proposal`):

  PROPOSED ──approve──▶ APPROVED ──apply agent──▶ ANALYSED ──▶ APPLIED ──promote──▶ PROMOTED
     │                     ▲          │              (code: stops here,        └──retire───▶ RETIRED
     └──reject──▶ REJECTED │          └──▶ NEEDS_REVIEW  build brief only)
                           └── re-analyse ──┘

The apply agent (Claude via `llm_cli`, Opus) re-checks the proposal's evidence against the latest
ledger and turns it into a concrete plan; `validate_plan` (deterministic) then decides whether it
can run and how:

- **counterfactual** challenger — every override key is in `params.COUNTERFACTUAL_KEYS` (exits,
  basket T, leg structure, enforced gates): grading re-scores v2's OWN decision with the override
  on the captured premiums. No LLM cost, no live risk.
- **shadow_call2** challenger — the override changes Call 2's inputs or prompt
  (`call2_prompt_addendum`, timing, model…): the challenger watcher runs its OWN Call 2 each minute
  (run rows `v2{challenger_id}`, champion's Call 1, never emits signals) and grading scores its
  decision the same way.
- **code** — anything needing new logic: no challenger; the plan carries a build brief.

Both challenger kinds appear in the ledger as arm `ch_{challenger_id}`. Promote / Retire unlock
after ≥ PROMOTION_MIN_DAYS graded trading days of challenger vs champion (`v2_llm` counterfactual)
on the same days; Promote also requires the challenger to be ahead in BOTH halves. Promote merges
`params_override` into the champion's `strategy_configs.parameters` — only on the user's click.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import is_trading_day, now_ist
from app.models.ih_v2 import IhDayGrade, IhV2Proposal, IhWeeklyReview
from app.services.intraday_hunter import llm_cli
from app.services.intraday_hunter_v2.params import (
    COUNTERFACTUAL_KEYS,
    INTRADAY_HUNTER_V2_DEFAULTS,
    STRATEGY,
    v2_params_async,
)

logger = logging.getLogger(__name__)

PROMOTION_MIN_DAYS = 20
KINDS = ("param", "gate", "prompt", "code")
# Keys that shape the SHARED data pipeline / the champion's Call 1, not a decision — a challenger
# can't carry them (they'd change the data both books are judged on). Needs a code change instead.
NON_CHALLENGER_KEYS = frozenset({
    "minute_log_start", "minute_log_end", "capture_strikes_each_side", "oi_hf_start", "oi_hf_end",
    "grade_barriers", "lessons_n", "gate_shadow_days", "call1_model", "ai_overlay_enabled",
})
LIVE_STATUSES = ("APPLIED",)
CLOSED_CHALLENGER = ("PROMOTED", "RETIRED")

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "approve": ("PROPOSED",),
    "reject": ("PROPOSED", "NEEDS_REVIEW"),
    "analyse": ("APPROVED", "NEEDS_REVIEW"),
    "promote": ("APPLIED",),
    "retire": ("APPLIED",),
}

APPLY_SCHEMA = {
    "evidence_holds": "true | false — does the proposal's evidence still hold on the latest ledger?",
    "recheck": "the current n, t and halves for the arm(s) the proposal relies on",
    "kind": "param | gate | prompt | code",
    "params_override": {"<v2 param key>": "<new value>"},
    "prompt_addendum": "text appended to v2's Call 2 system prompt (prompt changes only, else \"\")",
    "build_brief": "code changes only: what to build, where, and how to test it (else \"\")",
    "reasoning": "why this plan, and what would make you stop the challenger",
}


class TransitionError(ValueError):
    """The requested action is not allowed from the proposal's current status."""


# ───────────────────────────── pure helpers ─────────────────────────────

def _log(p: IhV2Proposal, status: str, note: str | None = None, **extra) -> None:
    """Set the status and append a history entry (reassigns the JSONB list so it persists)."""
    p.status = status
    entry = {"status": status, "at": now_ist().isoformat(timespec="seconds")}
    if note:
        entry["note"] = note
    entry.update({k: v for k, v in extra.items() if v is not None})
    p.history = [*(p.history or []), entry]


def check_transition(p: IhV2Proposal, action: str) -> None:
    """Raise TransitionError unless `action` is allowed from `p.status`. Pure."""
    if p.status not in TRANSITIONS[action]:
        raise TransitionError(f"cannot {action} a proposal in status {p.status}")


def allowed_actions(status: str, stats: dict | None, agent_busy: bool = False) -> list[str]:
    """Buttons the UI may show for a proposal right now. Pure.

    approve/reject on PROPOSED; analyse (re-run) + reject on NEEDS_REVIEW; analyse on an
    APPROVED proposal whose agent is NOT running (left stuck by a restart); nothing while the
    agent runs; promote/retire on APPLIED only once the challenger stats allow them.
    """
    if agent_busy:
        return []
    out = [a for a in ("approve", "reject", "analyse") if status in TRANSITIONS[a]]
    if status == "APPLIED" and stats:
        if stats.get("can_promote"):
            out.append("promote")
        if stats.get("can_retire"):
            out.append("retire")
    return out


def _type_ok(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, dict):
        return isinstance(value, dict)
    if isinstance(default, list):
        return isinstance(value, list)
    return value is None or isinstance(value, str)  # None defaults: model names


def inert_keys(override: dict, champion: dict) -> list[str]:
    """Override keys that have no effect under the merged champion+override params. Pure.

    basket_tp_sl_pct is inert when the effective basket_t_mode is "rupees"; rupees_per_lot
    when it is "pct"; any round_hold_* key (other than round_hold_enabled) when the effective
    round_hold_enabled is false. Missing keys fall back to the v2 defaults.
    """
    eff = {**INTRADAY_HUNTER_V2_DEFAULTS, **champion, **override}
    inert = []
    if "basket_tp_sl_pct" in override and eff.get("basket_t_mode") == "rupees":
        inert.append("basket_tp_sl_pct")
    if "rupees_per_lot" in override and eff.get("basket_t_mode") == "pct":
        inert.append("rupees_per_lot")
    if not eff.get("round_hold_enabled"):
        inert += [k for k in override if k.startswith("round_hold_") and k != "round_hold_enabled"]
    return sorted(inert)


def validate_plan(plan: dict | None, champion: dict) -> dict:
    """Deterministic gate on the apply agent's plan. Pure.

    Returns {ok, status, mode, override, reason}: `status` is the proposal's next status
    (ANALYSED for an accepted plan, NEEDS_REVIEW otherwise); `mode` is "counterfactual" |
    "shadow_call2" | None (code); `override` the validated params override.
    """
    def fail(reason: str) -> dict:
        return {"ok": False, "status": "NEEDS_REVIEW", "mode": None, "override": None, "reason": reason}

    if not isinstance(plan, dict):
        return fail("apply agent returned no plan")
    if plan.get("evidence_holds") is not True:
        return fail("evidence no longer holds on the latest ledger")
    kind = plan.get("kind")
    if kind not in KINDS:
        return fail(f"unknown kind {kind!r}")
    if kind == "code":
        if not str(plan.get("build_brief") or "").strip():
            return fail("code change without a build brief")
        return {"ok": True, "status": "ANALYSED", "mode": None, "override": None, "reason": "code: build brief only"}

    override = dict(plan.get("params_override") or {})
    addendum = str(plan.get("prompt_addendum") or "").strip()
    if addendum:
        override["call2_prompt_addendum"] = addendum
    if not override:
        return fail("no concrete parameter or prompt change")
    unknown = sorted(k for k in override if k not in INTRADAY_HUNTER_V2_DEFAULTS)
    if unknown:
        return fail(f"unknown v2 param key(s) {unknown} — needs a code change")
    shared = sorted(k for k in override if k in NON_CHALLENGER_KEYS)
    if shared:
        return fail(f"{shared} shape the shared data pipeline — not a challenger change")
    bad = sorted(k for k, v in override.items() if not _type_ok(INTRADAY_HUNTER_V2_DEFAULTS[k], v))
    if bad:
        return fail(f"wrong value type for {bad}")
    inert = inert_keys(override, champion)
    if inert:
        return fail(f"{inert} have no effect under the effective basket_t_mode / round_hold_enabled")
    if all(champion.get(k) == v for k, v in override.items()):
        return fail("override is identical to the champion's params")
    mode = "counterfactual" if set(override) <= COUNTERFACTUAL_KEYS else "shadow_call2"
    return {"ok": True, "status": "ANALYSED", "mode": mode, "override": override, "reason": None}


def next_trading_day(d: date) -> date:
    """The first trading day strictly after `d`."""
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def challenger_stats(grades: list[dict], challenger_id: str, started_on: date | None,
                     ended_on: date | None = None, min_days: int = PROMOTION_MIN_DAYS) -> dict:
    """Challenger vs champion over the same graded days. Pure.

    grades: [{trading_date (date), arms}] oldest→newest. Per day, P&L = the arm's counterfactual
    basket P&L (0 when it did not trade): champion = `v2_llm`, challenger = `ch_{id}`. `diff` =
    challenger − champion. Promote needs ≥ min_days with the challenger ahead in BOTH halves;
    Retire is offered once ≥ min_days and it is not.
    """
    arm = f"ch_{challenger_id}"
    days = [g for g in grades
            if started_on and g["trading_date"] >= started_on
            and (ended_on is None or g["trading_date"] <= ended_on)]

    def pnl(g: dict, a: str) -> float:
        return float((((g.get("arms") or {}).get(a) or {}).get("cf") or {}).get("pnl") or 0.0)

    ch = [pnl(g, arm) for g in days]
    champ = [pnl(g, "v2_llm") for g in days]
    diff = [a - b for a, b in zip(ch, champ)]
    n, half = len(diff), len(diff) // 2
    first, second = sum(diff[:half]), sum(diff[half:])
    sd = math.sqrt(sum((x - sum(diff) / n) ** 2 for x in diff) / (n - 1)) if n > 1 else 0.0
    eligible = n >= min_days
    ahead_both = half > 0 and first > 0 and second > 0
    return {
        "days": n, "min_days": min_days,
        "challenger_total": round(sum(ch), 2), "champion_total": round(sum(champ), 2),
        "diff_total": round(sum(diff), 2),
        "diff_mean": round(sum(diff) / n, 2) if n else None,
        "diff_t": round((sum(diff) / n) / (sd / math.sqrt(n)), 3) if n > 1 and sd > 0 else None,
        "first_half_diff": round(first, 2), "second_half_diff": round(second, 2),
        "eligible": eligible, "can_promote": eligible and ahead_both,
        "can_retire": eligible and not ahead_both,
    }


def build_apply_prompt(p: IhV2Proposal, ledger: dict, champion: dict, active: list[dict]) -> str:
    """The apply agent's prompt (Opus). Pure."""
    ctx = {
        "proposal": {"change": p.change, "kind": p.kind, "evidence": p.evidence,
                     "expected_effect": p.expected_effect, "risk": p.risk, "user_note": p.user_note},
        "latest_ledger": ledger,
        "champion_params": {k: v for k, v in champion.items() if not k.startswith("_")},
        "param_keys": {
            "counterfactual (scored offline on v2's own decision)": sorted(COUNTERFACTUAL_KEYS),
            "call2_inputs (challenger runs its own shadow Call 2)": sorted(
                set(INTRADAY_HUNTER_V2_DEFAULTS) - COUNTERFACTUAL_KEYS - NON_CHALLENGER_KEYS),
            "not allowed (shared data pipeline)": sorted(NON_CHALLENGER_KEYS),
        },
        "active_challengers": active,
    }
    rules = (
        "Rules: paper only; the change runs as a CHALLENGER beside the champion, never in place; "
        "promotion needs >= 20 trading days ahead in both halves; judge on option premiums, not "
        "index direction; a single day is never evidence. If the ledger no longer supports the "
        "proposal (small n, t near 0, halves disagree), set evidence_holds=false and explain. "
        "Use ONLY keys listed in param_keys for params_override (exact names, same value types as "
        "champion_params). Keys that are inert under the effective mode are rejected "
        "(basket_tp_sl_pct needs basket_t_mode=pct, rupees_per_lot needs basket_t_mode=rupees, "
        "round_hold_* tunables need round_hold_enabled=true — in champion_params or your override). "
        "A prompt change goes in prompt_addendum as concise instructions. "
        "Anything needing new logic is kind=code with a build_brief and no override."
    )
    return (
        "You are the apply agent for a paper-trading index-options strategy (Intraday Hunter v2). "
        "The user APPROVED the proposal below. Re-check its evidence against the latest ledger and "
        "turn it into a concrete, minimal apply plan.\n\n" + rules + "\n\n"
        f"DATA (JSON):\n{json.dumps(ctx, indent=2, default=str)}\n\n"
        f"Return ONLY this JSON object:\n{json.dumps(APPLY_SCHEMA, indent=2)}"
    )


def telegram_text(p: IhV2Proposal) -> str:
    """Telegram confirmation for an analysed / applied / needs-review proposal. Pure."""
    plan = p.apply_plan or {}
    head = {"APPLIED": "✅ challenger started", "ANALYSED": "📝 build brief ready",
            "NEEDS_REVIEW": "⚠️ needs review", "PROMOTED": "🏆 promoted to champion",
            "RETIRED": "🗑 challenger retired"}.get(p.status, p.status)
    lines = [f"🧠 <b>IH v2 proposal — {head}</b>", str(p.change)[:300]]
    if p.status == "APPLIED":
        lines.append(f"challenger {p.challenger_id} ({p.challenger_mode}) from {p.started_on}: "
                     f"{json.dumps(p.params_override)[:300]}")
    elif p.status == "NEEDS_REVIEW":
        lines.append(str(plan.get("_validation") or plan.get("reasoning") or "")[:400])
    elif p.status == "ANALYSED":
        lines.append(str(plan.get("build_brief") or "")[:600])
    if plan.get("recheck"):
        lines.append(f"recheck: {str(plan['recheck'])[:300]}")
    return "\n".join(lines)


# ───────────────────────────── DB operations ─────────────────────────────

async def sync_proposals(session: AsyncSession, review: IhWeeklyReview) -> list[IhV2Proposal]:
    """Create one IhV2Proposal per proposal in `review.proposal`. Re-running a week replaces only
    rows still PROPOSED (a decided proposal keeps its row, status and history)."""
    existing = (await session.execute(
        select(IhV2Proposal).where(IhV2Proposal.review_id == review.id)
    )).scalars().all()
    kept = {p.idx for p in existing if p.status != "PROPOSED"}
    for p in existing:
        if p.status == "PROPOSED":
            await session.delete(p)
    await session.flush()
    rows = []
    for i, pr in enumerate(((review.proposal or {}).get("proposals") or []), 1):
        if i in kept or not isinstance(pr, dict) or not pr.get("change"):
            continue
        row = IhV2Proposal(review_id=review.id, idx=i, change=str(pr["change"]),
                           kind=str(pr.get("kind") or "")[:12] or None,
                           evidence=pr.get("evidence"), expected_effect=pr.get("expected_effect"),
                           risk=pr.get("risk"), status="PROPOSED", history=[])
        _log(row, "PROPOSED")
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def active_challengers(session: AsyncSession, d: date, mode: str | None = None) -> list[IhV2Proposal]:
    """Challengers that were live on trading date `d` (APPLIED, or closed on/after `d`)."""
    rows = (await session.execute(
        select(IhV2Proposal).where(
            IhV2Proposal.challenger_id.is_not(None),
            IhV2Proposal.status.in_((*LIVE_STATUSES, *CLOSED_CHALLENGER)),
            IhV2Proposal.started_on <= d,
        )
    )).scalars().all()
    return [p for p in rows
            if (p.ended_on is None or p.ended_on >= d) and (mode is None or p.challenger_mode == mode)]


async def _next_challenger_id(session: AsyncSession) -> str:
    ids = (await session.execute(
        select(IhV2Proposal.challenger_id).where(IhV2Proposal.challenger_id.is_not(None))
    )).scalars().all()
    nums = [int(i[1:]) for i in ids if i and i[1:].isdigit()]
    return f"c{max(nums, default=0) + 1}"


async def decide(session: AsyncSession, p: IhV2Proposal, action: str, note: str | None) -> IhV2Proposal:
    """approve / reject (user click). Approve → APPROVED; the caller then starts the agent."""
    check_transition(p, action)
    p.user_note = note or p.user_note
    _log(p, "APPROVED" if action == "approve" else "REJECTED", note)
    await session.flush()
    return p


async def decide_reanalyse(session: AsyncSession, p: IhV2Proposal, note: str | None) -> IhV2Proposal:
    """User click "Re-analyse" on NEEDS_REVIEW → APPROVED (the caller starts the agent)."""
    check_transition(p, "analyse")
    p.user_note = note or p.user_note
    _log(p, "APPROVED", note or "re-analyse")
    await session.flush()
    return p


async def run_apply_agent(session: AsyncSession, p: IhV2Proposal) -> IhV2Proposal:
    """Re-check + classify + apply one APPROVED (or NEEDS_REVIEW, re-run) proposal. Never raises
    on an LLM failure (→ NEEDS_REVIEW)."""
    from app.services.intraday_hunter_v2 import grading

    check_transition(p, "analyse")
    champion = await v2_params_async()
    led = await grading.ledger(session)
    active = [{"challenger_id": c.challenger_id, "mode": c.challenger_mode,
               "params_override": c.params_override, "started_on": c.started_on}
              for c in await active_challengers(session, now_ist().date())]
    plan = await llm_cli.call_claude_json(
        build_apply_prompt(p, led, champion, active),
        required_keys=("evidence_holds", "kind", "reasoning"),
    )
    verdict = validate_plan(plan, champion)
    p.apply_plan = {**(plan or {}), "_validation": verdict["reason"], "_mode": verdict["mode"],
                    "_analysed_at": now_ist().isoformat(timespec="seconds")}
    if not verdict["ok"]:
        _log(p, "NEEDS_REVIEW", verdict["reason"])
    elif verdict["mode"] is None:
        _log(p, "ANALYSED", "code change — build brief only, nothing applied")
    else:
        _log(p, "ANALYSED", f"{verdict['mode']} challenger")
        p.challenger_id = await _next_challenger_id(session)
        p.challenger_mode = verdict["mode"]
        p.params_override = verdict["override"]
        p.started_on = next_trading_day(now_ist().date())
        p.ended_on = None
        _log(p, "APPLIED", challenger_id=p.challenger_id, started_on=p.started_on.isoformat())
    await session.flush()
    return p


async def run_apply_agent_by_id(proposal_id) -> None:
    """Background entry point (own session; commits; Telegram). Never raises."""
    from app.agent.notification import send_telegram
    from app.core.database import async_session_factory

    try:
        async with async_session_factory() as session:
            p = await session.get(IhV2Proposal, proposal_id)
            if p is None or p.status not in TRANSITIONS["analyse"]:
                return
            await run_apply_agent(session, p)
            await session.commit()
            text = telegram_text(p)
        logger.info("ih_v2 apply agent: proposal %s → %s", proposal_id, p.status)
        try:
            await send_telegram(text)
        except Exception:  # noqa: BLE001
            logger.warning("ih_v2 apply agent: Telegram failed")
    except Exception:  # noqa: BLE001
        logger.exception("ih_v2 apply agent failed for %s", proposal_id)


_agent_tasks: dict[str, asyncio.Task] = {}


def agent_running(proposal_id) -> bool:
    """True while this process has an apply agent in flight for the proposal."""
    t = _agent_tasks.get(str(proposal_id))
    return bool(t and not t.done())


def start_apply_agent(proposal_id) -> asyncio.Task | None:
    """Fire-and-forget the apply agent (the approve/analyse endpoints return immediately).
    Single-flight per proposal: returns None when one is already running."""
    key = str(proposal_id)
    if agent_running(key):
        return None
    task = asyncio.create_task(run_apply_agent_by_id(proposal_id), name=f"ih_v2_apply_{key}")
    _agent_tasks[key] = task
    task.add_done_callback(lambda _t, k=key: _agent_tasks.pop(k, None))
    return task


async def stats_for(session: AsyncSession, p: IhV2Proposal) -> dict | None:
    """challenger_stats for an APPLIED/closed challenger (None when it has none)."""
    if not p.challenger_id:
        return None
    rows = (await session.execute(
        select(IhDayGrade).where(IhDayGrade.trading_date >= p.started_on).order_by(IhDayGrade.trading_date)
    )).scalars().all()
    grades = [{"trading_date": r.trading_date, "arms": r.arms} for r in rows]
    return challenger_stats(grades, p.challenger_id, p.started_on, p.ended_on)


async def promote(session: AsyncSession, p: IhV2Proposal, note: str | None) -> IhV2Proposal:
    """User click: merge the challenger's override into the champion's params (only when the
    challenger is ahead in both halves after ≥ PROMOTION_MIN_DAYS)."""
    from app.models.strategy_config import StrategyConfig
    from app.services.strategy_params import clear_strategy_params_cache

    check_transition(p, "promote")
    stats = await stats_for(session, p)
    if not stats or not stats["can_promote"]:
        raise TransitionError(f"promotion needs >= {PROMOTION_MIN_DAYS} graded days ahead in both halves: {stats}")
    cfg = (await session.execute(
        select(StrategyConfig).where(StrategyConfig.strategy_name == STRATEGY)
    )).scalar_one()
    before = dict(cfg.parameters or {})
    cfg.parameters = {**before, **(p.params_override or {})}
    p.ended_on = now_ist().date()
    p.user_note = note or p.user_note
    _log(p, "PROMOTED", note, stats=stats,
         previous={k: before.get(k) for k in (p.params_override or {})})
    await session.flush()
    clear_strategy_params_cache(STRATEGY)
    return p


async def retire(session: AsyncSession, p: IhV2Proposal, note: str | None) -> IhV2Proposal:
    """User click: stop the challenger (offered once ≥ PROMOTION_MIN_DAYS and not ahead)."""
    check_transition(p, "retire")
    stats = await stats_for(session, p)
    if not stats or not stats["can_retire"]:
        raise TransitionError(f"retire is offered after >= {PROMOTION_MIN_DAYS} graded days when not ahead: {stats}")
    p.ended_on = now_ist().date()
    p.user_note = note or p.user_note
    _log(p, "RETIRED", note, stats=stats)
    await session.flush()
    return p
