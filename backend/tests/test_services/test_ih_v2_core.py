"""Intraday Hunter v2 — pure core: level facts + opening type, gates, OI flow, basket exit,
Call 2 normalization, and grading counterfactuals on a synthetic premium path."""
from datetime import date, datetime, time

import pytest

from app.core.constants import IST
from app.services.intraday_hunter_v2 import basket, gates, grading, levels, oi_flow
from app.services.intraday_hunter_v2.decision import decision_time_for_candle, normalize_call2
from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS

D = date(2026, 10, 9)


def c(hhmm, o, h, l, cl):
    return {"ts": f"2026-10-09T{hhmm}:00+05:30", "open": o, "high": h, "low": l, "close": cl}


PREV = {"close": 25000.0, "high": 25080.0, "low": 24900.0}


# ───────────────────────────── level facts ─────────────────────────────
class TestLevelFacts:
    def test_pdh_break_up_and_pools(self):
        cs = [c("09:15", 25040, 25070, 25030, 25060), c("09:16", 25060, 25095, 25055, 25090)]
        f = levels.level_facts("NIFTY", PREV, cs)
        pdh = next(x for x in f["levels"] if x["name"] == "pdh")
        assert pdh["broken"] and pdh["broken_at"] == "09:16" and pdh["broken_dir"] == "up"
        assert "pdh" in f["pools_taken"]
        assert f["gap_pct"] == pytest.approx(0.16, abs=1e-3)
        # 25100 round is 10 pts above and untaken → nearest pool ahead for CE
        assert f["nearest_ahead"]["CE"]["name"] == "round_above"
        assert f["nearest_ahead"]["CE"]["price"] == 25100.0
        assert levels.pdh_pdl_break_side(f) == "CE"

    def test_gap_open_above_pdh_counts_as_broken_at_open(self):
        f = levels.level_facts("NIFTY", PREV, [c("09:15", 25100, 25120, 25090, 25110)])
        pdh = next(x for x in f["levels"] if x["name"] == "pdh")
        assert pdh["broken"] and pdh["broken_at"] == "09:15"

    def test_prev_close_cross_relative_to_open(self):
        cs = [c("09:15", 24950, 24960, 24940, 24955), c("09:16", 24955, 25005, 24950, 25002)]
        pc = next(x for x in levels.level_facts("NIFTY", PREV, cs)["levels"] if x["name"] == "prev_close")
        assert pc["broken"] and pc["broken_dir"] == "up" and pc["broken_at"] == "09:16"

    def test_opening_range_only_after_0919(self):
        cs = [c(f"09:{m}", 25000, 25010 + m, 24990, 25000) for m in range(15, 19)]
        assert levels.level_facts("NIFTY", PREV, cs)["opening_range"] is None
        cs.append(c("09:19", 25000, 25040, 24980, 25000))
        cs.append(c("09:20", 25000, 25050, 24995, 25045))
        f = levels.level_facts("NIFTY", PREV, cs)
        assert f["opening_range"] == {"high": 25040, "low": 24980}
        orh = next(x for x in f["levels"] if x["name"] == "or_high")
        assert orh["broken"] and orh["broken_at"] == "09:20"

    def test_drawn_levels_and_describe(self):
        f = levels.level_facts("NIFTY", PREV, [c("09:15", 25000, 25010, 24990, 25005)],
                               drawn_levels=[25030, "bad"])
        assert any(x["name"] == "drawn_1" and x["price"] == 25030 for x in f["levels"])
        assert any("drawn_1" in ln for ln in levels.describe_facts(f))

    def test_no_candles(self):
        assert levels.level_facts("NIFTY", PREV, []) is None

    @pytest.mark.parametrize("gaps,expected", [
        ({"NIFTY": 0.3, "BANKNIFTY": 0.1, "SENSEX": 0.2}, "gap_up"),
        ({"NIFTY": -0.2, "BANKNIFTY": -0.1, "SENSEX": -0.2}, "gap_down"),
        ({"NIFTY": 0.2, "BANKNIFTY": -0.2, "SENSEX": 0.05}, "flat"),
        ({"NIFTY": 0.15}, "gap_up"),
        ({}, "flat"),
    ])
    def test_opening_type(self, gaps, expected):
        assert levels.opening_type(gaps) == expected

    def test_rule_a_two_of_three(self):
        up = levels.level_facts("NIFTY", PREV, [c("09:15", 25090, 25100, 25085, 25095)])
        dn = levels.level_facts("NIFTY", PREV, [c("09:15", 24890, 24895, 24880, 24885)])
        flat = levels.level_facts("NIFTY", PREV, [c("09:15", 25000, 25010, 24990, 25000)])
        assert levels.rule_a_side({"NIFTY": up, "BANKNIFTY": up, "SENSEX": dn}) == "CE"
        assert levels.rule_a_side({"NIFTY": up, "BANKNIFTY": dn, "SENSEX": flat}) is None

    def test_round_levels(self):
        assert levels.round_levels(25040, 100) == (25100.0, 25000.0)
        assert levels.round_levels(25000, 100) == (25100.0, 25000.0)


# ───────────────────────────── gates ─────────────────────────────
class TestGates:
    PLAN = {"gap_up_side": "CE", "flat_side": "none", "gap_down_side": "PE"}

    def test_plan_side_for_opening(self):
        assert gates.plan_side_for_opening(self.PLAN, "gap_up") == "CE"
        assert gates.plan_side_for_opening(self.PLAN, "flat") is None
        assert gates.plan_side_for_opening(None, "gap_up") is None

    def test_shadow_only_never_blocks(self):
        g = gates.compute_gates("CE", "PE", "PE")
        assert g["plan"]["verdict"] == "OPPOSES" and g["plan"]["would_block"]
        assert g["oi"]["would_block"] and not g["blocked"]

    def test_enforced_blocks_only_when_opposing(self):
        assert gates.compute_gates("CE", "PE", None, enforce_plan=True)["blocked"]
        assert not gates.compute_gates("CE", "CE", "PE", enforce_plan=True)["blocked"]
        assert gates.compute_gates("CE", None, "PE", enforce_oi=True)["blocked"]

    def test_na_when_missing(self):
        g = gates.compute_gates(None, "CE", "PE")
        assert g["plan"]["verdict"] == "NA" and g["oi"]["verdict"] == "NA"


# ───────────────────────────── OI flow ─────────────────────────────
class TestOIFlow:
    def test_index_flow_sign(self):
        # puts written faster than calls → positive → CE
        assert oi_flow.index_flow({"CE": 100, "PE": 100}, {"CE": 101, "PE": 110}) == pytest.approx(0.045)

    def test_flow_from_sums_mean_and_side(self):
        sums = {
            "NIFTY": {"09:16": {"CE": 100, "PE": 100}, "09:19": {"CE": 120, "PE": 100}},
            "BANKNIFTY": {"09:16": {"CE": 100, "PE": 100}, "09:18": {"CE": 110, "PE": 100}},
            "SENSEX": {"09:16": {"CE": 100}},  # incomplete → excluded
        }
        res = oi_flow.flow_from_sums(sums, "09:16", "09:19")
        assert res["per_index"]["SENSEX"] is None
        assert res["flow"] == pytest.approx((-0.1 + -0.05) / 2)
        assert res["side"] == "PE" and res["t1"] == {"NIFTY": "09:19", "BANKNIFTY": "09:18"}

    def test_deadband(self):
        assert oi_flow.combine_flow({"N": 0.001}, deadband=0.002)["side"] is None
        assert oi_flow.combine_flow({})["flow"] is None


# ───────────────────────────── basket exit ─────────────────────────────
P = dict(INTRADAY_HUNTER_V2_DEFAULTS)


def legs(prices, bids=None):
    """Three legs, entry 100 each, qty 10 → cost 3000, T = 600."""
    bids = bids or [None] * len(prices)
    return [basket.LegQuote(index=i, qty=10, entry_price=100.0, ltp=p, bid=b)
            for i, p, b in zip(("NIFTY", "BANKNIFTY", "SENSEX"), prices, bids)]


def at(hhmm, sec=0):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 9, h, m, sec, tzinfo=IST)


def ev(mtm, cost=3000.0, now="09:40", spots=None, state=None, direction="CE", params=None):
    return basket.evaluate_basket(
        mtm=mtm, cost=cost, direction=direction,
        spots=spots or {"NIFTY": 25040, "BANKNIFTY": 56240, "SENSEX": 82240},
        traded_indices=["NIFTY", "BANKNIFTY", "SENSEX"], now=at(now),
        state=state if state is not None else basket.RoundHoldState(), params=params or P,
    )


class TestBasketExit:
    def test_mtm_uses_bid_side(self):
        mtm, cost = basket.basket_mtm(legs([130, 130, 130], bids=[120, 120, 120]))
        assert cost == 3000 and mtm == pytest.approx(600)  # bid 120, not LTP 130
        mtm_ltp, _ = basket.basket_mtm(legs([130, 130, 130], bids=[120, 120, 120]), use_book=False)
        assert mtm_ltp == pytest.approx(900)

    def test_bid_fallback_to_ltp_and_missing_quote(self):
        mtm, _ = basket.basket_mtm(legs([110, 110, 110], bids=[0, None, 105]))
        assert mtm == pytest.approx(100 + 100 + 50)
        assert basket.basket_mtm(legs([110, None, 110]))[0] is None

    def test_plus_T_closes_all_as_target(self):
        d = ev(600.0)
        assert d.action == "CLOSE" and d.exit_reason == "BASKET_TARGET" and d.reason == "target"

    def test_minus_T_closes_all_as_stop(self):
        d = ev(-600.0)
        assert d.action == "CLOSE" and d.exit_reason == "BASKET_STOP"

    def test_hold_inside_band(self):
        assert ev(300.0).action == "HOLD"
        assert ev(-599.0).action == "HOLD"

    def test_time_backstop_1130(self):
        assert ev(100.0, now="11:29").action == "HOLD"
        d = ev(100.0, now="11:30")
        assert d.action == "CLOSE" and d.exit_reason == "BASKET_TIME"
        # even with no full quote, the backstop still fires
        assert ev(None, now="11:30").exit_reason == "BASKET_TIME"
        assert ev(None, now="10:00").action == "HOLD"

    def test_round_hold_needs_majority_near_round(self):
        # NIFTY 25092 (8 pts under 25100), BN 56480 (20 under 56500), SENSEX far → 2/3 majority
        near = {"NIFTY": 25092, "BANKNIFTY": 56480, "SENSEX": 82200}
        st = basket.RoundHoldState()
        d = ev(560.0, spots=near, state=st)  # 0.93T
        assert d.action == "HOLD" and d.event == "round_hold_activated"
        assert st.active and st.targets == {"NIFTY": 25100.0, "BANKNIFTY": 56500.0}
        # only 1/3 near → no hold, normal target at +T
        far = {"NIFTY": 25092, "BANKNIFTY": 56400, "SENSEX": 82200}
        st2 = basket.RoundHoldState()
        assert ev(560.0, spots=far, state=st2).action == "HOLD" and not st2.active
        assert ev(600.0, spots=far, state=st2).exit_reason == "BASKET_TARGET"

    def test_round_hold_direction_pe(self):
        near_pe = {"NIFTY": 25008, "BANKNIFTY": 56020, "SENSEX": 82300}  # rounds BELOW
        st = basket.RoundHoldState()
        d = ev(560.0, spots=near_pe, state=st, direction="PE")
        assert st.active and st.targets == {"NIFTY": 25000.0, "BANKNIFTY": 56000.0}
        assert d.event == "round_hold_activated"

    def _activated(self):
        st = basket.RoundHoldState()
        ev(560.0, spots={"NIFTY": 25092, "BANKNIFTY": 56480, "SENSEX": 82200}, state=st, now="09:40")
        assert st.active
        return st

    def test_round_hold_holds_past_T_then_exits_on_touch(self):
        st = self._activated()
        held = ev(700.0, spots={"NIFTY": 25098, "BANKNIFTY": 56490, "SENSEX": 82200},
                  state=st, now="09:41")
        assert held.action == "HOLD"  # +T passed but still holding for the touch
        d = ev(720.0, spots={"NIFTY": 25100.5, "BANKNIFTY": 56490, "SENSEX": 82200},
               state=st, now="09:42")
        assert d.action == "CLOSE" and d.reason == "round_touch" and d.exit_reason == "BASKET_TARGET"
        assert d.detail["touched"] == ["NIFTY"]

    def test_round_hold_giveback_cap(self):
        st = self._activated()
        d = ev(450.0, state=st, now="09:41")  # 0.75T = 450
        assert d.action == "CLOSE" and d.reason == "round_giveback"

    def test_round_hold_time_cap(self):
        st = self._activated()
        assert ev(560.0, state=st, now="09:44").action == "HOLD"
        d = ev(560.0, state=st, now="09:45")  # 5 minutes after 09:40
        assert d.action == "CLOSE" and d.reason == "round_timeout"

    def test_round_hold_only_once(self):
        st = self._activated()
        ev(450.0, state=st, now="09:41")  # giveback exit
        st.active = False
        assert ev(560.0, spots={"NIFTY": 25092, "BANKNIFTY": 56480, "SENSEX": 0},
                  state=st, now="09:50").event is None

    def test_round_hold_disabled(self):
        st = basket.RoundHoldState()
        d = ev(560.0, spots={"NIFTY": 25092, "BANKNIFTY": 56480, "SENSEX": 82490}, state=st,
               params={**P, "round_hold_enabled": False})
        assert d.action == "HOLD" and not st.active

    def test_next_round(self):
        assert basket.next_round_in_direction(25100, 100, "CE") == 25200
        assert basket.next_round_in_direction(25100, 100, "PE") == 25000
        assert basket.next_round_in_direction(25150, 100, "PE") == 25100


# ───────────────────────────── Call 2 normalization ─────────────────────────────
class TestCall2Normalize:
    def test_llm_failure_wait_then_skip_at_deadline(self):
        assert normalize_call2(None, at_deadline=False)["decision"] == "WAIT"
        r = normalize_call2(None, at_deadline=True)
        assert r["decision"] == "SKIP" and r["skip_reason_code"] == "OTHER"

    def test_wait_at_deadline_is_skip(self):
        r = normalize_call2({"decision": "WAIT", "confidence": 40}, at_deadline=True)
        assert r["decision"] == "SKIP" and r["skip_reason_code"] == "NO_POOL_BROKEN_BY_DEADLINE"

    def test_enter_needs_direction(self):
        assert normalize_call2({"decision": "ENTER", "direction": None}, at_deadline=False)["decision"] == "WAIT"
        r = normalize_call2({"decision": "enter", "direction": "pe"}, at_deadline=False)
        assert r["decision"] == "ENTER" and r["direction"] == "PE" and r["skip_reason_code"] is None

    def test_skip_requires_reason_code(self):
        r = normalize_call2({"decision": "SKIP", "skip_reason_code": "bored"}, at_deadline=False)
        assert r["skip_reason_code"] == "OTHER"
        r = normalize_call2({"decision": "SKIP", "skip_reason_code": "TWO_SIDED_CHOP",
                             "direction": "CE"}, at_deadline=False)
        assert r["skip_reason_code"] == "TWO_SIDED_CHOP" and r["direction"] is None

    def test_decision_time_is_candle_plus_one(self):
        assert decision_time_for_candle(datetime(2026, 10, 9, 9, 15, 0, 400000, tzinfo=IST)) == time(9, 16)


# ───────────────────────────── grading ─────────────────────────────
class TestGrading:
    def test_first_touch_and_clean_side(self):
        cs = [c("09:16", 100, 100.1, 99.9, 100), c("09:17", 100, 100.3, 99.95, 100.2)]
        assert grading.first_touch(cs, 100.0, "CE", 0.25, 0.20) == "target"
        assert grading.first_touch(cs, 100.0, "PE", 0.25, 0.20) == "stop"
        assert grading.clean_side(cs, 100.0, 0.25, 0.20) == "CE"
        tie = [c("09:16", 100, 100.3, 99.7, 100)]
        assert grading.first_touch(tie, 100.0, "CE", 0.25, 0.20) == "stop"  # same-candle tie
        assert grading.clean_side([c("09:16", 100, 100.1, 99.9, 100)], 100.0, 0.25, 0.2) is None

    def test_majority(self):
        assert grading.majority({"a": "CE", "b": "CE", "c": "PE"}) == "CE"
        assert grading.majority({"a": "CE", "b": None, "c": "PE"}) is None

    def _path(self, closes):
        return {f"09:{15 + i}": v for i, v in enumerate(closes)}

    def test_simulate_basket_target_on_synthetic_premiums(self):
        # decision 09:16 → entry = 09:15 close (100); +20% basket at 09:18 close → BASKET_TARGET
        legs_ = [{"index": "NIFTY", "qty": 10, "closes": self._path([100, 105, 112, 121, 90])},
                 {"index": "SENSEX", "qty": 10, "closes": self._path([100, 104, 110, 120, 90])}]
        idx = {"NIFTY": self._path([25000] * 5), "SENSEX": self._path([82000] * 5)}
        res = grading.simulate_basket("09:16", "CE", legs_, idx, P, D)
        assert res["exit_reason"] == "BASKET_TARGET" and res["exit_time"] == "09:19"
        assert res["pnl"] == pytest.approx(410.0) and res["entry_cost"] == 2000

    def test_simulate_basket_stop_and_time(self):
        legs_ = [{"index": "NIFTY", "qty": 10, "closes": self._path([100, 95, 85, 79])}]
        idx = {"NIFTY": self._path([25000] * 4)}
        res = grading.simulate_basket("09:16", "CE", legs_, idx, P, D)
        assert res["exit_reason"] == "BASKET_STOP" and res["pnl"] == pytest.approx(-210.0)
        flat = {"11:27": 100, "11:28": 101, "11:29": 102, "11:30": 103}
        res = grading.simulate_basket("11:28", "CE", [{"index": "NIFTY", "qty": 1, "closes": flat}],
                                      {"NIFTY": {}}, P, D)
        assert res["exit_reason"] == "BASKET_TIME" and res["exit_time"] == "11:30"

    def test_simulate_basket_missing_entry(self):
        assert grading.simulate_basket("09:16", "CE", [{"index": "NIFTY", "qty": 1, "closes": {}}],
                                       {}, P, D) is None

    def test_basket_legs_for_shape(self):
        contracts = {"BANKNIFTY": [{"strike": 56000.0, "type": "CE", "symbol": "BN56000CE"},
                                   {"strike": 56100.0, "type": "CE", "symbol": "BN56100CE"},
                                   {"strike": 55900.0, "type": "PE", "symbol": "BN55900PE"},
                                   {"strike": 56000.0, "type": "PE", "symbol": "BN56000PE"}]}
        prem = {s: {"09:15": 100.0} for s in ("BN56000CE", "BN56100CE", "BN55900PE", "BN56000PE")}
        ce = grading.basket_legs_for("CE", {"BANKNIFTY": 56020}, contracts, prem, {"BANKNIFTY": [0, -1]})
        assert [l["symbol"] for l in ce] == ["BN56000CE", "BN56100CE"]  # ATM + OTM-1 (up)
        pe = grading.basket_legs_for("PE", {"BANKNIFTY": 56020}, contracts, prem, {"BANKNIFTY": [0, -1]})
        assert [l["symbol"] for l in pe] == ["BN56000PE", "BN55900PE"]  # ATM + OTM-1 (down)

    def test_isotonic_monotone(self):
        m = grading.isotonic_fit([10, 20, 30, 40], [1, 0, 1, 1])
        ps = [b["p"] for b in m]
        assert ps == sorted(ps) and m[0]["n"] == 2

    def test_ledger(self):
        g = [{"trading_date": f"2026-10-0{i}", "market": {"clean_side": "CE"},
              "arms": {"rule_a": {"side": "CE", "cf": {"pnl": p}}, "plan_side": {"side": None}},
              "gates": {"v2_actual": {"pnl": 10}, "plan_enforced": {"pnl": 0}}}
             for i, p in enumerate([100, -50, 200, 50], start=1)]
        led = grading.compute_ledger(g, 20)
        a = led["rule_a"]
        assert a["n"] == 4 and a["right_side_pct"] == 100.0 and a["win_pct"] == 75.0
        assert a["mean_cf_pnl"] == 75.0 and a["first_half_mean"] == 25.0 and a["second_half_mean"] == 125.0
        assert led["plan_side"]["n"] == 0
        assert led["_gates"]["v2_actual"] == 40 and led["_gates"]["plan_enforced"] == 0

    def test_lesson(self):
        lesson = grading.build_lesson(D, {"clean_side": "PE", "opening": "gap_down"},
                                      {"side": "PE", "pnl": 120000}, {"side": "CE", "yolo_net_pnl": -3000},
                                      {"rule_a": {"side": "PE"}, "v2_llm": {"side": "CE"}},
                                      {"NIFTY": ["pdl"]})
        assert lesson["arms_right"] == ["rule_a"] and "pdl" in lesson["one_line_takeaway"]
        assert lesson["v2"] == {"side": "CE", "result": -3000}
