"""IH v2 weekly-review approval flow: plan validation, the per-proposal status chain, challenger
scoring vs the champion, promotion gating, and the DB lifecycle (review → approve → agent →
APPLIED → graded days → promote)."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.intraday_hunter_v2 import apply, grading
from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS

CHAMP = dict(INTRADAY_HUNTER_V2_DEFAULTS, basket_tp_sl_pct=0.15)


def plan(**kw):
    base = {"evidence_holds": True, "kind": "param", "params_override": {}, "prompt_addendum": "",
            "build_brief": "", "reasoning": "r", "recheck": "n=22 t=2.1"}
    base.update(kw)
    return base


class TestValidatePlan:
    def test_evidence_no_longer_holds(self):
        v = apply.validate_plan(plan(evidence_holds=False, params_override={"basket_tp_sl_pct": 0.2}), CHAMP)
        assert not v["ok"] and v["status"] == "NEEDS_REVIEW" and "evidence" in v["reason"]

    def test_llm_failure(self):
        assert apply.validate_plan(None, CHAMP)["status"] == "NEEDS_REVIEW"

    def test_counterfactual_param(self):
        v = apply.validate_plan(plan(params_override={"basket_t_mode": "rupees"}), CHAMP)
        assert v["ok"] and v["status"] == "ANALYSED" and v["mode"] == "counterfactual"
        assert v["override"] == {"basket_t_mode": "rupees"}

    def test_gate_is_counterfactual(self):
        v = apply.validate_plan(plan(kind="gate", params_override={"enforce_plan_gate": True}), CHAMP)
        assert v["mode"] == "counterfactual"

    def test_prompt_addendum_is_shadow_call2(self):
        v = apply.validate_plan(plan(kind="prompt", prompt_addendum="Wait for a second index to confirm."), CHAMP)
        assert v["mode"] == "shadow_call2"
        assert v["override"] == {"call2_prompt_addendum": "Wait for a second index to confirm."}

    def test_call2_input_param_is_shadow_call2(self):
        v = apply.validate_plan(plan(params_override={"call2_deadline": "09:30"}), CHAMP)
        assert v["mode"] == "shadow_call2"

    def test_code_needs_brief(self):
        assert apply.validate_plan(plan(kind="code", build_brief="Add X to Y; test Z."), CHAMP)["mode"] is None
        assert apply.validate_plan(plan(kind="code", build_brief="Add X to Y."), CHAMP)["status"] == "ANALYSED"
        assert apply.validate_plan(plan(kind="code"), CHAMP)["status"] == "NEEDS_REVIEW"

    @pytest.mark.parametrize("override,frag", [
        ({}, "no concrete"),
        ({"stop_hunt_magic": 1}, "unknown"),
        ({"minute_log_end": "11:00"}, "shared data"),
        ({"basket_tp_sl_pct": "0.2"}, "wrong value type"),
        ({"enforce_plan_gate": 1}, "wrong value type"),
        ({"basket_tp_sl_pct": 0.15}, "identical"),
    ])
    def test_rejections(self, override, frag):
        v = apply.validate_plan(plan(params_override=override), CHAMP)
        assert v["status"] == "NEEDS_REVIEW" and frag in v["reason"]

    def test_unknown_kind(self):
        assert apply.validate_plan(plan(kind="vibes", params_override={"basket_tp_sl_pct": 0.2}), CHAMP)["status"] == "NEEDS_REVIEW"


RUPEES_CHAMP = dict(CHAMP, basket_t_mode="rupees")
NO_HOLD_CHAMP = dict(CHAMP, round_hold_enabled=False)


class TestInertOverrides:
    """An override with no effect under the merged champion+override params → NEEDS_REVIEW."""

    def test_pct_under_rupees_mode_is_inert(self):
        v = apply.validate_plan(plan(params_override={"basket_tp_sl_pct": 0.12}), RUPEES_CHAMP)
        assert v["status"] == "NEEDS_REVIEW" and "basket_tp_sl_pct" in v["reason"]

    def test_rupees_per_lot_under_pct_mode_is_inert(self):
        v = apply.validate_plan(plan(params_override={"rupees_per_lot": {"NIFTY": 2000}}), CHAMP)
        assert v["status"] == "NEEDS_REVIEW" and "rupees_per_lot" in v["reason"]

    def test_round_hold_tunables_inert_when_hold_disabled(self):
        v = apply.validate_plan(plan(params_override={"round_hold_max_min": 8, "round_hold_activate_frac": 0.8}),
                                NO_HOLD_CHAMP)
        assert v["status"] == "NEEDS_REVIEW"
        assert "round_hold_activate_frac" in v["reason"] and "round_hold_max_min" in v["reason"]

    def test_override_disabling_hold_makes_its_tunables_inert(self):
        v = apply.validate_plan(plan(params_override={"round_hold_enabled": False, "round_hold_max_min": 8}), CHAMP)
        assert v["status"] == "NEEDS_REVIEW" and v["reason"].startswith("['round_hold_max_min']")

    def test_mode_switch_with_pct_is_valid(self):
        v = apply.validate_plan(plan(params_override={"basket_t_mode": "pct", "basket_tp_sl_pct": 0.12}),
                                RUPEES_CHAMP)
        assert v["ok"] and v["mode"] == "counterfactual"
        assert v["override"] == {"basket_t_mode": "pct", "basket_tp_sl_pct": 0.12}

    def test_enabling_hold_with_tunable_is_valid(self):
        v = apply.validate_plan(plan(params_override={"round_hold_enabled": True, "round_hold_max_min": 8}),
                                NO_HOLD_CHAMP)
        assert v["ok"] and v["mode"] == "counterfactual"

    def test_rupees_per_lot_under_rupees_mode_is_valid(self):
        v = apply.validate_plan(plan(params_override={"rupees_per_lot": {"NIFTY": 2000}}), RUPEES_CHAMP)
        assert v["ok"] and v["mode"] == "counterfactual"


class TestTransitionsAndActions:
    def test_actions_by_status(self):
        assert apply.allowed_actions("PROPOSED", None) == ["approve", "reject"]
        assert apply.allowed_actions("NEEDS_REVIEW", None) == ["reject", "analyse"]
        assert apply.allowed_actions("APPROVED", None) == ["analyse"]
        assert apply.allowed_actions("ANALYSED", None) == []
        assert apply.allowed_actions("REJECTED", None) == []
        assert apply.allowed_actions("APPLIED", {"can_promote": False, "can_retire": False}) == []
        assert apply.allowed_actions("APPLIED", {"can_promote": True, "can_retire": False}) == ["promote"]
        assert apply.allowed_actions("APPLIED", {"can_promote": False, "can_retire": True}) == ["retire"]

    def test_nothing_while_agent_runs(self):
        assert apply.allowed_actions("APPROVED", None, agent_busy=True) == []
        assert apply.allowed_actions("NEEDS_REVIEW", None, agent_busy=True) == []

    @pytest.mark.asyncio
    async def test_agent_single_flight(self):
        import asyncio as aio

        gate = aio.Event()

        async def slow(_pid):
            await gate.wait()

        with patch.object(apply, "run_apply_agent_by_id", new=slow):
            t1 = apply.start_apply_agent("p1")
            assert t1 is not None and apply.agent_running("p1")
            assert apply.start_apply_agent("p1") is None
            gate.set()
            await t1
            await aio.sleep(0)
        assert not apply.agent_running("p1")

    def test_check_transition(self):
        apply.check_transition(SimpleNamespace(status="PROPOSED"), "approve")
        with pytest.raises(apply.TransitionError):
            apply.check_transition(SimpleNamespace(status="APPLIED"), "approve")
        with pytest.raises(apply.TransitionError):
            apply.check_transition(SimpleNamespace(status="PROPOSED"), "promote")

    def test_next_trading_day_skips_weekend(self):
        assert apply.next_trading_day(date(2026, 10, 9)) == date(2026, 10, 12)  # Fri → Mon
        assert apply.next_trading_day(date(2026, 10, 12)) == date(2026, 10, 13)


def _grades(diffs, start=date(2026, 11, 2), champ=1000.0):
    out, d = [], start
    for x in diffs:
        out.append({"trading_date": d, "arms": {"v2_llm": {"cf": {"pnl": champ}},
                                                "ch_c1": {"cf": {"pnl": champ + x}}}})
        d += timedelta(days=1)
    return out


class TestChallengerStats:
    def test_under_20_days_not_eligible(self):
        s = apply.challenger_stats(_grades([500] * 19), "c1", date(2026, 11, 2))
        assert s["days"] == 19 and not s["eligible"] and not s["can_promote"] and not s["can_retire"]

    def test_ahead_both_halves_can_promote(self):
        s = apply.challenger_stats(_grades([300, -100] * 10), "c1", date(2026, 11, 2))
        assert s["days"] == 20 and s["eligible"] and s["can_promote"] and not s["can_retire"]
        assert s["diff_total"] == 2000 and s["first_half_diff"] == 1000 and s["second_half_diff"] == 1000

    def test_behind_or_split_offers_retire(self):
        assert apply.challenger_stats(_grades([-200] * 20), "c1", date(2026, 11, 2))["can_retire"]
        split = apply.challenger_stats(_grades([900] * 10 + [-100] * 10), "c1", date(2026, 11, 2))
        assert split["diff_total"] > 0 and not split["can_promote"] and split["can_retire"]

    def test_window_and_missing_arms_count_as_flat(self):
        g = _grades([100] * 5)
        g[2]["arms"] = {}  # neither traded that day
        s = apply.challenger_stats(g, "c1", date(2026, 11, 3), ended_on=date(2026, 11, 5))
        assert s["days"] == 3 and s["diff_total"] == 200


class TestChallengerEntry:
    V2 = {"side": "PE", "decision_at": "09:18"}
    OPP = {"plan": {"would_block": True}, "oi": {"would_block": False}}

    def test_counterfactual_rides_v2(self):
        assert grading.challenger_entry("counterfactual", {"basket_tp_sl_pct": 0.2}, self.V2, self.OPP, None, None) == (self.V2, None)

    def test_enforced_gate_blocks(self):
        e, blocked = grading.challenger_entry("counterfactual", {"enforce_plan_gate": True}, self.V2, self.OPP, None, None)
        assert e is None and blocked == "plan"
        assert grading.challenger_entry("counterfactual", {"enforce_oi_gate": True}, self.V2, self.OPP, None, None)[0] == self.V2

    def test_no_v2_trade(self):
        assert grading.challenger_entry("counterfactual", {}, None, None, None, None) == (None, None)

    def test_shadow_uses_own_decision(self):
        assert grading.challenger_entry("shadow_call2", {}, self.V2, None, "CE", "09:20") == (
            {"side": "CE", "decision_at": "09:20"}, None)
        assert grading.challenger_entry("shadow_call2", {}, self.V2, None, None, None) == (None, None)


class TestPromptsAndText:
    def test_apply_prompt_lists_key_classes(self):
        p = SimpleNamespace(change="T to rupees", kind="param", evidence="n=20 t=2", expected_effect="e",
                            risk="r", user_note="go")
        txt = apply.build_apply_prompt(p, {"last_20": {}}, CHAMP, [])
        assert "basket_t_mode" in txt and "minute_log_end" in txt and "evidence_holds" in txt
        assert '"user_note": "go"' in txt
        assert "inert under the effective mode are rejected" in txt

    def test_telegram_applied(self):
        p = SimpleNamespace(status="APPLIED", change="T to rupees", challenger_id="c1",
                            challenger_mode="counterfactual", started_on=date(2026, 10, 12),
                            params_override={"basket_t_mode": "rupees"}, apply_plan={"recheck": "n=20"})
        t = apply.telegram_text(p)
        assert "challenger started" in t and "c1" in t and "rupees" in t

    def test_call2_addendum_suffix(self):
        from app.services.intraday_hunter_v2.decision import _addendum

        assert _addendum({"call2_prompt_addendum": ""}) == ""
        assert _addendum({"call2_prompt_addendum": " Be patient. "}).endswith("ADDITIONAL INSTRUCTIONS:\nBe patient.")


# ───────────────────────────── DB-backed (local Postgres) ─────────────────────────────
WEEK = date(2099, 1, 10)


@pytest.fixture
async def dbf():
    """Fresh-engine session factory; skips without local Postgres + the proposals migration."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings

    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with eng.connect() as c:
            await c.execute(text("SELECT 1 FROM ih_v2_proposals LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        await eng.dispose()
        pytest.skip(f"local Postgres with the proposals migration not available: {e}")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        saved = (await s.execute(text(
            "SELECT parameters FROM strategy_configs WHERE strategy_name='intraday_hunter_v2'"))).scalar()
    yield factory
    async with factory() as s:
        await s.execute(text("DELETE FROM ih_weekly_reviews WHERE week_ending = :w"), {"w": WEEK})
        await s.execute(text("DELETE FROM ih_day_grades WHERE trading_date >= '2099-01-01'"))
        await s.execute(text("UPDATE strategy_configs SET parameters = CAST(:p AS jsonb) "
                             "WHERE strategy_name='intraday_hunter_v2'"),
                        {"p": __import__("json").dumps(saved)})
        await s.commit()
    await eng.dispose()


REVIEW_JSON = {"summary": "s", "proposals": [
    {"change": "basket_t_mode → rupees", "kind": "param", "evidence": "n=22 t=2.3",
     "expected_effect": "steadier exits", "risk": "fewer big wins"},
    {"change": "prompt: wait for 2 indices", "kind": "prompt", "evidence": "n=21 t=1.9",
     "expected_effect": "fewer chops", "risk": "late entries"},
]}


@pytest.mark.asyncio
async def test_lifecycle_review_to_promotion(dbf):
    from sqlalchemy import select

    from app.models.ih_v2 import IhDayGrade, IhV2Proposal, IhWeeklyReview
    from app.models.strategy_config import StrategyConfig

    async with dbf() as s:
        review = IhWeeklyReview(week_ending=WEEK, ledger={}, proposal=REVIEW_JSON, status="PROPOSED")
        s.add(review)
        await s.flush()
        rows = await apply.sync_proposals(s, review)
        assert [r.status for r in rows] == ["PROPOSED", "PROPOSED"]
        p1, p2 = rows
        await apply.decide(s, p2, "reject", "not yet")
        await apply.decide(s, p1, "approve", "try it")
        # A re-run of the same week keeps the decided rows (only PROPOSED ones are replaced).
        assert await apply.sync_proposals(s, review) == []
        await s.commit()

    agent_plan = {"evidence_holds": True, "kind": "param", "params_override": {"basket_t_mode": "rupees"},
                  "prompt_addendum": "", "build_brief": "", "reasoning": "ok", "recheck": "n=22 t=2.3"}
    with patch.object(apply.llm_cli, "call_claude_json", new=AsyncMock(return_value=agent_plan)), \
         patch.object(grading, "ledger", new=AsyncMock(return_value={"last_20": {}})):
        async with dbf() as s:
            p = await s.get(IhV2Proposal, p1.id)
            await apply.run_apply_agent(s, p)
            await s.commit()
    assert p.status == "APPLIED" and p.challenger_mode == "counterfactual" and p.challenger_id
    assert [h["status"] for h in p.history] == ["PROPOSED", "APPROVED", "ANALYSED", "APPLIED"]
    assert apply.allowed_actions(p.status, None) == []

    async with dbf() as s:
        p = await s.get(IhV2Proposal, p1.id)
        p.started_on = date(2099, 1, 12)
        cid = p.challenger_id
        for i in range(20):
            s.add(IhDayGrade(trading_date=date(2099, 1, 12) + timedelta(days=i), status="FINAL",
                             arms={"v2_llm": {"cf": {"pnl": 1000.0}},
                                   f"ch_{cid}": {"cf": {"pnl": 1300.0}}}))
        await s.flush()
        stats = await apply.stats_for(s, p)
        assert stats["days"] == 20 and stats["can_promote"]
        assert "promote" in apply.allowed_actions(p.status, stats)
        live = await apply.active_challengers(s, date(2099, 1, 20))
        assert cid in {c.challenger_id for c in live}
        await apply.promote(s, p, "ship it")
        await s.commit()
        cfg = (await s.execute(select(StrategyConfig).where(
            StrategyConfig.strategy_name == "intraday_hunter_v2"))).scalar_one()
        assert cfg.parameters["basket_t_mode"] == "rupees"
        assert p.status == "PROMOTED" and p.ended_on is not None
        assert set(p.history[-1]["previous"]) == {"basket_t_mode"} and p.history[-1]["stats"]["can_promote"]
        with pytest.raises(apply.TransitionError):
            await apply.retire(s, p, None)


@pytest.mark.asyncio
async def test_agent_needs_review_then_rerun(dbf):
    from app.models.ih_v2 import IhV2Proposal, IhWeeklyReview

    async with dbf() as s:
        review = IhWeeklyReview(week_ending=WEEK, ledger={}, proposal=REVIEW_JSON, status="PROPOSED")
        s.add(review)
        await s.flush()
        p = (await apply.sync_proposals(s, review))[0]
        await apply.decide(s, p, "approve", None)
        await s.commit()
    with patch.object(grading, "ledger", new=AsyncMock(return_value={})):
        with patch.object(apply.llm_cli, "call_claude_json", new=AsyncMock(return_value=None)):
            async with dbf() as s:
                p = await s.get(IhV2Proposal, p.id)
                await apply.run_apply_agent(s, p)
                await s.commit()
        assert p.status == "NEEDS_REVIEW" and p.challenger_id is None
        assert apply.allowed_actions(p.status, None) == ["reject", "analyse"]
        code = {"evidence_holds": True, "kind": "code", "build_brief": "add a VWAP filter in levels.py",
                "reasoning": "needs logic"}
        with patch.object(apply.llm_cli, "call_claude_json", new=AsyncMock(return_value=code)):
            async with dbf() as s:
                p = await s.get(IhV2Proposal, p.id)
                await apply.decide_reanalyse(s, p, "try again")
                assert p.status == "APPROVED"
                await apply.run_apply_agent(s, p)
                await s.commit()
    assert p.status == "ANALYSED" and p.challenger_id is None
    assert [h["status"] for h in p.history] == ["PROPOSED", "APPROVED", "NEEDS_REVIEW", "APPROVED", "ANALYSED"]
    assert p.apply_plan["build_brief"].startswith("add a VWAP")
