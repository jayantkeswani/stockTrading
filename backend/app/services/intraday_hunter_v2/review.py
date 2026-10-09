"""Saturday weekly review — Claude reads the ledger + recent grades and PROPOSES changes.

Nothing is auto-applied. A review row is stored in `ih_weekly_reviews`, one `ih_v2_proposals` row
per proposal (status PROPOSED — approve/reject per proposal in the UI, see `apply.py`), and a
Telegram summary is sent. An approved change runs as a challenger beside the champion; promotion
needs the challenger to be ahead over ≥20 trading days in both halves, and the user's click.

Also (once there are ≥30 v2 trades) fits an isotonic map from v2 confidence → realised win rate
and stores it on the review row — recorded only, NOT used for sizing.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ih_v2 import IhDayGrade, IhWeeklyReview
from app.services.intraday_hunter import llm_cli
from app.services.intraday_hunter_v2 import apply, grading
from app.services.intraday_hunter_v2.params import v2_params_async

logger = logging.getLogger(__name__)

MIN_CALIBRATION_TRADES = 30

PROPOSAL_SCHEMA = {
    "summary": "3-5 lines: what the evidence says this week",
    "proposals": [{
        "change": "the specific parameter or prompt change",
        "kind": "param | prompt | gate",
        "evidence": "ledger/grade numbers that support it (cite n and t)",
        "expected_effect": "what should improve",
        "risk": "what could get worse / overfitting risk",
    }],
    "challenger_variant": "how to run it as a challenger on a new paper profile",
    "do_not_change": "anything the evidence says to leave alone",
}


def calibration_points(grades: list[IhDayGrade]) -> tuple[list[float], list[float]]:
    """(confidence, win 0/1) for every graded v2 ENTER with realised YOLO P&L. Pure."""
    xs, ys = [], []
    for g in grades:
        v2 = g.v2 or {}
        if v2.get("decision") == "ENTER" and v2.get("confidence") is not None \
                and v2.get("yolo_net_pnl") is not None:
            xs.append(float(v2["confidence"]))
            ys.append(1.0 if float(v2["yolo_net_pnl"]) > 0 else 0.0)
    return xs, ys


async def run_weekly_review(session: AsyncSession, week_ending: date) -> IhWeeklyReview:
    """Build + store this week's review (idempotent per week_ending)."""
    params = await v2_params_async()
    led = await grading.ledger(session)
    recent = (await session.execute(
        select(IhDayGrade).order_by(IhDayGrade.trading_date.desc()).limit(10)
    )).scalars().all()
    all_grades = (await session.execute(select(IhDayGrade))).scalars().all()
    xs, ys = calibration_points(all_grades)
    calib = ({"n": len(xs), "map": grading.isotonic_fit(xs, ys)}
             if len(xs) >= MIN_CALIBRATION_TRADES else {"n": len(xs), "map": None,
                                                         "note": f"needs >= {MIN_CALIBRATION_TRADES} v2 trades"})
    ctx = {
        "current_params": {k: v for k, v in params.items() if not k.startswith("_")},
        "ledger": led,
        "recent_grades": [{"date": g.trading_date.isoformat(), "market": g.market,
                           "v2": g.v2, "lesson": g.lesson} for g in reversed(recent)],
        "rules": "Nothing is auto-applied. Approved changes run as a challenger on a new paper "
                 "profile and need >= 20 trading days with both halves positive to be promoted.",
    }
    prompt = (
        "You are reviewing a paper-trading index-options agent (Intraday Hunter v2) that rides "
        "breaks of stop pools at the open with a basket exited at ~1:1. Read the ledger and grades "
        "and propose SPECIFIC, evidence-backed parameter or prompt changes. Be conservative with "
        "small samples (state n and t). If nothing is supported, say so.\n\n"
        f"DATA (JSON):\n{json.dumps(ctx, indent=2, default=str)}\n\n"
        f"Return ONLY this JSON object:\n{json.dumps(PROPOSAL_SCHEMA, indent=2)}"
    )
    proposal = await llm_cli.call_claude_json(prompt, required_keys=("summary", "proposals"))
    row = (await session.execute(
        select(IhWeeklyReview).where(IhWeeklyReview.week_ending == week_ending)
    )).scalar_one_or_none()
    if row is None:
        row = IhWeeklyReview(week_ending=week_ending)
        session.add(row)
    row.ledger = led
    row.proposal = proposal or {"error": "LLM unavailable — ledger stored without a proposal"}
    row.calibration = calib
    row.summary = (proposal or {}).get("summary")
    row.status = "PROPOSED"
    await session.flush()
    await apply.sync_proposals(session, row)
    return row


def telegram_summary(row: IhWeeklyReview) -> str:
    """Short Telegram text for a review row. Pure."""
    p = row.proposal or {}
    lines = [f"🧠 <b>IH v2 weekly review — {row.week_ending}</b>"]
    if p.get("summary"):
        lines.append(str(p["summary"])[:900])
    for i, pr in enumerate((p.get("proposals") or [])[:5], 1):
        lines.append(f"{i}. {pr.get('change')} — {pr.get('evidence')}"[:300])
    led20 = (row.ledger or {}).get("last_20") or {}
    for arm in ("v2_llm", "teacher", "rule_a", "oi_flow_side", "plan_side"):
        a = led20.get(arm)
        if a and a.get("n"):
            lines.append(f"• {arm}: n={a['n']} right={a['right_side_pct']}% "
                         f"meanCF={a['mean_cf_pnl']} t={a['t_stat']}")
    lines.append("Nothing auto-applied — approve / reject each proposal on the IH v2 tab.")
    return "\n".join(lines)
