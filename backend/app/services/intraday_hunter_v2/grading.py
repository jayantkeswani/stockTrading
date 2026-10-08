"""Nightly grading (16:00 IST; re-run → FINAL once the teacher's live trade lands).

Writes one `ih_day_grades` row per trading date:
  market   clean side by FIRST TOUCH from 09:16 at each barrier set (+0.25/-0.20%, +0.30/-0.25%),
           per index + 2-of-3 majority; index move to 10:30 and to EOD.
  teacher  his side, entry/exit clocks, P&L, whether a PDH/PDL broke before his entry (and
           whether he went with it).
  v1, v2   decision, side, entry time, real paper net P&L (YOLO + SHADOW), exit reasons, and
           the barrier label from their own entry.
  arms     every arm's side + entry time + COUNTERFACTUAL basket P&L on REAL premiums: the
           captured ATM±2 1m premium candles, v2's basket shape (BN ATM+OTM1, NIFTY ATM, SENSEX
           ATM), 1 lot per leg, replayed through the SAME `basket.evaluate_basket` rules.
  gates    v2's P&L had each gate been enforced (opposed → 0) — the shadow-gate what-if.
  lesson   compact structured lesson fed to v2's Call 1 (replaces the empty v1 memory).
Also fills v1's `intraday_hunter_runs.outcome_played_out` (ENTER: its side == the clean side;
SKIP: no clean side).

Replay conventions (documented in docs/ai/intraday-hunter-v2.md §Grading): a decision at wall
time D fills at the close of candle D-1; the basket is then evaluated on each later candle CLOSE
at time minute+1 (LTP basis — premium candles carry no bid). Same-candle barrier ties = stop.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IST, LOT_SIZES, STRIKE_GAPS
from app.core.utils import now_ist
from app.models.ih_v2 import IhDayGrade, IhMinuteLog
from app.models.trade import Trade
from app.services.intraday_hunter import store
from app.services.intraday_hunter_v2 import capture, levels
from app.services.intraday_hunter_v2 import context as ctx_mod
from app.services.intraday_hunter_v2.basket import (
    LegQuote,
    RoundHoldState,
    basket_mtm,
    evaluate_basket,
)
from app.services.intraday_hunter_v2.params import STRATEGY, parse_hhmm, v2_params_async

logger = logging.getLogger(__name__)

ARMS = ("rule_a", "plan_side", "oi_flow_side", "v1", "v2_llm", "jev", "teacher")


# ─────────────────────────── pure helpers ───────────────────────────
def _hm(c) -> str:
    return c["ts"][11:16] if isinstance(c["ts"], str) else c["ts"].strftime("%H:%M")


def _add_min(hhmm: str, n: int) -> str:
    t = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=n)
    return t.strftime("%H:%M")


def first_touch(candles: list[dict], ref: float, side: str, tgt_pct: float, stop_pct: float) -> str | None:
    """'target' | 'stop' | None — which barrier the index hit first for `side` from `ref`.

    CE: target = ref*(1+tgt), stop = ref*(1-stop); PE mirrors. Same-candle tie → 'stop'.
    """
    if not ref:
        return None
    up_t, dn_s = ref * (1 + tgt_pct / 100), ref * (1 - stop_pct / 100)
    dn_t, up_s = ref * (1 - tgt_pct / 100), ref * (1 + stop_pct / 100)
    for c in candles:
        hi, lo = float(c["high"]), float(c["low"])
        if side == "CE":
            hit_s, hit_t = lo <= dn_s, hi >= up_t
        else:
            hit_s, hit_t = hi >= up_s, lo <= dn_t
        if hit_s:
            return "stop"
        if hit_t:
            return "target"
    return None


def clean_side(candles: list[dict], ref: float, tgt_pct: float, stop_pct: float) -> str | None:
    """The side whose target is touched before its stop (mutually exclusive), else None."""
    for side in ("CE", "PE"):
        if first_touch(candles, ref, side, tgt_pct, stop_pct) == "target":
            return side
    return None


def majority(sides: dict[str, str | None], k: int = 2) -> str | None:
    vals = [s for s in sides.values() if s]
    for s in ("CE", "PE"):
        if vals.count(s) >= k:
            return s
    return None


def move_pct(candles: list[dict], ref: float, until: str | None = None) -> float | None:
    """% move from `ref` to the last close at or before `until` (EOD when None)."""
    sel = [c for c in candles if until is None or _hm(c) <= until]
    if not sel or not ref:
        return None
    return round((float(sel[-1]["close"]) / ref - 1) * 100, 3)


def simulate_basket(
    decision_hhmm: str,
    direction: str,
    legs: list[dict],
    index_closes: dict[str, dict[str, float]],
    params: dict,
    trading_date: date,
) -> dict | None:
    """Replay v2's basket rules on 1m closes. Pure.

    legs: [{index, qty, closes: {HH:MM: premium close}}]. Entry = close of candle (D-1).
    Returns {pnl, exit_reason, sub_reason, exit_time, entry_cost, round_hold} or None
    (no entry premium for some leg).
    """
    entry_m = _add_min(decision_hhmm, -1)
    quotes = []
    for leg in legs:
        px = leg["closes"].get(entry_m)
        if px is None or px <= 0:
            return None
        quotes.append({"index": leg["index"], "qty": int(leg["qty"]), "entry": float(px),
                       "closes": leg["closes"]})
    if not quotes:
        return None
    minutes = sorted({m for q in quotes for m in q["closes"] if m >= decision_hhmm})
    state = RoundHoldState()
    last_px = {id(q): q["entry"] for q in quotes}
    round_events = []
    for m in minutes:
        for q in quotes:
            if m in q["closes"]:
                last_px[id(q)] = float(q["closes"][m])
        lq = [LegQuote(index=q["index"], qty=q["qty"], entry_price=q["entry"], ltp=last_px[id(q)])
              for q in quotes]
        mtm, cost = basket_mtm(lq, use_book=False)
        t_eval = datetime.combine(trading_date, parse_hhmm(_add_min(m, 1)), tzinfo=IST)
        spots = {i: c.get(m) for i, c in index_closes.items()}
        dec = evaluate_basket(mtm=mtm, cost=cost, direction=direction, spots=spots,
                              traded_indices=[q["index"] for q in quotes], now=t_eval,
                              state=state, params=params)
        if dec.event:
            round_events.append({"at": _add_min(m, 1), "event": dec.event})
        if dec.action == "CLOSE":
            return {"pnl": round(mtm, 2), "exit_reason": dec.exit_reason, "sub_reason": dec.reason,
                    "exit_time": _add_min(m, 1), "entry_cost": round(cost, 2),
                    "round_hold": round_events}
    lq = [LegQuote(index=q["index"], qty=q["qty"], entry_price=q["entry"], ltp=last_px[id(q)])
          for q in quotes]
    mtm, cost = basket_mtm(lq, use_book=False)
    return {"pnl": round(mtm or 0, 2), "exit_reason": "END_OF_DATA", "sub_reason": None,
            "exit_time": minutes[-1] if minutes else decision_hhmm, "entry_cost": round(cost, 2),
            "round_hold": round_events}


def basket_legs_for(
    direction: str, spots: dict[str, float], contracts: dict[str, list[dict]],
    premium_closes: dict[str, dict[str, float]], leg_structure: dict[str, list[int]],
) -> list[dict]:
    """v2-shape legs (1 lot each) resolved against the captured contracts. Pure.

    depth 0 = ATM, -1 = OTM-1 (CE up / PE down). Missing contracts are skipped.
    """
    legs = []
    for idx, depths in leg_structure.items():
        spot, gap = spots.get(idx), STRIKE_GAPS.get(idx)
        if not spot or not gap:
            continue
        atm = round(spot / gap) * gap
        for depth in depths:
            if depth == 0:
                strike = atm
            elif direction == "CE":
                strike = atm - depth * gap
            else:
                strike = atm + depth * gap
            sym = next((r["symbol"] for r in contracts.get(idx, [])
                        if float(r["strike"]) == float(strike) and r["type"] == direction), None)
            if sym and premium_closes.get(sym):
                legs.append({"index": idx, "qty": LOT_SIZES.get(idx, 1), "symbol": sym,
                             "strike": strike, "closes": premium_closes[sym]})
    return legs


def isotonic_fit(xs: list[float], ys: list[float]) -> list[dict]:
    """Pool-adjacent-violators isotonic regression → [{x_min, x_max, p}] (non-decreasing p)."""
    pts = sorted(zip(xs, ys))
    blocks: list[list[float]] = []  # [sum_y, n, x_min, x_max]
    for x, y in pts:
        blocks.append([float(y), 1, x, x])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b = blocks.pop()
            blocks[-1][0] += b[0]
            blocks[-1][1] += b[1]
            blocks[-1][3] = b[3]
    return [{"x_min": b[2], "x_max": b[3], "p": round(b[0] / b[1], 4), "n": b[1]} for b in blocks]


def t_stat(vals: list[float]) -> float | None:
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return round(m / (sd / math.sqrt(n)), 3) if sd > 0 else None


def compute_ledger(grades: list[dict], window: int) -> dict:
    """Rolling per-arm stats over the last `window` graded days. Pure.

    grades: [{trading_date, market:{clean_side}, arms:{arm:{side, cf:{pnl}}}, gates:{...}}]
    oldest→newest. Per arm: n, right_side_pct, win_pct, mean_cf_pnl, t_stat, first/second half.
    """
    g = grades[-window:]
    out: dict = {}
    arm_names = sorted({a for row in g for a in (row.get("arms") or {})})
    for arm in arm_names:
        sides_right, pnls = [], []
        for row in g:
            a = (row.get("arms") or {}).get(arm) or {}
            side = a.get("side")
            if side not in ("CE", "PE"):
                continue
            clean = (row.get("market") or {}).get("clean_side")
            sides_right.append(1 if side == clean else 0)
            cf = (a.get("cf") or {}).get("pnl")
            if cf is not None:
                pnls.append(float(cf))
        half = len(pnls) // 2
        out[arm] = {
            "n": len(sides_right),
            "right_side_pct": round(100 * sum(sides_right) / len(sides_right), 1) if sides_right else None,
            "n_cf": len(pnls),
            "win_pct": round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 1) if pnls else None,
            "mean_cf_pnl": round(sum(pnls) / len(pnls), 2) if pnls else None,
            "t_stat": t_stat(pnls),
            "first_half_mean": round(sum(pnls[:half]) / half, 2) if half else None,
            "second_half_mean": (round(sum(pnls[half:]) / (len(pnls) - half), 2)
                                 if len(pnls) - half else None),
        }
    gate_rows = [row.get("gates") or {} for row in g]
    out["_gates"] = {
        k: round(sum(float((r.get(k) or {}).get("pnl") or 0) for r in gate_rows), 2)
        for k in ("v2_actual", "plan_enforced", "oi_enforced", "both_enforced")
    }
    out["_window"] = {"days": len(g), "from": g[0]["trading_date"] if g else None,
                      "to": g[-1]["trading_date"] if g else None}
    return out


def build_lesson(d: date, market: dict, teacher: dict | None, v2: dict | None, arms: dict,
                 pools_by_index: dict[str, list[str]]) -> dict:
    """Compact structured lesson for v2's Call 1 (deterministic template, no LLM). Pure."""
    clean = market.get("clean_side")
    right = [a for a, v in arms.items() if v.get("side") and v.get("side") == clean]
    t_side = (teacher or {}).get("side")
    t_res = (teacher or {}).get("pnl")
    v2_side = (v2 or {}).get("side")
    v2_res = (v2 or {}).get("yolo_net_pnl")
    taken = sorted({p for ps in pools_by_index.values() for p in ps})
    take = (f"{market.get('opening', '?').replace('_', '-')} open; "
            f"{'pools taken: ' + ', '.join(taken) if taken else 'no pool broken by 09:25'}; "
            f"clean side {clean or 'none (chop)'}; "
            f"v2 {v2_side or 'skipped'}{'' if v2_res is None else f' ({v2_res:+.0f})'}"
            f"{'; teacher ' + t_side if t_side else ''}; right arms: {', '.join(right) or 'none'}.")
    return {
        "date": d.isoformat(),
        "opening": market.get("opening"),
        "pools_broken": pools_by_index,
        "teacher": {"side": t_side, "result": t_res},
        "v2": {"side": v2_side, "result": v2_res},
        "arms_right": right,
        "one_line_takeaway": take,
    }


# ─────────────────────────── DB orchestration ───────────────────────────
async def _premium_closes(session: AsyncSession, d: date, symbols: list[str]) -> dict[str, dict[str, float]]:
    from app.services.intraday_hunter.data import fetch_day

    out = {}
    for s in symbols:
        rows = await fetch_day(session, s, d)
        if rows:
            out[s] = {_hm(c): float(c["close"]) for c in rows}
    return out


async def _book_pnl(session: AsyncSession, d: date, strategy: str) -> dict:
    """Real paper P&L for `strategy` trades entered on `d`: YOLO vs SHADOW."""
    rows = (await session.execute(
        select(Trade).where(
            Trade.strategy_name == strategy,
            cast(func.timezone("Asia/Kolkata", Trade.entry_time), Date) == d,
        )
    )).scalars().all()
    out: dict = {"yolo_net_pnl": None, "shadow_net_pnl": None, "exit_reasons": [], "entry_time": None,
                 "legs": len(rows), "open_legs": 0}
    for t in rows:
        if t.status != "CLOSED":
            out["open_legs"] += 1
            continue
        key = "shadow_net_pnl" if t.source == "SHADOW" else "yolo_net_pnl"
        v = float(t.net_pnl if t.net_pnl is not None else (t.pnl or 0))
        out[key] = round((out[key] or 0) + v, 2)
        if t.exit_reason and t.exit_reason not in out["exit_reasons"]:
            out["exit_reasons"].append(t.exit_reason)
        et = t.entry_time.astimezone(IST).strftime("%H:%M:%S") if t.entry_time else None
        if et and (out["entry_time"] is None or et < out["entry_time"]):
            out["entry_time"] = et
    return out


async def _arm_entries(session: AsyncSession, d: date, params: dict) -> dict[str, dict]:
    """First side each logged arm gave inside the decision window → {arm: {side, decision_at}}."""
    rows = (await session.execute(
        select(IhMinuteLog).where(IhMinuteLog.trading_date == d, IhMinuteLog.index == "NIFTY")
        .order_by(IhMinuteLog.minute_ts)
    )).scalars().all()
    first, deadline = params["call2_first"], params["call2_deadline"]
    out: dict[str, dict] = {}
    for r in rows:
        dt = _add_min(r.minute_ts.astimezone(IST).strftime("%H:%M"), 1)
        if not (first <= dt <= deadline):
            continue
        arms = r.arms or {}
        cand = {
            "rule_a": arms.get("rule_a"),
            "plan_side": arms.get("plan_side"),
            "oi_flow_side": arms.get("oi_flow_side"),
            "jev": (((arms.get("jev") or {}).get("answers") or {}).get("side") or {}).get("answer"),
        }
        for arm, side in cand.items():
            if arm not in out and side in ("CE", "PE"):
                out[arm] = {"side": side, "decision_at": dt}
    return out


async def grade_day(session: AsyncSession, d: date) -> IhDayGrade | None:
    """Grade trading date `d` (idempotent upsert). Returns the grade row (None: no index data)."""
    from app.services.intraday_hunter.data import fetch_day

    params = await v2_params_async()
    idx_candles = {i: await fetch_day(session, i, d) for i in levels.INDICES}
    if not any(idx_candles.values()):
        logger.warning("ih_v2 grade %s: no index candles — skipped", d)
        return None
    prev = await ctx_mod.prev_day_all(session, d)
    teacher_row = await ctx_mod.get_teacher_day(session, d)
    plan = teacher_row.plan if teacher_row and teacher_row.plan else None
    live = teacher_row.live if teacher_row and teacher_row.live else None

    # ── market labels ──
    def ref_at(idx: str, hhmm: str) -> float | None:
        c = [x for x in idx_candles[idx] if _hm(x) < hhmm]
        return float(c[-1]["close"]) if c else None

    barriers = params.get("grade_barriers") or [[0.25, 0.20], [0.30, 0.25]]
    market: dict = {"barriers": {}, "move_to_1030": {}, "move_eod": {}}
    for tgt, stp in barriers:
        key = f"+{tgt}/-{stp}"
        per = {}
        for i, cs in idx_candles.items():
            ref = ref_at(i, "09:16")
            per[i] = clean_side([c for c in cs if _hm(c) >= "09:16"], ref, tgt, stp) if ref else None
        market["barriers"][key] = {"per_index": per, "majority": majority(per)}
    for i, cs in idx_candles.items():
        ref = ref_at(i, "09:16")
        market["move_to_1030"][i] = move_pct(cs, ref, "10:30") if ref else None
        market["move_eod"][i] = move_pct(cs, ref) if ref else None
    first_key = f"+{barriers[0][0]}/-{barriers[0][1]}"
    market["clean_side"] = market["barriers"][first_key]["majority"]

    facts_0925 = ctx_mod.facts_by_index(
        prev, {i: [c for c in cs if _hm(c) <= "09:24"] for i, cs in idx_candles.items()}, plan, params,
    )
    market["opening"] = ctx_mod.opening_of(facts_0925, params) if facts_0925 else None
    pools = {i: f["pools_taken"] for i, f in facts_0925.items()}

    def label_from(entry_hhmm: str | None, side: str | None) -> dict | None:
        if not entry_hhmm or side not in ("CE", "PE"):
            return None
        out = {}
        for tgt, stp in barriers:
            per = {}
            for i, cs in idx_candles.items():
                ref = ref_at(i, entry_hhmm)
                per[i] = first_touch([c for c in cs if _hm(c) >= entry_hhmm], ref, side, tgt, stp) if ref else None
            out[f"+{tgt}/-{stp}"] = per
        return out

    # ── teacher ──
    teacher = None
    if live:
        t_entry = (live.get("entry_clock") or "")[:5] or None
        f_at = ctx_mod.facts_by_index(
            prev, {i: [c for c in cs if t_entry and _hm(c) < t_entry] for i, cs in idx_candles.items()},
            plan, params,
        ) if t_entry else {}
        brk = {i: levels.pdh_pdl_break_side(f) for i, f in f_at.items()}
        brk_side = majority(brk, 1) if any(brk.values()) else None
        teacher = {
            "side": live.get("side"), "entry_clock": t_entry, "exit_clock": live.get("exit_clock"),
            "pnl": live.get("total_pnl"), "legs": live.get("legs"),
            "pdh_pdl_broke_before_entry": any(brk.values()), "break_side": brk_side,
            "went_with_break": (brk_side == live.get("side")) if brk_side else None,
            "label_from_entry": label_from(t_entry, live.get("side")),
        }
    plan_info = {"present": plan is not None,
                 "side_for_opening": ctx_mod.plan_side_today(plan, market["opening"] or "flat")}

    # ── v1 / v2 ──
    out_variants = {}
    for variant, strat in (("v1", "intraday_hunter"), ("v2", STRATEGY)):
        run = await store.get_run(session, d, variant)
        c2 = (run.call2_json or {}) if run else {}
        book = await _book_pnl(session, d, strat)
        out_variants[variant] = {
            "decision": run.decision if run else None,
            "side": run.direction if run else None,
            "decision_at": c2.get("_at"),
            "confidence": run.confidence if run else None,
            "skip_reason_code": c2.get("skip_reason_code"),
            **book,
            "label_from_entry": label_from(c2.get("_at"), run.direction if run else None),
        }
        if variant == "v1" and run is not None:
            clean = market["clean_side"]
            if run.decision == "ENTER":
                run.outcome_played_out = run.direction == clean
            elif run.decision == "SKIP":
                run.outcome_played_out = clean is None

    # ── arms + counterfactual baskets ──
    entries = await _arm_entries(session, d, params)
    for v, arm in (("v1", "v1"), ("v2", "v2_llm")):
        if out_variants[v]["decision"] == "ENTER" and out_variants[v]["side"]:
            entries[arm] = {"side": out_variants[v]["side"], "decision_at": out_variants[v]["decision_at"]}
    if teacher and teacher.get("side") in ("CE", "PE") and teacher.get("entry_clock"):
        entries["teacher"] = {"side": teacher["side"], "decision_at": teacher["entry_clock"]}

    cap = await capture.load_capture(d)
    contracts = cap.get("contracts") or {}
    prem = await _premium_closes(session, d, cap.get("symbols") or [])
    index_closes = {i: {_hm(c): float(c["close"]) for c in cs} for i, cs in idx_candles.items()}
    structure = params.get("leg_structure") or {}
    arms: dict = {}
    for arm in ARMS:
        e = entries.get(arm)
        if not e:
            arms[arm] = {"side": None}
            continue
        entry_m = _add_min(e["decision_at"], -1)
        spots = {i: index_closes[i].get(entry_m) for i in index_closes}
        legs = basket_legs_for(e["side"], spots, contracts, prem, structure)
        cf = simulate_basket(e["decision_at"], e["side"], legs, index_closes, params, d) if legs else None
        arms[arm] = {"side": e["side"], "decision_at": e["decision_at"],
                     "right_side": e["side"] == market["clean_side"],
                     "cf": cf, "legs": [{k: l[k] for k in ("index", "strike", "symbol")} for l in legs]}

    # ── gates what-if (v2 actual YOLO P&L; opposed+enforced → 0) ──
    v2 = out_variants["v2"]
    v2_gates = None
    v2_run = await store.get_run(session, d, "v2")
    if v2_run and v2_run.call2_json:
        v2_gates = v2_run.call2_json.get("_gates")
    actual = float(v2.get("yolo_net_pnl") or 0)
    opp_plan = bool(v2_gates and v2_gates["plan"]["would_block"])
    opp_oi = bool(v2_gates and v2_gates["oi"]["would_block"])
    gates = {
        "v2_actual": {"pnl": actual},
        "plan_enforced": {"pnl": 0.0 if opp_plan else actual, "would_block": opp_plan},
        "oi_enforced": {"pnl": 0.0 if opp_oi else actual, "would_block": opp_oi},
        "both_enforced": {"pnl": 0.0 if (opp_plan or opp_oi) else actual},
        "verdicts": v2_gates,
    }

    lesson = build_lesson(d, market, teacher, v2, arms, pools)
    row = (await session.execute(select(IhDayGrade).where(IhDayGrade.trading_date == d))).scalar_one_or_none()
    if row is None:
        row = IhDayGrade(trading_date=d)
        session.add(row)
    row.status = "FINAL" if live else "PRELIM"
    row.market = market
    row.teacher = {**(teacher or {}), "plan": plan_info}
    row.v1 = out_variants["v1"]
    row.v2 = v2
    row.arms = arms
    row.gates = gates
    row.lesson = lesson
    row.graded_at = now_ist()
    await session.flush()
    logger.info("ih_v2 grade %s: %s clean=%s v2=%s arms=%s", d, row.status, market["clean_side"],
                v2.get("side"), {a: (v.get("cf") or {}).get("pnl") for a, v in arms.items() if v.get("side")})
    return row


async def ledger(session: AsyncSession, windows=(20, 60)) -> dict:
    """Rolling arm ledger over the last 20 and 60 graded trading days."""
    rows = (await session.execute(select(IhDayGrade).order_by(IhDayGrade.trading_date))).scalars().all()
    grades = [{"trading_date": r.trading_date.isoformat(), "market": r.market, "arms": r.arms,
               "gates": r.gates} for r in rows]
    return {f"last_{w}": compute_ledger(grades, w) for w in windows}
