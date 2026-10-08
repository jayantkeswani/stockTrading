"""Basket-level exit rules for Intraday Hunter v2 — pure (no DB / Redis / clock).

One implementation, two callers: the live trade monitor (`trade_monitor._check_ih_v2_baskets`,
on every 500ms poll) and grading's counterfactual replay (`grading.simulate_basket`, on 1m
premium candle closes). The teacher enters and exits the WHOLE basket together and runs ~1:1 in
rupees at the basket level, so:

  T = basket_tp_sl_pct x basket cost            (cost = sum(entry premium x qty))
  MTM = sum(leg exit-side value - entry) x qty  (exit side = BID for these long options, LTP
                                                 fallback — matches fill_model BID_ASK)
  CLOSE ALL when MTM <= -T (BASKET_STOP) or MTM >= +T (BASKET_TARGET).

Round-number hold (`round_hold_enabled`): at MTM >= 0.9T, if on the MAJORITY of the traded
indices the spot is within `round_hold_points` of the next round number IN the trade's direction
(above for CE, below for PE), hold for the touch instead of booking at +T. While holding, exit on
the first touch of any of those round numbers, or when MTM gives back to 0.75T, or after
`round_hold_max_min` minutes — whichever comes first (all three book as BASKET_TARGET; the
round-hold sub-reason is logged). Backstop: at `basket_time_exit` (11:30) close all (BASKET_TIME).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS, parse_hhmm


@dataclass
class LegQuote:
    """One open leg's live state."""

    index: str
    qty: int
    entry_price: float
    ltp: float | None = None
    bid: float | None = None


@dataclass
class RoundHoldState:
    """Per-basket round-hold state (in-memory; carried across polls by the caller)."""

    active: bool = False
    started_at: datetime | None = None
    targets: dict[str, float] = field(default_factory=dict)  # index -> round level to touch
    activated_mtm: float | None = None
    used: bool = False  # one activation per basket


@dataclass
class BasketDecision:
    action: str  # "HOLD" | "CLOSE"
    reason: str | None = None  # target | stop | time | round_touch | round_giveback | round_timeout
    event: str | None = None  # "round_hold_activated" when a hold starts this poll
    detail: dict = field(default_factory=dict)

    @property
    def exit_reason(self) -> str | None:
        """The trades.exit_reason this decision books (ExitReason value)."""
        if self.action != "CLOSE":
            return None
        if self.reason == "stop":
            return "BASKET_STOP"
        if self.reason == "time":
            return "BASKET_TIME"
        return "BASKET_TARGET"


def leg_exit_price(leg: LegQuote, use_book: bool = True) -> float | None:
    """Exit-side value of a long option leg: the bid when valid (BID_ASK), else LTP."""
    if use_book and leg.bid is not None and leg.bid > 0:
        return float(leg.bid)
    if leg.ltp is not None and leg.ltp > 0:
        return float(leg.ltp)
    return None


def basket_mtm(legs: list[LegQuote], use_book: bool = True) -> tuple[float | None, float]:
    """(MTM, cost). MTM is None when any leg has no usable quote (never decide on a partial basket)."""
    cost = sum(float(leg.entry_price) * leg.qty for leg in legs)
    mtm = 0.0
    for leg in legs:
        px = leg_exit_price(leg, use_book)
        if px is None:
            return None, cost
        mtm += (px - float(leg.entry_price)) * leg.qty
    return mtm, cost


def next_round_in_direction(spot: float, step: float, direction: str) -> float:
    """The next round number strictly ahead of `spot` in the trade direction (CE up / PE down)."""
    if direction == "CE":
        return float(math.floor(spot / step) * step + step)
    return float(math.ceil(spot / step) * step - step)


def near_round_targets(spots: dict[str, float], direction: str, params: dict) -> dict[str, float]:
    """{index: round level} for indices within `round_hold_points` of the next round ahead."""
    pts = params.get("round_hold_points") or INTRADAY_HUNTER_V2_DEFAULTS["round_hold_points"]
    steps = params.get("round_step") or INTRADAY_HUNTER_V2_DEFAULTS["round_step"]
    out: dict[str, float] = {}
    for idx, spot in spots.items():
        if spot is None or idx not in steps:
            continue
        r = next_round_in_direction(float(spot), float(steps[idx]), direction)
        if abs(r - float(spot)) <= float(pts.get(idx, 0)):
            out[idx] = r
    return out


def _touched(spots: dict[str, float], targets: dict[str, float], direction: str) -> list[str]:
    hit = []
    for idx, lvl in targets.items():
        s = spots.get(idx)
        if s is None:
            continue
        if (direction == "CE" and s >= lvl) or (direction == "PE" and s <= lvl):
            hit.append(idx)
    return hit


def evaluate_basket(
    *,
    mtm: float | None,
    cost: float,
    direction: str,
    spots: dict[str, float],
    traded_indices: list[str],
    now: datetime,
    state: RoundHoldState,
    params: dict,
) -> BasketDecision:
    """One poll of the basket rules. Mutates `state` (round hold); returns the decision."""
    p = {**INTRADAY_HUNTER_V2_DEFAULTS, **(params or {})}
    T = float(p["basket_tp_sl_pct"]) * float(cost)
    detail = {"mtm": None if mtm is None else round(mtm, 2), "T": round(T, 2), "cost": round(cost, 2)}
    time_exit = parse_hhmm(p["basket_time_exit"])

    if mtm is None or T <= 0:
        # No full quote this poll — only the time backstop may act.
        if now.time() >= time_exit:
            return BasketDecision("CLOSE", "time", detail=detail)
        return BasketDecision("HOLD", detail=detail)

    if mtm <= -T:
        state.active = False
        return BasketDecision("CLOSE", "stop", detail=detail)

    if state.active:
        held_min = (now - state.started_at).total_seconds() / 60 if state.started_at else 0.0
        detail.update({"round_targets": state.targets, "held_min": round(held_min, 2)})
        hit = _touched(spots, state.targets, direction)
        if hit:
            state.active = False
            return BasketDecision("CLOSE", "round_touch", detail={**detail, "touched": hit})
        if mtm <= float(p["round_hold_giveback_frac"]) * T:
            state.active = False
            return BasketDecision("CLOSE", "round_giveback", detail=detail)
        if held_min >= float(p["round_hold_max_min"]):
            state.active = False
            return BasketDecision("CLOSE", "round_timeout", detail=detail)
        if now.time() >= time_exit:
            state.active = False
            return BasketDecision("CLOSE", "time", detail=detail)
        return BasketDecision("HOLD", detail=detail)

    if (p.get("round_hold_enabled") and not state.used
            and mtm >= float(p["round_hold_activate_frac"]) * T):
        targets = near_round_targets(
            {i: spots.get(i) for i in set(traded_indices)}, direction, p
        )
        n = len(set(traded_indices))
        if n and len(targets) * 2 > n:  # strict majority of the traded indices
            state.active, state.used = True, True
            state.started_at = now
            state.targets = targets
            state.activated_mtm = mtm
            return BasketDecision("HOLD", event="round_hold_activated",
                                  detail={**detail, "round_targets": targets})

    if mtm >= T:
        return BasketDecision("CLOSE", "target", detail=detail)
    if now.time() >= time_exit:
        return BasketDecision("CLOSE", "time", detail=detail)
    return BasketDecision("HOLD", detail=detail)
