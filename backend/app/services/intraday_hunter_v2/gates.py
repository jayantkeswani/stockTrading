"""v2 entry gates — pure functions. SHADOW-ONLY for the first 20 trading days.

(a) plan gate: the teacher's plan for TODAY'S ACTUAL OPENING (gap-up / flat / gap-down side)
    opposes v2's side;  (b) OI gate: the opening option-chain OI flow opposes v2's side.

With `enforce_plan_gate` / `enforce_oi_gate` = False (the seeded default) a gate is computed and
logged (minute log `arms.gates`, the run's call2_json `_gates`, the nightly grade's what-if) but
NEVER blocks. Enforcing them is out of scope until the ledger shows they earn it.
"""
from __future__ import annotations

SIDES = ("CE", "PE")


def plan_side_for_opening(plan: dict | None, opening: str) -> str | None:
    """The teacher's side for today's actual opening type, or None (no plan / 'none')."""
    if not plan:
        return None
    key = {"gap_up": "gap_up_side", "flat": "flat_side", "gap_down": "gap_down_side"}.get(opening)
    side = (plan.get(key) or "").upper() if key else ""
    return side if side in SIDES else None


def _verdict(ref_side: str | None, side: str | None) -> str:
    if ref_side not in SIDES or side not in SIDES:
        return "NA"
    return "AGREES" if ref_side == side else "OPPOSES"


def compute_gates(
    side: str | None,
    plan_side: str | None,
    oi_side: str | None,
    *,
    enforce_plan: bool = False,
    enforce_oi: bool = False,
) -> dict:
    """Both gate verdicts for a candidate side + whether an ENFORCED gate would block it.

    Returns {plan: {ref_side, verdict, would_block, enforced}, oi: {...}, blocked: bool}.
    `would_block` = the gate opposes (logged regardless); `blocked` = an enforced gate opposes.
    """
    out: dict = {}
    for name, ref, enforced in (("plan", plan_side, enforce_plan), ("oi", oi_side, enforce_oi)):
        v = _verdict(ref, side)
        out[name] = {
            "ref_side": ref,
            "verdict": v,
            "would_block": v == "OPPOSES",
            "enforced": bool(enforced),
        }
    out["blocked"] = any(out[g]["would_block"] and out[g]["enforced"] for g in ("plan", "oi"))
    return out
