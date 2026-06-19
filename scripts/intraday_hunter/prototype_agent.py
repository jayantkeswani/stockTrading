#!/usr/bin/env python3
"""Offline prototype for the Intraday Hunter agent — the PROMPT REVIEW GATE.

Assembles the real Call 1 (pre-open thesis) and the Call 2 decision LOOP (the 9:15-9:30
watcher: fire ~09:18, re-fire on WAIT per recheck_in_minutes, capped 09:30) for a
historical day from the local DB, renders the mplfinance charts, inlines them as base64
in a single-turn stream-json call, and prints the prompts + responses. Each recheck is
fed the PRIOR decisions so it is not blind. Runs one or more models and times each.

Reuses the pure builders + CLI wrapper in app/services/intraday_hunter/*.

Usage:
    python scripts/intraday_hunter/prototype_agent.py --date 2026-06-03
    python scripts/intraday_hunter/prototype_agent.py --date 2026-06-03 --models claude-opus-4-8,claude-sonnet-4-6
    python scripts/intraday_hunter/prototype_agent.py --date 2026-06-03 --dry   # prompts only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time as _time
from datetime import date, datetime, time, timedelta

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from app.services.intraday_hunter import charts, context, llm_cli, prompts  # noqa: E402

DEFAULT_DB = "postgresql://trader:trader_dev_123@localhost:5433/stocktrading"
CHART_DIR = "/tmp/ih_charts"
CALL2_START = time(9, 18)        # first watcher checkpoint
CALL2_DEADLINE = time(9, 30)     # backstop
VIX_SYMBOL = "INDIA VIX"


def _resolve_token() -> str | None:
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok
    scratch = os.path.join(os.path.dirname(__file__), "..", "claude_oauth_test.py")
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_ih_scratch", scratch)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return (getattr(mod, "TOKEN", "") or "").strip() or None
    except Exception:
        return None


async def _fetch_day(conn, symbol: str, d: date) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT (timestamp AT TIME ZONE 'Asia/Kolkata') AS ts, open, high, low, close, volume
        FROM market_data_1m
        WHERE symbol = $1 AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = $2
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::time BETWEEN '09:15' AND '15:30'
        ORDER BY timestamp
        """,
        symbol, d,
    )
    return [
        {"ts": r["ts"].isoformat(), "open": r["open"], "high": r["high"],
         "low": r["low"], "close": r["close"], "volume": r["volume"]}
        for r in rows if min(r["open"], r["high"], r["low"], r["close"]) > 0
    ]


async def _prev_trading_date(conn, symbol: str, d: date) -> date | None:
    row = await conn.fetchrow(
        """SELECT MAX((timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS pd
           FROM market_data_1m
           WHERE symbol = $1 AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date < $2""",
        symbol, d,
    )
    return row["pd"] if row else None


async def _fetch_vix(conn, d: date, upto: time | None = None) -> float | None:
    """Most recent INDIA VIX close on date d up to `upto` (defaults to EOD)."""
    cutoff = upto or time(15, 30)
    row = await conn.fetchrow(
        """SELECT close FROM market_data_1m
           WHERE symbol = $1 AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = $2
             AND (timestamp AT TIME ZONE 'Asia/Kolkata')::time <= $3
           ORDER BY timestamp DESC LIMIT 1""",
        VIX_SYMBOL, d, cutoff,
    )
    return float(row["close"]) if row and row["close"] else None


def _calendar(d: date) -> dict:
    wd = d.weekday()
    if wd == 1:
        return {"is_expiry": True, "expiry_index": "NIFTY", "holiday_ahead": False}
    if wd == 3:
        return {"is_expiry": True, "expiry_index": "SENSEX", "holiday_ahead": False}
    return {"is_expiry": False, "expiry_index": None, "holiday_ahead": False}


def _slim_decision(d: dict, at: str) -> dict:
    """Compact prior-decision record fed back into the next recheck."""
    return {
        "at": at,
        "decision": d.get("decision"),
        "action": d.get("action"),
        "direction": d.get("direction"),
        "entry_trigger": d.get("entry_trigger"),
        "confidence": d.get("confidence"),
        "recheck_in_minutes": d.get("recheck_in_minutes"),
    }


async def run_model(conn, trading_date: date, model: str, dry: bool, token: str | None) -> None:
    ds = trading_date.isoformat()
    os.makedirs(CHART_DIR, exist_ok=True)
    print("\n" + "#" * 92)
    print(f"# MODEL: {model}    DATE: {ds}")
    print("#" * 92)

    # ---- Call 1 context + prev-day charts ----
    per_index_prev: dict[str, dict] = {}
    prevday_imgs: list[str] = []
    for idx in context.INDICES:
        pd = await _prev_trading_date(conn, idx, trading_date)
        if pd is None:
            continue
        prev_candles = await _fetch_day(conn, idx, pd)
        if not prev_candles:
            continue
        struct = context.prev_day_structure(prev_candles, idx)
        struct["prev_date"] = pd.isoformat()
        per_index_prev[idx] = struct
        levels = {k: struct[k] for k in ("prev_close", "pdh", "pdl", "round")}
        prevday_imgs.append(charts.render_prev_day_chart(idx, prev_candles, levels, CHART_DIR, ds))
    if not per_index_prev:
        print("No previous-day data — pick another --date.")
        return

    prev_date = next(iter(per_index_prev.values()))["prev_date"]
    vix_c1 = await _fetch_vix(conn, datetime.strptime(prev_date, "%Y-%m-%d").date())
    cal = _calendar(trading_date)
    c1_ctx = context.build_call1_context(per_index_prev, multi_day_memory=[],
                                         calendar=cal, india_vix=vix_c1)
    call1_prompt = prompts.build_call1_prompt(c1_ctx)
    sys_prompt = prompts.build_system_prompt()

    print(f"\n--- CALL 1 USER PROMPT (VIX={vix_c1}, expiry={cal['expiry_index'] or 'no'}) ---\n")
    print(call1_prompt)
    call1_output: dict | None = None
    if not dry:
        t0 = _time.time()
        call1_output = await llm_cli.call_claude_json(
            sys_prompt + "\n\n" + call1_prompt, image_paths=prevday_imgs,
            required_keys=("trapped_side", "thesis", "conditional_plan"),
            token=token, model=model,
        )
        print(f"\n--- CALL 1 RESPONSE ({_time.time()-t0:.1f}s) ---\n")
        print(json.dumps(call1_output, indent=2) if call1_output else "(SKIP — no/invalid response)")
    if dry or call1_output is None:
        if call1_output is None and not dry:
            print("\n(Stopping — Call 1 produced no thesis.)")
        return

    # ---- Call 2 watcher loop: fire 09:18, re-fire on WAIT, cap 09:30 ----
    today: dict[str, list[dict]] = {idx: await _fetch_day(conn, idx, trading_date)
                                    for idx in per_index_prev}
    prior: list[dict] = []
    checkpoint = CALL2_START
    while checkpoint <= CALL2_DEADLINE:
        hhmm = checkpoint.strftime("%H:%M")
        per_open: dict[str, float] = {}
        per_opening: dict[str, list[dict]] = {}
        opening_imgs: list[str] = []
        for idx in per_index_prev:
            opening = [c for c in today[idx] if c["ts"][11:16] <= hhmm]
            if not opening:
                continue
            per_open[idx] = float(opening[0]["open"])
            per_opening[idx] = opening
            levels = {k: per_index_prev[idx][k] for k in ("prev_close", "pdh", "pdl", "round")}
            opening_imgs.append(charts.render_opening_chart(idx, opening, levels, CHART_DIR, ds))
        if not per_open:
            print(f"\n[no opening data at {hhmm} — cannot run Call 2]")
            return

        mins = (checkpoint.hour * 60 + checkpoint.minute) - (9 * 60 + 15)
        vix_c2 = await _fetch_vix(conn, trading_date, upto=checkpoint)
        live = context.build_call2_live(per_open, per_index_prev, per_opening,
                                        now_hhmm=hhmm, minutes_since_open=mins, india_vix=vix_c2)
        call2_prompt = prompts.build_call2_prompt(call1_output, live, prior)

        is_backstop = checkpoint >= CALL2_DEADLINE
        print(f"\n{'='*70}\nCALL 2 @ {hhmm}{'  (BACKSTOP)' if is_backstop else ''}   "
              f"priors={len(prior)}\n{'='*70}")
        t0 = _time.time()
        out = await llm_cli.call_claude_json(
            sys_prompt + "\n\n" + call2_prompt, image_paths=prevday_imgs + opening_imgs,
            required_keys=("decision", "confidence"), token=token, model=model,
        )
        print(f"--- CALL 2 RESPONSE ({_time.time()-t0:.1f}s) ---")
        if not out:
            print("(SKIP — no/invalid response)")
            return
        print(json.dumps(out, indent=2))

        decision = (out.get("decision") or "").upper()
        if decision in ("ENTER", "SKIP"):
            return
        # WAIT -> advance by recheck_in_minutes (1-2, default 1), capped at the deadline
        prior.append(_slim_decision(out, hhmm))
        try:
            step = int(out.get("recheck_in_minutes") or 1)
        except (TypeError, ValueError):
            step = 1
        step = max(1, min(step, 5))
        nxt = (datetime.combine(date.today(), checkpoint) + timedelta(minutes=step)).time()
        checkpoint = nxt if nxt <= CALL2_DEADLINE else CALL2_DEADLINE
        if prior and prior[-1]["at"] == checkpoint.strftime("%H:%M"):
            return  # already at deadline; avoid infinite loop


async def run(trading_date: date, dry: bool, models: list[str]) -> None:
    db = os.environ.get("DATABASE_URL", DEFAULT_DB).replace("+asyncpg", "")
    conn = await asyncpg.connect(db)
    token = _resolve_token()
    try:
        for model in models:
            t0 = _time.time()
            await run_model(conn, trading_date, model, dry, token)
            print(f"\n>>> {model} total wall time: {_time.time()-t0:.1f}s\n")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True, help="trading date YYYY-MM-DD")
    p.add_argument("--dry", action="store_true", help="print prompts only; no LLM call")
    p.add_argument("--models", default="claude-opus-4-8,claude-sonnet-4-6",
                   help="comma-separated claude models to run")
    args = p.parse_args()
    asyncio.run(run(datetime.strptime(args.date, "%Y-%m-%d").date(), args.dry,
                    [m.strip() for m in args.models.split(",") if m.strip()]))


if __name__ == "__main__":
    main()
