"""Morning Screener & Briefing for Strategy 5 (Intraday Futures).

Pre-market workflow:
  8:00 AM  — run_morning_briefing(): LLM synthesis of yesterday's performance
  8:30 AM  — run_morning_screener(): 3-stage pipeline (quant → news → LLM confidence)
  9:08 AM  — run_preopen_reassessment(): gap-adjusted bias override from pre-open prices

Results stored in Redis with 90-day TTL under strat5:* keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta

from app.core.redis import get_redis
from app.data.sectors import get_sector
from app.data_feed.fyers_client import FyersClient
from app.indicators.adr import adr_qualifies, compute_adr
from app.indicators.candle_patterns import Candle
from app.indicators.previous_day import analyze_previous_day
from app.indicators.relative_strength import (
    compute_rs_raw_score,
    percentile_rank_rs,
)
from app.indicators.stock_trend import compute_stock_trend
from app.indicators.rvol import build_volume_profile, serialize_profile
from app.indicators.volume_analysis import volume_ratio
from app.research.llm_client import create_llm_client

logger = logging.getLogger(__name__)

REDIS_TTL = 90 * 86400  # 90 days in seconds
FYERS_SEMAPHORE_LIMIT = 2
FYERS_INTER_REQUEST_DELAY = 1.0  # seconds between requests within a semaphore slot
FYERS_429_MAX_RETRIES = 4
FYERS_429_BASE_DELAY = 8  # seconds before first 429 retry
MIN_COMPOSITE_SCORE = 50
TOP_N_FOR_NEWS = 25
TOP_N_FOR_CONFIDENCE = 20

_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string", "enum": ["aggressive", "normal", "conservative"]},
        "summary": {"type": "string"},
        "sector_bias": {"type": "string"},
        "sector_avoid": {"type": "string"},
        "setup_priority": {"type": "array", "items": {"type": "string"}},
        "flags": {"type": "array", "items": {"type": "string"}},
        "max_lots_recommendation": {"type": "integer"},
    },
    "required": ["approach", "summary", "max_lots_recommendation"],
}

_STAGE3_CONFIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "confidence", "reason"],
            },
        },
        "correlated_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "sector": {"type": "string"},
                    "keep": {"type": "string"},
                },
                "required": ["symbols", "sector", "keep"],
            },
        },
    },
    "required": ["ratings", "correlated_groups"],
}


# ---------------------------------------------------------------------------
# Morning Briefing
# ---------------------------------------------------------------------------


async def run_morning_briefing(as_of: date | None = None, force: bool = False) -> dict:
    """Synthesize yesterday's performance into today's trading approach.

    Gathers trade history, computes stats, and asks the LLM for a briefing.
    Pass force=True to re-run even if a cached result exists (e.g. manual trigger).
    """
    from app.core.utils import now_ist

    today = as_of or now_ist().date()
    r = get_redis()

    # Check if already run today (skip when force=True so manual trigger always re-runs)
    key = f"strat5:morning_briefing:{today}"
    if not force:
        existing = await r.get(key)
        if existing:
            return json.loads(existing)

    data = await _gather_briefing_data(today)

    llm = create_llm_client(pro=True)
    briefing = await _synthesize_briefing(llm, data)
    briefing["date"] = str(today)
    briefing["generated_at"] = time.time()

    await r.set(key, json.dumps(briefing), ex=REDIS_TTL)
    await _append_agent_log(today, "BRIEFING", briefing.get("summary", "Morning briefing generated"))

    return briefing


async def _gather_briefing_data(today: date) -> dict:
    """Gather rich context for the morning briefing LLM call.

    Includes: per-trade details, per-sector P&L, per-setup win rates,
    VIX 5-day trend, yesterday's agent log summary, and today's global cues.
    """
    from collections import Counter

    from sqlalchemy import and_, desc, func, select

    from app.core.database import async_session_factory
    from app.data.sectors import get_sector
    from app.models.global_market_snapshot import GlobalMarketSnapshot
    from app.models.trade import Trade

    yesterday = today - timedelta(days=1)
    five_days_ago = today - timedelta(days=7)

    async with async_session_factory() as session:
        # Yesterday's trades (full detail)
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.strategy_name == "intraday_futures",
                    func.date(Trade.entry_time) >= yesterday,
                    func.date(Trade.entry_time) <= yesterday,
                )
            )
        )
        yesterday_trades = result.scalars().all()

        # Last 5 trading days (closed only)
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.strategy_name == "intraday_futures",
                    func.date(Trade.entry_time) >= five_days_ago,
                    Trade.status == "CLOSED",
                )
            )
        )
        recent_trades = result.scalars().all()

        # VIX 5-day trend from GlobalMarketSnapshot
        result = await session.execute(
            select(
                func.date(GlobalMarketSnapshot.timestamp).label("day"),
                func.avg(GlobalMarketSnapshot.us_vix).label("avg_vix"),
            )
            .where(
                and_(
                    GlobalMarketSnapshot.us_vix.is_not(None),
                    func.date(GlobalMarketSnapshot.timestamp) >= five_days_ago,
                    func.date(GlobalMarketSnapshot.timestamp) < today,
                )
            )
            .group_by(func.date(GlobalMarketSnapshot.timestamp))
            .order_by(desc(func.date(GlobalMarketSnapshot.timestamp)))
            .limit(5)
        )
        vix_rows = result.all()

    # --- Per-trade details for yesterday ---
    yesterday_detail = []
    for t in yesterday_trades:
        yesterday_detail.append({
            "symbol": t.symbol,
            "side": t.side,
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price) if t.exit_price else None,
            "pnl": float(t.pnl) if t.pnl else None,
            "pnl_pct": float(t.pnl_percent) if t.pnl_percent else None,
            "exit_reason": t.exit_reason,
            "status": t.status,
        })

    # --- Aggregate stats ---
    yesterday_stats = _compute_trade_stats(yesterday_trades)
    recent_stats = _compute_trade_stats(recent_trades)

    # --- Per-sector P&L over 5 days ---
    sector_pnl: dict[str, dict] = {}
    for t in recent_trades:
        sector = get_sector(t.symbol) or "UNKNOWN"
        if sector not in sector_pnl:
            sector_pnl[sector] = {"pnl": 0.0, "wins": 0, "losses": 0}
        sector_pnl[sector]["pnl"] += float(t.pnl or 0)
        if t.pnl and t.pnl > 0:
            sector_pnl[sector]["wins"] += 1
        elif t.pnl is not None:
            sector_pnl[sector]["losses"] += 1
    sector_pnl = {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in sector_pnl.items()}

    # --- Per-setup win rates over 5 days ---
    # Batch-load signal indicators to extract setup_type
    signal_setup_map: dict[str, str] = {}
    signal_ids = [t.signal_id for t in recent_trades if t.signal_id]
    if signal_ids:
        from app.models.signal import Signal
        async with async_session_factory() as session:
            sig_result = await session.execute(
                select(Signal.id, Signal.indicators).where(Signal.id.in_(signal_ids))
            )
            for sig_id, indicators in sig_result.all():
                if isinstance(indicators, dict):
                    signal_setup_map[str(sig_id)] = indicators.get("setup_type", "ORB")

    setup_stats: dict[str, dict] = {}
    for t in recent_trades:
        setup = signal_setup_map.get(str(t.signal_id), "ORB") if t.signal_id else "ORB"
        if setup not in setup_stats:
            setup_stats[setup] = {"wins": 0, "losses": 0}
        if t.pnl and t.pnl > 0:
            setup_stats[setup]["wins"] += 1
        elif t.pnl is not None:
            setup_stats[setup]["losses"] += 1
    for s in setup_stats.values():
        total = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / max(total, 1) * 100, 1)

    # --- Drawdown streak ---
    recent_sorted = sorted(recent_trades, key=lambda t: t.entry_time, reverse=True)
    consecutive_losses = 0
    for t in recent_sorted:
        if t.pnl and t.pnl <= 0:
            consecutive_losses += 1
        else:
            break

    # --- VIX 5-day trend ---
    vix_trend = []
    for row in reversed(vix_rows):  # oldest first
        vix_trend.append({"date": str(row.day), "vix": round(float(row.avg_vix), 1)})
    vix_direction = "stable"
    if len(vix_trend) >= 3:
        first_val = vix_trend[0]["vix"]
        last_val = vix_trend[-1]["vix"]
        if last_val < first_val - 1.0:
            vix_direction = "falling"
        elif last_val > first_val + 1.0:
            vix_direction = "rising"

    # --- Today's global cues ---
    global_cues = await get_global_cues(str(today))
    if not global_cues:
        global_cues = await snapshot_global_cues(today)

    # --- Yesterday's agent log summary ---
    agent_log_summary = await _summarize_agent_log(yesterday)

    # Current VIX
    r = get_redis()
    vix_str = await r.get("indicator:global:us_vix")

    return {
        "yesterday": yesterday_stats,
        "yesterday_trades": yesterday_detail,
        "recent_5d": recent_stats,
        "sector_pnl_5d": sector_pnl,
        "setup_stats_5d": setup_stats,
        "consecutive_losses": consecutive_losses,
        "vix": float(vix_str) if vix_str else None,
        "vix_trend": vix_trend,
        "vix_direction": vix_direction,
        "global_cues": global_cues,
        "agent_log_summary": agent_log_summary,
    }


async def _summarize_agent_log(log_date: date) -> dict:
    """Summarize yesterday's agent log into key counts and top skip reasons."""
    from collections import Counter

    entries = await get_agent_log(str(log_date))
    if not entries:
        return {"available": False}

    category_counts = Counter(e.get("category", "UNKNOWN") for e in entries)

    skip_reasons: list[str] = []
    for e in entries:
        if e.get("category") == "SKIP":
            skip_reasons.append(e.get("message", ""))

    skip_reason_counts = Counter()
    for reason in skip_reasons:
        # Normalize: extract the filter that caused the skip
        for keyword in ["RVOL", "ADR", "volume", "VWAP", "Nifty", "price", "R:R"]:
            if keyword.lower() in reason.lower():
                skip_reason_counts[keyword] += 1
                break
        else:
            skip_reason_counts["other"] += 1

    return {
        "available": True,
        "total_entries": len(entries),
        "signals_generated": category_counts.get("SIGNAL", 0),
        "signals_skipped": category_counts.get("SKIP", 0),
        "trades_executed": category_counts.get("TRADE", 0),
        "risk_warnings": category_counts.get("RISK", 0),
        "top_skip_reasons": dict(skip_reason_counts.most_common(3)),
    }


def _compute_trade_stats(trades: list) -> dict:
    wins = sum(1 for t in trades if t.pnl and t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl and t.pnl <= 0)
    total_pnl = sum(float(t.pnl or 0) for t in trades)
    return {
        "trade_count": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
        "net_pnl": round(total_pnl, 2),
    }


async def _synthesize_briefing(llm, data: dict) -> dict:
    """Ask LLM to synthesize a morning briefing from enriched data."""
    system = (
        "You are the risk-management layer for an intraday stock futures strategy "
        "on NSE India. Your briefing controls three downstream decisions:\n"
        "  1. 'approach' (aggressive/normal/conservative) — caps lot sizing and "
        "signal generation aggressiveness.\n"
        "  2. 'sector_bias' / 'sector_avoid' — the screener weights these in ranking.\n"
        "  3. 'max_lots_recommendation' (1 or 2) — hard cap on lots per trade today.\n\n"
        "## Approach rules (evaluated in priority order)\n"
        "CONSERVATIVE (max 1 lot) — any one of:\n"
        "  - 3+ consecutive losing trades (drawdown streak)\n"
        "  - VIX rising for 3+ days AND current VIX > 17\n"
        "  - Global cues strongly negative (US markets -1%+, crude spike >2%)\n"
        "  - Win rate below 30% across all setups over 5 days\n"
        "AGGRESSIVE (max 2 lots) — ALL of:\n"
        "  - VIX falling or stable AND current VIX < 16\n"
        "  - 5-day net P&L positive\n"
        "  - No drawdown streak (consecutive losses < 2)\n"
        "  - Global cues neutral or positive\n"
        "NORMAL (max 2 lots) — everything else. This is the default.\n\n"
        "## Setup priority\n"
        "setup_priority controls which setups the strategy favors today:\n"
        "  - ORB: best on trending days (gap + follow-through, VIX moderate)\n"
        "  - VWAP_BOUNCE: best on mean-reversion days (gap fade, choppy price action)\n"
        "  - PDH_PDL: best when prior day had a clear range and today breaks it\n"
        "  - GAP_CONTINUATION: best on strong gap days (>0.5% Nifty gap, global trend)\n"
        "Recommend 1-2 setups that match today's expected regime. Do NOT always default to ORB.\n\n"
        "## Sector bias\n"
        "Consider BOTH recent P&L performance AND today's global cues:\n"
        "  - Crude spike >2% → avoid ENERGY (input cost), favor METALS (commodity cycle)\n"
        "  - USD strengthening >0.5% → avoid IT (revenue tailwind priced in), favor PHARMA\n"
        "  - US markets strongly positive → favor IT, METALS (global beta)\n"
        "  - Recent sector P&L: bias toward sectors with >60% win rate, avoid <30%\n"
        "  - When recent P&L and global cues conflict, weight global cues higher (forward-looking)\n\n"
        "## Mixed signals\n"
        "When indicators conflict (e.g. VIX rising but P&L positive, or global negative but "
        "sector strong), default to NORMAL with specific flags explaining the tension.\n\n"
        "Be specific and data-driven. 'Banking strong 4/5 days, net +8K' is good. "
        "'Consider sectors' is useless."
    )

    prompt = f"""## Yesterday's Trades (per-trade detail)
{json.dumps(data.get('yesterday_trades', []), indent=2)}

## Yesterday Aggregate
{json.dumps(data['yesterday'])}

## Last 5 Trading Days Aggregate
{json.dumps(data['recent_5d'])}

## Per-Sector P&L (last 5 days)
{json.dumps(data.get('sector_pnl_5d', {}), indent=2)}

## Per-Setup Win Rates (last 5 days)
{json.dumps(data.get('setup_stats_5d', {}), indent=2)}

## Drawdown Streak
Consecutive recent losing trades: {data.get('consecutive_losses', 0)}

## VIX Trend (oldest → newest)
{json.dumps(data.get('vix_trend', []))}
Direction: {data.get('vix_direction', 'unknown')}
Current VIX: {data.get('vix', 'N/A')}

## Today's Global Cues
{json.dumps(data.get('global_cues', {}), indent=2)}

## Yesterday's Agent Activity Summary
{json.dumps(data.get('agent_log_summary', {}), indent=2)}

Respond in JSON:
{{
    "approach": "aggressive" | "normal" | "conservative",
    "summary": "<2-3 sentence briefing citing specific numbers from the data above>",
    "sector_bias": "<specific sector name to favor, or 'none'>",
    "sector_avoid": "<specific sector name to avoid, or 'none'>",
    "setup_priority": ["<1-2 setup types from: ORB, VWAP_BOUNCE, PDH_PDL, GAP_CONTINUATION>"],
    "flags": ["<specific warnings with numbers, e.g. 'ORB win rate 25% over 5 days'>"],
    "max_lots_recommendation": 1 or 2
}}"""

    try:
        result = await llm.generate_json(
            prompt=prompt, system=system, max_tokens=4096,
            response_schema=_BRIEFING_SCHEMA,
        )
        result.setdefault("approach", "normal")
        result.setdefault("flags", [])
        result.setdefault("max_lots_recommendation", 1 if data.get("consecutive_losses", 0) >= 3 else 2)
        if not result.get("summary"):
            result["summary"] = "No briefing available."
        return result
    except Exception as e:
        logger.warning("Morning briefing LLM failed: %s", e)
        conservative = data.get("consecutive_losses", 0) >= 3
        return {
            "approach": "conservative" if conservative else "normal",
            "summary": "LLM briefing unavailable. Using default approach.",
            "flags": ["llm_unavailable"],
            "max_lots_recommendation": 1 if conservative else 2,
        }


# ---------------------------------------------------------------------------
# Morning Screener — 3-stage pipeline
# ---------------------------------------------------------------------------


async def run_morning_screener(as_of: date | None = None) -> list[dict]:
    """Run the full 3-stage screener pipeline.

    Stage 1: Quantitative scoring (~180 F&O stocks, zero LLM calls)
    Stage 2: News & Sentiment (~20 Gemini calls, parallelized)
    Stage 3: LLM Confidence Check (1 batched call)
    """
    from app.core.utils import now_ist

    today = as_of or now_ist().date()
    r = get_redis()

    # Check if already run today
    key = f"strat5:watchlist:{today}"
    existing = await r.get(key)
    if existing:
        return json.loads(existing)

    await _append_agent_log(today, "SCREENER", "Starting morning screener pipeline")

    # Stage 1: Quantitative scoring
    candidates, all_scores_lookup = await _stage1_quantitative(today)
    await _append_agent_log(
        today, "SCREENER", f"Stage 1 complete: {len(candidates)} stocks scored >= {MIN_COMPOSITE_SCORE}"
    )

    if not candidates:
        await r.set(key, json.dumps([]), ex=REDIS_TTL)
        return []

    # Merge permanent watchlist stocks that didn't clear the quant threshold
    perm_raw = await r.get("strat5:watchlist:permanent")
    permanent_symbols = set(json.loads(perm_raw)) if perm_raw else set()
    if permanent_symbols:
        existing_symbols = {c["symbol"] for c in candidates}
        injected = 0
        for sym in permanent_symbols:
            if sym not in existing_symbols:
                scored = all_scores_lookup.get(sym)
                if scored:
                    candidate = {**scored, "manual": True}
                else:
                    candidate = {
                        "symbol": sym,
                        "composite_score": 0,
                        "price": 0,
                        "bias": "NEUTRAL",
                        "trend_strength": "WEAK",
                        "trend_score": 0.0,
                        "trend_components": {},
                        "factors": {
                            "rs_percentile": 0.0,
                            "range_position": 50.0,
                            "volume_trend": 50.0,
                            "oi_change": 50.0,
                            "adr_pct": 0.0,
                            "adr_qualifies": False,
                            "sector": get_sector(sym),
                            "trend_quality": 50.0,
                            "delivery_pct": 50.0,
                            "high_52w_proximity": 50.0,
                        },
                        "pdh": None,
                        "pdl": None,
                        "pdc": None,
                        "lot_size": 0,
                        "manual": True,
                    }
                candidates.append(candidate)
                injected += 1
            else:
                for c in candidates:
                    if c["symbol"] == sym:
                        c["from_permanent"] = True
                        break
        await _append_agent_log(today, "SCREENER", f"Merged {len(permanent_symbols)} permanent watchlist stock(s)")
        logger.info("Screener: merged %d permanent symbols (%d injected, %d already present)", len(permanent_symbols), injected, len(permanent_symbols) - injected)

    # Stage 2: News & Sentiment
    candidates = await _stage2_news_sentiment(candidates)
    await _append_agent_log(today, "SCREENER", f"Stage 2 complete: {len(candidates)} stocks after news filter")

    # Stage 3: LLM Confidence Check
    watchlist = await _stage3_llm_confidence(candidates)
    await _append_agent_log(today, "SCREENER", f"Stage 3 complete: {len(watchlist)} stocks on final watchlist")

    # Store watchlist
    await r.set(key, json.dumps(watchlist), ex=REDIS_TTL)

    # Build RVOL baselines for watchlist stocks
    symbols = [w["symbol"] for w in watchlist]
    await _build_rvol_baselines(symbols, today)

    # Subscribe watchlist stocks on Fyers WebSocket + backfill today's candles
    await _provision_watchlist_symbols(symbols, today)
    await _append_agent_log(today, "SYSTEM", f"Provisioned {len(symbols)} watchlist symbols (WS + candle backfill)")

    return watchlist


async def _fetch_stock_oi_changes(symbols: list[str]) -> dict[str, dict]:
    """Fetch OI change for stock futures from oi_snapshots table.

    Compares the two most recent daily FUT snapshots per symbol to compute
    OI change. Returns {symbol: {"oi_change": int, "latest_oi": int}}.
    """
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.oi_snapshot import OISnapshot

    result: dict[str, dict] = {}

    try:
        async with async_session_factory() as session:
            # Get distinct snapshot dates for FUT entries, most recent first
            date_subq = (
                select(
                    OISnapshot.symbol,
                    func.date(OISnapshot.timestamp).label("snap_date"),
                    OISnapshot.open_interest,
                )
                .where(
                    OISnapshot.option_type == "FUT",
                    OISnapshot.symbol.in_(symbols),
                )
                .order_by(OISnapshot.timestamp.desc())
            )
            rows = (await session.execute(date_subq)).all()

            # Group by symbol — take the two most recent distinct dates
            per_symbol: dict[str, list[tuple]] = {}
            for row in rows:
                sym = row.symbol
                if sym not in per_symbol:
                    per_symbol[sym] = []
                snap_date = row.snap_date
                # Only keep one entry per distinct date
                if not per_symbol[sym] or per_symbol[sym][-1][0] != snap_date:
                    per_symbol[sym].append((snap_date, row.open_interest))
                if len(per_symbol[sym]) >= 2:
                    continue  # enough data for this symbol

            for sym, entries in per_symbol.items():
                latest_oi = entries[0][1] if entries else 0
                if len(entries) >= 2:
                    oi_change = entries[0][1] - entries[1][1]
                else:
                    oi_change = 0
                result[sym] = {"oi_change": oi_change, "latest_oi": latest_oi}
    except Exception:
        logger.warning("Failed to fetch stock OI changes", exc_info=True)

    return result


async def _stage1_quantitative(today: date) -> tuple[list[dict], dict[str, dict]]:
    """Score ~180 F&O stocks on 8 quantitative factors."""
    from app.data_sources.nse_client import get_fo_ban_list, get_fo_lot_sizes
    from app.tasks.nse_bhav_copy_task import get_bhav_copy, _previous_trading_day

    # Get F&O stock list
    lot_sizes = await get_fo_lot_sizes()
    if not lot_sizes:
        logger.error("Could not fetch F&O lot sizes — screener aborted")
        return []

    symbols = list(lot_sizes.keys())

    # Filter out F&O ban list (MWPL-breached securities — new positions are prohibited)
    ban_set = await get_fo_ban_list(today)
    if ban_set:
        banned_present = [s for s in symbols if s in ban_set]
        symbols = [s for s in symbols if s not in ban_set]
        if banned_present:
            await _append_agent_log(
                today, "SCREENER",
                f"Excluded {len(banned_present)} F&O banned symbol(s): {', '.join(sorted(banned_present))}",
            )
            logger.info(
                "Screener: excluded %d banned F&O symbols: %s",
                len(banned_present), banned_present,
            )

    logger.info("Screener: scoring %d F&O stocks (%d banned excluded)", len(symbols), len(ban_set))

    # Fetch daily candle data for all symbols (last 60 days)
    daily_data = await _fetch_daily_data_batch(symbols, today, days=60)

    # Load bhav copy (delivery %) from Redis
    prev_day = _previous_trading_day(today)
    bhav_data = await get_bhav_copy(prev_day)

    # Load OI changes from oi_snapshots
    oi_change_data = await _fetch_stock_oi_changes(symbols)

    # Compute RS scores across universe for percentile ranking
    rs_raw: dict[str, float] = {}
    for sym, candles in daily_data.items():
        closes = [c.close for c in candles]
        if len(closes) >= 20:
            rs_raw[sym] = compute_rs_raw_score(closes)

    rs_percentiles = percentile_rank_rs(rs_raw) if rs_raw else {}

    # Score each stock — keep ALL scores for Redis, filter for pipeline
    all_scores: list[dict] = []
    candidates = []
    for sym in symbols:
        candles = daily_data.get(sym, [])
        if len(candles) < 5:
            continue

        sym_bhav = bhav_data.get(sym) if bhav_data else None
        sym_oi = oi_change_data.get(sym)
        score_data = _compute_stock_score(
            sym, candles, rs_percentiles, today,
            bhav_data=sym_bhav, oi_change_data=sym_oi,
        )
        if score_data:
            score_data["lot_size"] = lot_sizes.get(sym, 0)
            all_scores.append(score_data)
            if score_data["composite_score"] >= MIN_COMPOSITE_SCORE:
                candidates.append(score_data)

    # Persist all quant scores to Redis (for future LLM enrichment)
    r = get_redis()
    quant_key = f"strat5:quant_scores:{today}"
    await r.set(quant_key, json.dumps(all_scores), ex=REDIS_TTL)
    logger.info(
        "Screener: %d stocks scored, %d above threshold (%d), saved to %s",
        len(all_scores), len(candidates), MIN_COMPOSITE_SCORE, quant_key,
    )

    candidates.sort(key=lambda x: x["composite_score"], reverse=True)
    all_scores_lookup = {s["symbol"]: s for s in all_scores}
    return candidates[:TOP_N_FOR_NEWS], all_scores_lookup


def _compute_stock_score(
    symbol: str,
    daily_candles: list[Candle],
    rs_percentiles: dict[str, float],
    today: date,
    bhav_data: dict | None = None,
    oi_change_data: dict | None = None,
) -> dict | None:
    """Compute 8-factor composite score for a single stock."""
    if not daily_candles or daily_candles[-1].close <= 0:
        return None

    last = daily_candles[-1]
    price = last.close

    # Filter: minimum price
    if price < 100:
        return None

    # 1. RS Percentile (weight: 0.20)
    rs_pct = rs_percentiles.get(symbol, 50.0)
    rs_score = min(rs_pct, 99.0)

    # 2. Previous day range position (weight: 0.10)
    if len(daily_candles) >= 2:
        prev = daily_candles[-2]
        pd_levels = analyze_previous_day(prev.open, prev.high, prev.low, prev.close)
        day_range = pd_levels.day_range
        if day_range > 0:
            close_position = (last.close - pd_levels.pdl) / day_range * 100
            range_score = max(0, min(close_position, 100))
        else:
            range_score = 50.0
    else:
        range_score = 50.0
        pd_levels = None

    # 3. Volume trend — 5d/20d ratio (weight: 0.10)
    volumes = [c.volume for c in daily_candles]
    if len(volumes) >= 20:
        avg_5d = sum(volumes[-5:]) / 5
        avg_20d = sum(volumes[-20:]) / 20
        vol_trend = volume_ratio(int(avg_5d), int(avg_20d)) * 50
        vol_score = min(vol_trend, 100)
    else:
        vol_score = 50.0

    # 4. OI change (weight: 0.10) — from oi_snapshots FUT rows
    oi_score = 50.0
    if oi_change_data:
        oi_change = oi_change_data.get("oi_change", 0)
        price_up = (
            daily_candles[-1].close > daily_candles[-2].close
            if len(daily_candles) >= 2
            else True
        )
        oi_up = oi_change > 0
        if oi_up and price_up:
            oi_score = 100.0  # long buildup
        elif oi_up and not price_up:
            oi_score = 50.0   # short buildup
        elif not oi_up and price_up:
            oi_score = 30.0   # short covering
        else:
            oi_score = 20.0   # long unwinding

    # 5. ADR (weight: 0.10)
    adr_pct = compute_adr(daily_candles, period=20)
    adr_score = min(adr_pct / 5.0 * 100, 100) if adr_pct > 0 else 0

    # 6. Trend quality (weight: 0.10) — repurposed from sector_momentum placeholder
    sector = get_sector(symbol)
    trend = compute_stock_trend(daily_candles)
    trend_quality_score = 50.0 + abs(trend.score) * 50.0  # stronger trend = higher score

    # 7. Delivery % (weight: 0.05) — from NSE bhav copy
    delivery_score = 50.0
    if bhav_data:
        delivery_pct = bhav_data.get("delivery_pct", 0)
        delivery_score = min(100, max(0, (delivery_pct - 10) / 40 * 100))

    # 8. 52-week high proximity (weight: 0.15)
    if len(daily_candles) >= 20:
        high_52w = max(c.high for c in daily_candles)
        proximity = price / high_52w * 100 if high_52w > 0 else 50
        high_score = min(proximity, 100)
    else:
        high_score = 50.0

    # Weighted composite (weights from spec)
    composite = (
        rs_score * 0.20
        + range_score * 0.15
        + vol_score * 0.15
        + oi_score * 0.15
        + adr_score * 0.10
        + trend_quality_score * 0.10
        + delivery_score * 0.10
        + high_score * 0.05
    )

    return {
        "symbol": symbol,
        "composite_score": round(composite, 1),
        "price": price,
        "bias": trend.direction,
        "trend_strength": trend.strength,
        "trend_score": trend.score,
        "trend_components": trend.components,
        "factors": {
            "rs_percentile": round(rs_pct, 1),
            "range_position": round(range_score, 1),
            "volume_trend": round(vol_score, 1),
            "oi_change": round(oi_score, 1),
            "adr_pct": round(adr_pct, 2),
            "adr_qualifies": adr_qualifies(adr_pct),
            "sector": sector,
            "trend_quality": round(trend_quality_score, 1),
            "delivery_pct": round(delivery_score, 1),
            "high_52w_proximity": round(high_score, 1),
        },
        "pdh": pd_levels.pdh if pd_levels else None,
        "pdl": pd_levels.pdl if pd_levels else None,
        "pdc": pd_levels.pdc if pd_levels else None,
    }


async def _stage2_news_sentiment(candidates: list[dict]) -> list[dict]:
    """Run news sentiment analysis for top candidates via Gemini grounded search.

    Uses a 48-hour news window (vs 30 days in the research module) since
    the screener cares about recency — yesterday's downgrade matters more
    than last month's results.
    """
    from app.research.agents.base import ResearchContext
    from app.research.agents.news_sentiment import (
        SEARCH_SYSTEM_PROMPT,
        SENTIMENT_PROMPT_TEMPLATE,
        NewsSentimentAgent,
        _enrich_article_urls,
    )

    _SCREENER_SEARCH_PROMPT = (
        "Search for the latest news about {symbol} ({display_name}) on NSE India. "
        "LAST 48 HOURS ONLY — older news is irrelevant for today's trading.\n\n"
        "Priority (highest first):\n"
        "1. Earnings results or guidance changes (last 48h)\n"
        "2. Analyst upgrades/downgrades with price targets\n"
        "3. SEBI/regulatory actions, fraud allegations, credit rating changes\n"
        "4. Block deals, bulk deals, or large institutional transactions\n"
        "5. Corporate actions (dividends, splits, bonus, buybacks)\n"
        "6. Breaking news or significant price-moving events\n\n"
        "Skip: general sector commentary, old news, routine management quotes. "
        "Be factual. Attribute each item to a source."
    )

    class _ScreenerNewsAgent(NewsSentimentAgent):
        """Narrower search window for the morning screener context."""

        async def research(self, ctx, llm):
            search_prompt = _SCREENER_SEARCH_PROMPT.format(
                symbol=ctx.symbol, display_name=ctx.display_name
            )
            try:
                search_result = await llm.generate_with_search(
                    search_prompt, system=SEARCH_SYSTEM_PROMPT, max_tokens=4096
                )
            except Exception as e:
                from app.research.agents.base import AgentResult
                return AgentResult(
                    agent_name=self.name, status="failed", findings={},
                    summary="", error=str(e), data_sources=["google_search_grounding"],
                )
            news_text = search_result.text
            sources = search_result.sources
            if not news_text:
                from app.research.agents.base import AgentResult
                return AgentResult(
                    agent_name=self.name, status="partial",
                    findings={"overall_sentiment": "neutral", "articles": []},
                    summary="No recent news found.",
                    data_sources=["google_search_grounding"],
                )
            sources_text = "\n".join(
                f"- {s.get('title', 'Unknown')}: {s.get('url', 'N/A')}" for s in sources
            ) or "No source URLs available"
            sentiment_prompt = SENTIMENT_PROMPT_TEMPLATE.format(
                symbol=ctx.symbol, display_name=ctx.display_name,
                news_text=news_text, sources_text=sources_text,
            )
            try:
                findings = await llm.generate_json(sentiment_prompt, max_tokens=4096)
            except Exception:
                findings = {"overall_sentiment": "neutral", "articles": [], "raw_news": news_text[:2000]}
            findings.setdefault("overall_sentiment", "neutral")
            findings.setdefault("sentiment_score", 0.0)
            findings.setdefault("articles", [])
            findings.setdefault("key_themes", [])
            findings.setdefault("risk_events", [])
            findings.setdefault("catalyst_events", [])
            if sources and findings["articles"]:
                _enrich_article_urls(findings["articles"], sources)
            sentiment = findings["overall_sentiment"]
            score = findings.get("sentiment_score", 0)
            n_articles = len(findings["articles"])
            from app.research.agents.base import AgentResult
            return AgentResult(
                agent_name=self.name, status="completed", findings=findings,
                summary=f"News sentiment: {sentiment} ({score:+.1f}), {n_articles} articles.",
                data_sources=["google_search_grounding"],
            )

    llm = create_llm_client()
    agent = _ScreenerNewsAgent()
    news_sem = asyncio.Semaphore(5)

    async def _fetch_news(candidate: dict) -> dict:
        symbol = candidate["symbol"]
        ctx = ResearchContext(symbol=symbol, display_name=symbol)
        async with news_sem:
            try:
                result = await asyncio.wait_for(
                    agent.research(ctx, llm),
                    timeout=60,
                )
                findings = result.findings
                sentiment_score = findings.get("sentiment_score", 0.0)

                # Adjust composite score based on sentiment
                adjustment = sentiment_score * 10  # -10 to +10
                candidate["composite_score"] = round(candidate["composite_score"] + adjustment, 1)
                candidate["news"] = {
                    "sentiment": findings.get("overall_sentiment", "neutral"),
                    "score": sentiment_score,
                    "key_themes": findings.get("key_themes", [])[:3],
                    "risk_events": findings.get("risk_events", []),
                    "catalyst_events": findings.get("catalyst_events", []),
                    "articles_count": len(findings.get("articles", [])),
                    "headlines": [
                        a["headline"]
                        for a in findings.get("articles", [])[:3]
                        if a.get("headline")
                    ],
                }

                # Flag severe negative news
                risk_events = findings.get("risk_events", [])
                if sentiment_score < -0.5 and risk_events:
                    candidate["news"]["flagged"] = True

            except Exception as e:
                err_msg = str(e) or type(e).__name__
                logger.warning("News sentiment failed for %s: [%s] %s", symbol, type(e).__name__, err_msg)
                candidate["news"] = {"sentiment": "unknown", "score": 0.0, "error": err_msg}

        return candidate

    tasks = [_fetch_news(c) for c in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("News task exception: %s", r)
            continue
        # Drop stocks with severe negative news — but never drop permanent watchlist pins
        if r.get("news", {}).get("flagged") and not r.get("manual"):
            logger.info("Dropping %s due to severe negative news", r["symbol"])
            continue
        enriched.append(r)

    manual_pins = [r for r in enriched if r.get("manual")]
    screened = [r for r in enriched if not r.get("manual")]
    screened.sort(key=lambda x: x["composite_score"], reverse=True)
    return screened[:TOP_N_FOR_CONFIDENCE] + manual_pins


async def _stage3_llm_confidence(candidates: list[dict]) -> list[dict]:
    """Batched LLM confidence check enriched with global cues, briefing,
    fundamentals, and our own trade history per stock."""
    if not candidates:
        return []

    from app.core.utils import now_ist

    today = now_ist().date()

    # --- Gather enrichment data in parallel ---
    global_cues, briefing, fundamentals, trade_history = await asyncio.gather(
        _get_confidence_global_cues(today),
        _get_confidence_briefing(today),
        _get_confidence_fundamentals([c["symbol"] for c in candidates]),
        _get_confidence_trade_history([c["symbol"] for c in candidates]),
    )

    llm = create_llm_client(pro=True)
    system = (
        "You are a senior quantitative analyst reviewing stock candidates for "
        "intraday futures trading on NSE India.\n\n"
        "## Rating criteria\n"
        "HIGH: Strong quant score (>60) + supportive or neutral news + no sector "
        "headwinds + fundamentals show institutional interest (FII >15% or rising). "
        "Prime candidate for aggressive sizing.\n"
        "MEDIUM: Decent profile but one concern (mixed news, weak volume trend, "
        "sector rotation risk, low institutional interest, or low quant score 30-60). "
        "Standard watchlist inclusion.\n"
        "LOW: Specific red flag making intraday trading DANGEROUS today — regulatory "
        "risk, earnings tonight (not yet reported), active SEBI investigation, severe "
        "negative news score (<-0.5), or analyst downgrade within 48h. Will be DROPPED "
        "from the watchlist.\n\n"
        "IMPORTANT: Only rate LOW for concrete, stock-specific dangers. Do NOT rate "
        "LOW for: low quant scores alone, weak volume, general sector weakness, "
        "no news (neutral is fine), or limited trade history. Those are MEDIUM.\n\n"
        "## Using the morning briefing\n"
        "The briefing sets today's approach (aggressive/normal/conservative), sector "
        "bias, and sector to avoid. Apply these:\n"
        "  - If briefing says 'avoid PHARMA', downgrade PHARMA candidates by one tier "
        "unless they have exceptional quant+news.\n"
        "  - If briefing says 'sector_bias: METALS', give METALS candidates the benefit "
        "of the doubt on borderline ratings.\n"
        "  - Conservative approach: be stricter — require stronger evidence for HIGH.\n\n"
        "## Trade history interpretation\n"
        "our_history shows our own recent results trading this stock:\n"
        "  - No history (trades=0): neutral — don't penalize, we just haven't traded it.\n"
        "  - 1-2 trades: too small a sample — mention but don't let it drive the rating.\n"
        "  - 3+ trades with <30% win rate: flag as concern, factor into rating.\n"
        "  - 3+ trades with >60% win rate: mild positive (we trade this stock well).\n\n"
        "## Correlated groups\n"
        "Aggressively identify sector clusters. If 3+ candidates share a sector "
        "(especially BANKING, NBFC, IT, METALS), recommend keeping only the 1-2 "
        "strongest by quant score + news quality. Mark the rest for dropping.\n\n"
        "## Token efficiency\n"
        "Every candidate MUST have a reason (1 short sentence). If you run out of "
        "reasoning space, write 'Score X, news Y' — never leave reason empty."
    )

    candidates_json = json.dumps(
        [
            {
                "symbol": c["symbol"],
                "score": round(c["composite_score"], 1),
                "bias": c["bias"],
                "rs_pct": c["factors"]["rs_percentile"],
                "adr": round(c["factors"]["adr_pct"], 1),
                "volume_trend": c["factors"]["volume_trend"],
                "sector": c["factors"]["sector"],
                "news_sentiment": c.get("news", {}).get("sentiment", "unknown"),
                "news_score": c.get("news", {}).get("score", 0),
                "risk_events": c.get("news", {}).get("risk_events", []),
                "catalyst_events": c.get("news", {}).get("catalyst_events", []),
                "fundamentals": fundamentals.get(c["symbol"], {}),
                "our_history": trade_history.get(c["symbol"], {}),
            }
            for c in candidates
        ],
        indent=1,
    )

    prompt = f"""Rate these {len(candidates)} intraday futures candidates for today.

## Market Conditions
{json.dumps(global_cues, indent=1)}

## Morning Briefing
{json.dumps(briefing, indent=1)}

## Candidates
{candidates_json}

Rate EVERY candidate. Every reason must be non-empty (1 sentence).

Respond in JSON:
{{
    "ratings": [
        {{"symbol": "SYMBOL", "confidence": "HIGH" | "MEDIUM" | "LOW", "reason": "<1 sentence>"}},
        ...
    ],
    "correlated_groups": [
        {{"symbols": ["SYM1", "SYM2"], "sector": "BANKING", "keep": "SYM1"}}
    ]
}}"""

    try:
        result = await asyncio.wait_for(
            llm.generate_json(
                prompt=prompt, system=system, max_tokens=8192,
                response_schema=_STAGE3_CONFIDENCE_SCHEMA,
            ),
            timeout=60,
        )
        ratings_raw = result.get("ratings", [])
        if isinstance(ratings_raw, list):
            ratings = {r["symbol"]: r for r in ratings_raw if "symbol" in r}
        else:
            ratings = ratings_raw
        correlated_groups = result.get("correlated_groups", [])
    except Exception as e:
        logger.warning("LLM confidence check failed: %s — keeping all candidates", e)
        ratings = {}
        correlated_groups = []

    # Build set of symbols to drop from correlated groups
    correlated_drops: set[str] = set()
    for group in correlated_groups:
        keep = group.get("keep", "")
        for sym in group.get("symbols", []):
            if sym != keep:
                correlated_drops.add(sym)

    watchlist = []
    for c in candidates:
        sym = c["symbol"]
        rating = ratings.get(sym, {})
        confidence = rating.get("confidence", "MEDIUM")
        if confidence == "LOW" and not c.get("manual"):
            logger.info("Dropping %s: LLM rated LOW — %s", sym, rating.get("reason", ""))
            continue
        if sym in correlated_drops and not c.get("manual"):
            logger.info("Dropping %s: correlated sector duplicate", sym)
            continue
        c["llm_confidence"] = confidence
        c["llm_reason"] = rating.get("reason", "")
        watchlist.append(c)

    return watchlist


async def _get_confidence_global_cues(today: date) -> dict:
    """Read today's global cues for the confidence check prompt."""
    cues = await get_global_cues(str(today))
    if not cues:
        cues = await snapshot_global_cues(today)
    return {
        "india_vix": cues.get("us_vix"),
        "nifty_gap_pct": cues.get("nifty_pct"),
        "us_sp500_pct": cues.get("sp500_close_pct"),
        "us_nasdaq_pct": cues.get("nasdaq_close_pct"),
        "crude_pct": cues.get("crude_pct"),
        "usdinr_pct": cues.get("usdinr_pct"),
        "volatile_open": cues.get("volatile_open", False),
        "halted": cues.get("halted", False),
    }


async def _get_confidence_briefing(today: date) -> dict:
    """Read today's morning briefing for the confidence check prompt."""
    briefing = await get_morning_briefing(str(today))
    if not briefing:
        return {"available": False}
    return {
        "approach": briefing.get("approach", "normal"),
        "sector_bias": briefing.get("sector_bias", "none"),
        "flags": briefing.get("flags", []),
        "summary": briefing.get("summary", ""),
    }


async def _get_confidence_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """Fetch fundamental quality data for candidates from stock_fundamentals.

    Fetches on demand for symbols that are missing or older than 12 hours so
    Strategy 5 watchlist symbols stay fresh without a dedicated scheduler.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.core.utils import now_ist
    from app.models.fundamental_data import StockFundamental
    from app.tasks.fundamental_data_task import _fetch_and_store_symbol

    def _row_to_dict(row: StockFundamental) -> dict:
        return {
            "market_cap_cr": float(row.market_cap_cr) if row.market_cap_cr else None,
            "eps_growth_qtr_pct": float(row.latest_qtr_eps_growth_pct) if row.latest_qtr_eps_growth_pct else None,
            "roe_pct": float(row.roe_pct) if row.roe_pct else None,
            "debt_to_equity": float(row.debt_to_equity) if row.debt_to_equity else None,
            "fii_pct": float(row.fii_pct) if row.fii_pct else None,
            "fii_change_qoq": float(row.fii_change_qoq) if row.fii_change_qoq else None,
            "mf_pct": float(row.mf_pct) if row.mf_pct else None,
            "mf_change_qoq": float(row.mf_change_qoq) if row.mf_change_qoq else None,
        }

    result_map: dict[str, dict] = {}
    stale_cutoff = now_ist() - timedelta(hours=12)

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StockFundamental).where(StockFundamental.symbol.in_(symbols))
            )
            rows = {row.symbol: row for row in result.scalars()}

        # Populate fresh rows; collect symbols that are missing or stale
        needs_fetch: list[str] = []
        for symbol in symbols:
            row = rows.get(symbol)
            if row is None:
                needs_fetch.append(symbol)
            elif row.last_refreshed_at is None or row.last_refreshed_at < stale_cutoff:
                needs_fetch.append(symbol)
            else:
                result_map[symbol] = _row_to_dict(row)

        if needs_fetch:
            logger.info(
                "Fetching fundamentals on demand for %d symbols: %s",
                len(needs_fetch), needs_fetch,
            )
            await asyncio.gather(
                *[_fetch_and_store_symbol(sym) for sym in needs_fetch],
                return_exceptions=True,
            )

            # Re-read newly fetched rows from DB
            async with async_session_factory() as session:
                result = await session.execute(
                    select(StockFundamental).where(StockFundamental.symbol.in_(needs_fetch))
                )
                for row in result.scalars():
                    result_map[row.symbol] = _row_to_dict(row)

    except Exception:
        logger.debug("Could not fetch fundamentals for confidence check")
    return result_map


async def _get_confidence_trade_history(symbols: list[str]) -> dict[str, dict]:
    """Fetch our own recent Strategy 5 trade history per candidate stock."""
    from sqlalchemy import and_, desc, select

    from app.core.database import async_session_factory
    from app.models.trade import Trade

    result_map: dict[str, dict] = {}
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Trade)
                .where(
                    and_(
                        Trade.strategy_name == "intraday_futures",
                        Trade.symbol.in_(symbols),
                        Trade.status == "CLOSED",
                    )
                )
                .order_by(desc(Trade.entry_time))
                .limit(200)
            )
            trades = result.scalars().all()

        from collections import defaultdict
        by_symbol: dict[str, list] = defaultdict(list)
        for t in trades:
            by_symbol[t.symbol].append(t)

        for sym, sym_trades in by_symbol.items():
            recent = sym_trades[:10]
            wins = sum(1 for t in recent if t.pnl and t.pnl > 0)
            losses = sum(1 for t in recent if t.pnl and t.pnl <= 0)
            avg_pnl_pct = sum(float(t.pnl_percent or 0) for t in recent) / max(len(recent), 1)
            sl_hits = sum(1 for t in recent if t.exit_reason and "SL" in t.exit_reason.upper())
            result_map[sym] = {
                "total_trades": len(recent),
                "wins": wins,
                "losses": losses,
                "sl_hits": sl_hits,
                "avg_pnl_pct": round(avg_pnl_pct, 2),
            }
    except Exception:
        logger.debug("Could not fetch trade history for confidence check")
    return result_map


# ---------------------------------------------------------------------------
# RVOL Baseline Builder
# ---------------------------------------------------------------------------


async def _build_rvol_baselines(symbols: list[str], today: date) -> None:
    """Fetch 20-day 5-min candle history and build RVOL profiles for watchlist stocks."""
    r = get_redis()
    sem = asyncio.Semaphore(FYERS_SEMAPHORE_LIMIT)

    async def _build_one(symbol: str) -> None:
        cache_key = f"strat5:rvol_baseline:{symbol}"
        existing = await r.get(cache_key)
        if existing:
            return

        async with sem:
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)
            try:
                profile = await _fetch_and_build_rvol_profile(symbol, today)
                if profile:
                    await r.set(cache_key, serialize_profile(profile), ex=REDIS_TTL)
            except Exception as e:
                logger.warning("RVOL baseline failed for %s: %s", symbol, e)

    await asyncio.gather(*[_build_one(s) for s in symbols], return_exceptions=True)


async def _fetch_and_build_rvol_profile(symbol: str, today: date) -> dict[str, float] | None:
    """Fetch 20 days of 5-min candles from Fyers and build volume profile."""
    from app.core.redis import get_redis as _get_redis

    r = _get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        return None

    client = FyersClient(access_token=token)
    try:
        from_date = today - timedelta(days=30)
        fyers_symbol = f"NSE:{symbol}-EQ"

        raw_candles = await _fyers_history_with_retry(
            client, fyers_symbol, "5", from_date, today - timedelta(days=1),
        )
    finally:
        await client.close()

    if not raw_candles:
        return None

    # Group candles by date
    from datetime import datetime, timezone

    historical: dict[date, list[Candle]] = {}
    for rc in raw_candles:
        ts = datetime.fromtimestamp(rc["timestamp"], tz=timezone.utc)
        d = ts.date()
        candle = Candle(
            open=rc["open"],
            high=rc["high"],
            low=rc["low"],
            close=rc["close"],
            volume=rc["volume"],
        )
        historical.setdefault(d, []).append(candle)

    return build_volume_profile(historical)


# ---------------------------------------------------------------------------
# Watchlist Symbol Provisioning (WS + candle backfill)
# ---------------------------------------------------------------------------


async def _provision_watchlist_symbols(symbols: list[str], today: date) -> None:
    """Subscribe watchlist symbols on Fyers WebSocket and backfill today's candles.

    Same pattern as strategies.py _provision_new_symbols but for dynamic screener output.
    """
    from app.core.constants import IST
    from app.core.redis import get_redis as _get_redis
    from app.data_feed.fyers_ws_client import fyers_ws_client
    from app.services.candle_backfill import _backfill_symbol, _previous_trading_day

    if not symbols:
        return

    r = _get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot provision watchlist symbols")
        return

    fyers_map = {sym: f"NSE:{sym}-EQ" for sym in symbols}

    # Fetch REST quotes into Redis price cache
    try:
        await fyers_ws_client.fetch_quotes_rest(extra_symbols=fyers_map)
    except Exception:
        logger.exception("Failed to fetch quotes for watchlist symbols")

    # Backfill candles (previous day + today) so strategies have context
    prev_day = _previous_trading_day(today)
    sem = asyncio.Semaphore(FYERS_SEMAPHORE_LIMIT)

    async def _backfill_one(sym: str, fyers_sym: str) -> None:
        async with sem:
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)
            try:
                await _backfill_symbol(token, sym, fyers_sym, prev_day)
                await _backfill_symbol(token, sym, fyers_sym, today)
            except Exception:
                logger.debug("Backfill failed for %s", sym)

    await asyncio.gather(
        *[_backfill_one(sym, fsym) for sym, fsym in fyers_map.items()],
        return_exceptions=True,
    )

    # Subscribe on WebSocket for live ticks.
    # If WS is down, log and move on — _collect_dynamic_symbols() on the next
    # reconnect reads the watchlist from Redis and subscribes everything.
    if fyers_ws_client.is_connected:
        fyers_symbols = list(fyers_map.values())
        await fyers_ws_client.subscribe_symbols(fyers_symbols, symbol_map=fyers_map)
        logger.info("Subscribed %d watchlist symbols on WebSocket", len(fyers_symbols))
    else:
        logger.warning(
            "Fyers WS not connected — watchlist symbols will be subscribed on reconnect"
        )


# ---------------------------------------------------------------------------
# Data Fetching Helpers
# ---------------------------------------------------------------------------


async def _fetch_daily_data_batch(
    symbols: list[str], today: date, days: int = 60
) -> dict[str, list[Candle]]:
    """Load daily candle data from market_data_daily for the screener's 8-factor scoring.

    Primary source: market_data_daily table, populated daily by nse_bhav_copy_task.
    Fallback: Fyers resolution="D" for symbols with no rows at all (e.g. newly listed
    stocks not yet seen by the bhav copy task). Fallback data is written to
    market_data_daily so subsequent runs are DB-only.

    Logs a warning if the latest date for any symbol is older than yesterday — the
    bhav copy task is responsible for keeping this table current; stale data here
    means that task failed.
    """
    from sqlalchemy import select, func
    from app.core.database import async_session_factory
    from app.models.market_data_daily import MarketDataDaily

    from_date = today - timedelta(days=days + 30)
    yesterday = today - timedelta(days=1)
    # Tolerate weekends/holidays: require at least days-15 rows
    min_candles = days - 15

    result: dict[str, list[Candle]] = {}
    latest_dates: dict[str, date] = {}

    # Step 1: Load from market_data_daily
    async with async_session_factory() as session:
        stmt = (
            select(MarketDataDaily)
            .where(
                MarketDataDaily.symbol.in_(symbols),
                MarketDataDaily.date >= from_date,
                MarketDataDaily.date <= yesterday,
            )
            .order_by(MarketDataDaily.symbol, MarketDataDaily.date)
        )
        rows = (await session.execute(stmt)).scalars().all()

    for row in rows:
        result.setdefault(row.symbol, []).append(
            Candle(
                open=float(row.open), high=float(row.high),
                low=float(row.low), close=float(row.close),
                volume=int(row.volume),
            )
        )
        latest_dates[row.symbol] = max(latest_dates.get(row.symbol, row.date), row.date)

    sufficient = sum(1 for s in symbols if len(result.get(s, [])) >= min_candles)
    logger.info(
        "market_data_daily: %d / %d symbols have sufficient data (>=%d rows)",
        sufficient, len(symbols), min_candles,
    )

    # Warn on stale data — bhav copy task should have updated this
    stale = [s for s, d in latest_dates.items() if d < yesterday]
    if stale:
        logger.warning(
            "Daily candles stale for %d symbols (latest < %s) — bhav copy task may have failed: %s",
            len(stale), yesterday, stale[:10],
        )

    # Step 2: Fyers fallback for symbols with zero rows only
    need_fetch = [s for s in symbols if s not in result]
    if not need_fetch:
        return result

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.error("No Fyers token — %d symbols have no daily data and cannot be fetched", len(need_fetch))
        return result

    sem = asyncio.Semaphore(FYERS_SEMAPHORE_LIMIT)

    async def _fetch_one(symbol: str) -> None:
        async with sem:
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)
            client = FyersClient(access_token=token)
            try:
                fyers_symbol = f"NSE:{symbol}-EQ"
                raw = await _fyers_history_with_retry(
                    client, fyers_symbol, "D", from_date, yesterday,
                )
                if not raw:
                    return
                result[symbol] = [
                    Candle(open=c["open"], high=c["high"], low=c["low"], close=c["close"], volume=c["volume"])
                    for c in raw
                ]
                await _persist_fyers_daily_to_db(symbol, raw)
            except Exception as e:
                logger.debug("Fyers daily fallback failed for %s: %s", symbol, e)
            finally:
                await client.close()

    logger.info("Fyers fallback: fetching daily candles for %d new symbols", len(need_fetch))
    await asyncio.gather(*[_fetch_one(s) for s in need_fetch], return_exceptions=True)

    return result


async def _persist_fyers_daily_to_db(symbol: str, raw_candles: list[dict]) -> None:
    """Write Fyers resolution='D' candles into market_data_daily.

    Only called as a fallback for symbols not yet in market_data_daily.
    Subsequent days will be populated by the bhav copy task instead.
    """
    from datetime import timezone
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.database import async_session_factory
    from app.models.market_data_daily import MarketDataDaily

    rows = []
    for c in raw_candles:
        # Fyers daily timestamp is midnight UTC
        candle_date = date.fromtimestamp(c["timestamp"])
        if c.get("close", 0) <= 0:
            continue
        rows.append({
            "symbol": symbol,
            "date": candle_date,
            "open": Decimal(str(c["open"])),
            "high": Decimal(str(c["high"])),
            "low": Decimal(str(c["low"])),
            "close": Decimal(str(c["close"])),
            "volume": int(c["volume"]),
            "delivery_pct": None,
        })

    if not rows:
        return

    async with async_session_factory() as session:
        stmt = pg_insert(MarketDataDaily).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_market_data_daily_symbol_date")
        await session.execute(stmt)
        await session.commit()

    logger.info("Fyers fallback: persisted %d daily rows for %s", len(rows), symbol)


async def _fyers_history_with_retry(
    client: FyersClient,
    symbol: str,
    resolution: str,
    from_date: date,
    to_date: date,
) -> list[dict] | None:
    """Call client.get_historical_data with retry + backoff on 429."""
    import httpx

    for attempt in range(FYERS_429_MAX_RETRIES + 1):
        try:
            return await client.get_historical_data(
                symbol=symbol,
                resolution=resolution,
                from_date=from_date,
                to_date=to_date,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < FYERS_429_MAX_RETRIES:
                wait = FYERS_429_BASE_DELAY * (2 ** attempt)
                logger.debug("429 for %s, retry %d/%d in %ds", symbol, attempt + 1, FYERS_429_MAX_RETRIES, wait)
                await asyncio.sleep(wait)
                continue
            raise
        except Exception as e:
            if "request limit" in str(e).lower() and attempt < FYERS_429_MAX_RETRIES:
                wait = FYERS_429_BASE_DELAY * (2 ** attempt)
                logger.debug("Rate limit for %s, retry %d/%d in %ds", symbol, attempt + 1, FYERS_429_MAX_RETRIES, wait)
                await asyncio.sleep(wait)
                continue
            raise
    return None


# ---------------------------------------------------------------------------
# Global Cues Snapshot
# ---------------------------------------------------------------------------


async def snapshot_global_cues(today: date | None = None, force: bool = False) -> dict:
    """Package existing global market data into a Strategy 5 snapshot.

    force=True bypasses the Redis cache and re-reads from the indicator:global:*
    keys (refreshed every 15 min by the global_market_task) so the user gets the
    latest yfinance values without waiting for the next morning workflow.

    When force=True, IST-specific fields set by run_preopen_reassessment() are
    preserved (nifty_gap_pct, preopen_reassessed) since yfinance has no equivalent.
    india_vix_live is re-read from the live Fyers price cache instead of being
    carried forward, so the UI always shows the current VIX on manual refresh.
    """
    from app.core.utils import now_ist

    today = today or now_ist().date()
    r = get_redis()

    key = f"strat5:global_cues:{today}"
    if not force:
        existing = await r.get(key)
        if existing:
            return json.loads(existing)

    # On force refresh, preserve IST pre-open fields that yfinance cannot supply.
    existing_raw = await r.get(key)
    existing_cues = json.loads(existing_raw) if existing_raw else {}

    pct_fields = [
        "dow_futures_pct", "sp500_close_pct", "nasdaq_close_pct",
        "nifty_pct", "crude_pct", "usdinr_pct", "dxy_pct", "us_vix",
    ]
    price_fields = [
        "crude_price", "usdinr_price", "sp500_price", "dow_futures_price",
        "nifty_price", "nasdaq_price", "dxy_price",
    ]
    all_fields = pct_fields + price_fields
    cues: dict = {}
    for field in all_fields:
        val = await r.get(f"indicator:global:{field}")
        cues[field] = float(val) if val else None

    # Carry forward pre-open IST fields (computed from Fyers quotes at 9:08 AM,
    # not available from yfinance).
    for field in ("nifty_gap_pct", "preopen_reassessed"):
        if field in existing_cues:
            cues[field] = existing_cues[field]

    # Re-read India VIX live from Fyers price cache (continuously updated by WS feed).
    # Use r directly (same patched client as the rest of this function in tests).
    vix_raw = await r.get("price:INDIA VIX")
    if vix_raw:
        try:
            cues["india_vix_live"] = round(float(json.loads(vix_raw)["ltp"]), 2)
        except (KeyError, ValueError, TypeError):
            pass

    # VIX halt check
    vix = cues.get("us_vix")
    if vix and vix > 20:
        await r.set(f"strat5:agent_status:{today}", "HALTED", ex=REDIS_TTL)
        await _append_agent_log(today, "GLOBAL", f"VIX at {vix} — agent HALTED")
        cues["halted"] = True
    else:
        cues["halted"] = False

    # Gap flag
    nifty_pct = cues.get("nifty_pct")
    if nifty_pct and abs(nifty_pct) > 1.0:
        cues["volatile_open"] = True
        await _append_agent_log(today, "GLOBAL", f"Nifty gap {nifty_pct}% — volatile open expected")
    else:
        cues["volatile_open"] = False

    # Compute derived sentiment fields
    from app.indicators.global_market import (
        GlobalCues as GlobalCuesData,
        combined_global_score,
        overnight_bias,
    )
    from app.core.enums import DayBias

    _gcdata = GlobalCuesData(
        dow_futures_pct=cues.get("dow_futures_pct"),
        sp500_close_pct=cues.get("sp500_close_pct"),
        nasdaq_close_pct=cues.get("nasdaq_close_pct"),
        nifty_pct=cues.get("nifty_pct"),
        crude_pct=cues.get("crude_pct"),
        usdinr_pct=cues.get("usdinr_pct"),
        dxy_pct=cues.get("dxy_pct"),
        us_vix=cues.get("us_vix"),
    )
    cues["global_score"] = round(combined_global_score(_gcdata), 3)
    _bias = overnight_bias(
        cues.get("dow_futures_pct"),
        cues.get("sp500_close_pct"),
        cues.get("us_vix"),
    )
    cues["overnight_bias"] = _bias.value

    cues["date"] = str(today)
    await r.set(key, json.dumps(cues), ex=REDIS_TTL)
    return cues


# ---------------------------------------------------------------------------
# Pre-Open Reassessment (9:08 AM)
# ---------------------------------------------------------------------------

# Relative gap thresholds for bias override
_GAP_HARD_OVERRIDE = 1.0   # |relative_gap| > 1% → force bias to gap direction
_GAP_NUDGE_THRESHOLD = 0.5  # |relative_gap| 0.5-1% → nudge bias toward gap direction
_GAP_ALIGNMENT_BONUS_CAP = 5.0  # max composite score bonus for gap alignment


async def run_preopen_reassessment(as_of: date | None = None) -> dict:
    """Reassess watchlist with pre-open prices at 9:08 AM.

    Fetches live quotes for watchlist symbols + NIFTY + VIX, computes
    per-stock gap relative to Nifty, overrides bias, updates global
    cues with live VIX, re-ranks watchlist, and writes back to Redis.

    Returns summary dict with changes made.
    """
    from app.core.constants import FYERS_SYMBOL_MAP
    from app.core.utils import now_ist

    today = as_of or now_ist().date()
    r = get_redis()

    # Read existing watchlist
    wl_key = f"strat5:watchlist:{today}"
    raw = await r.get(wl_key)
    if not raw:
        logger.info("preopen_reassessment: no watchlist for %s, skipping", today)
        return {"skipped": True, "reason": "no_watchlist"}

    watchlist = json.loads(raw)
    if not watchlist:
        return {"skipped": True, "reason": "empty_watchlist"}

    # Build batch quote request: watchlist symbols + NIFTY + VIX
    stock_fyers_symbols = {}
    for item in watchlist:
        sym = item["symbol"]
        stock_fyers_symbols[sym] = f"NSE:{sym}-EQ"

    extra_symbols = {
        "NIFTY": FYERS_SYMBOL_MAP["NIFTY"],
        "INDIA VIX": FYERS_SYMBOL_MAP["INDIA VIX"],
    }

    all_fyers = list(stock_fyers_symbols.values()) + list(extra_symbols.values())

    # Single batch Fyers REST call
    client = FyersClient()
    quotes_response = await client.get_quotes(all_fyers)

    quote_data = quotes_response.get("d", [])
    if not quote_data:
        logger.warning("preopen_reassessment: empty quotes response")
        await _append_agent_log(today, "SYSTEM", "Pre-open reassessment: no quotes from Fyers")
        return {"skipped": True, "reason": "no_quotes"}

    # Parse quotes into {fyers_symbol: {ltp, volume, ...}}
    price_map: dict[str, dict] = {}
    for q in quote_data:
        v = q.get("v", {})
        fyers_sym = v.get("symbol") or q.get("n", "")
        price_map[fyers_sym] = {
            "ltp": v.get("lp", 0),
            "prev_close": v.get("prev_close_price", 0),
            "volume": v.get("volume", 0),
            "open": v.get("open_price", 0),
        }

    # Extract Nifty gap
    nifty_quote = price_map.get(extra_symbols["NIFTY"], {})
    nifty_ltp = nifty_quote.get("ltp", 0)
    nifty_prev_close = nifty_quote.get("prev_close", 0)
    nifty_gap_pct = (
        ((nifty_ltp - nifty_prev_close) / nifty_prev_close * 100)
        if nifty_prev_close > 0 else 0.0
    )

    # Extract live VIX
    vix_quote = price_map.get(extra_symbols["INDIA VIX"], {})
    live_vix = vix_quote.get("ltp", 0)

    # Update global cues with live VIX and Nifty gap
    cues_key = f"strat5:global_cues:{today}"
    cues_raw = await r.get(cues_key)
    if cues_raw:
        cues = json.loads(cues_raw)
        if live_vix > 0:
            cues["india_vix_live"] = round(live_vix, 2)
        cues["nifty_gap_pct"] = round(nifty_gap_pct, 2)
        cues["preopen_reassessed"] = True
        await r.set(cues_key, json.dumps(cues), ex=REDIS_TTL)

    # Update VIX in Redis price cache (JSON dict — same format as feed_manager.cache_price)
    if live_vix > 0:
        await r.set("price:INDIA VIX", json.dumps({"ltp": live_vix}), ex=86400)

    # Reassess each watchlist item
    changes: list[str] = []
    for item in watchlist:
        sym = item["symbol"]
        fyers_sym = stock_fyers_symbols.get(sym, "")
        quote = price_map.get(fyers_sym, {})
        preopen_price = quote.get("ltp", 0) or quote.get("open", 0)

        if preopen_price <= 0:
            continue

        pdc = item.get("pdc") or 0
        if pdc <= 0:
            continue

        # Per-stock gap
        stock_gap_pct = (preopen_price - pdc) / pdc * 100
        # Relative gap: how much is this stock gapping beyond the market
        relative_gap_pct = stock_gap_pct - nifty_gap_pct

        item["preopen_price"] = round(preopen_price, 2)
        item["gap_pct"] = round(stock_gap_pct, 2)
        item["relative_gap_pct"] = round(relative_gap_pct, 2)
        item["gap_direction"] = "UP" if stock_gap_pct > 0 else "DOWN" if stock_gap_pct < 0 else "FLAT"
        item["nifty_gap_pct"] = round(nifty_gap_pct, 2)
        item["original_bias"] = item.get("bias", "NEUTRAL")

        # Bias override based on relative gap
        new_bias = _compute_gap_adjusted_bias(item["original_bias"], relative_gap_pct)
        if new_bias != item["original_bias"]:
            changes.append(f"{sym}: {item['original_bias']} → {new_bias} (rel_gap={relative_gap_pct:+.1f}%)")
            item["bias_source"] = "preopen_gap"
        else:
            item["bias_source"] = "screener"
        item["bias"] = new_bias

        # Gap alignment bonus for re-ranking
        item["gap_alignment_bonus"] = _compute_gap_alignment_bonus(
            new_bias, stock_gap_pct,
        )
        item["composite_score"] = round(
            item["composite_score"] + item["gap_alignment_bonus"], 1,
        )

    # Re-sort by updated composite score
    watchlist.sort(key=lambda w: w.get("composite_score", 0), reverse=True)

    # Write back
    await r.set(wl_key, json.dumps(watchlist), ex=REDIS_TTL)

    # Log
    summary = {
        "symbols": len(watchlist),
        "nifty_gap_pct": round(nifty_gap_pct, 2),
        "live_vix": round(live_vix, 2) if live_vix > 0 else None,
        "bias_changes": len(changes),
        "changes": changes,
    }
    log_msg = (
        f"Pre-open reassessment: Nifty gap {nifty_gap_pct:+.1f}%, "
        f"VIX {live_vix:.1f}, {len(changes)} bias changes"
    )
    await _append_agent_log(today, "SYSTEM", log_msg)
    for change in changes:
        await _append_agent_log(today, "SYSTEM", f"Bias override: {change}")

    logger.info("preopen_reassessment: %s", log_msg)
    return summary


def _compute_gap_adjusted_bias(original_bias: str, relative_gap_pct: float) -> str:
    """Override bias based on relative gap (stock gap minus Nifty gap)."""
    abs_gap = abs(relative_gap_pct)

    # Hard override: large relative gap forces bias
    if abs_gap >= _GAP_HARD_OVERRIDE:
        return "BULLISH" if relative_gap_pct > 0 else "BEARISH"

    # Nudge: moderate relative gap pushes bias toward gap direction
    if abs_gap >= _GAP_NUDGE_THRESHOLD:
        gap_bias = "BULLISH" if relative_gap_pct > 0 else "BEARISH"
        # If original bias conflicts with gap, move to NEUTRAL
        if original_bias != gap_bias and original_bias != "NEUTRAL":
            return "NEUTRAL"
        # If original was NEUTRAL, move toward gap direction
        if original_bias == "NEUTRAL":
            return gap_bias

    return original_bias


def _compute_gap_alignment_bonus(bias: str, stock_gap_pct: float) -> float:
    """Bonus to composite score when gap direction aligns with bias."""
    if bias == "NEUTRAL":
        return 0.0

    gap_aligned = (
        (bias == "BULLISH" and stock_gap_pct > 0)
        or (bias == "BEARISH" and stock_gap_pct < 0)
    )
    if not gap_aligned:
        return 0.0

    return round(min(_GAP_ALIGNMENT_BONUS_CAP, abs(stock_gap_pct) * 2), 1)


# ---------------------------------------------------------------------------
# Agent Log + Redis Readers
# ---------------------------------------------------------------------------


async def _append_agent_log(today: date, category: str, message: str) -> None:
    """Append an entry to today's agent log in Redis."""
    r = get_redis()
    key = f"strat5:agent_log:{today}"
    entry = {
        "timestamp": time.time(),
        "category": category,
        "message": message,
    }
    await r.rpush(key, json.dumps(entry))
    await r.expire(key, REDIS_TTL)


async def get_watchlist(date_str: str) -> list[dict]:
    """Read today's watchlist from Redis, enriched with ORB levels."""
    r = get_redis()
    raw = await r.get(f"strat5:watchlist:{date_str}")
    if not raw:
        return []
    items: list[dict] = json.loads(raw)
    if not items:
        return items
    pipe = r.pipeline()
    for item in items:
        pipe.get(f"strat5:orb:{date_str}:{item['symbol']}")
    orb_results = await pipe.execute()
    for item, orb_raw in zip(items, orb_results):
        if orb_raw:
            orb = json.loads(orb_raw)
            item["orb_high"] = orb.get("high")
            item["orb_low"] = orb.get("low")
            if orb.get("high") is not None and orb.get("low") is not None:
                item["orb_range"] = round(orb["high"] - orb["low"], 2)
    return items


async def get_morning_briefing(date_str: str) -> dict:
    """Read today's morning briefing from Redis."""
    r = get_redis()
    raw = await r.get(f"strat5:morning_briefing:{date_str}")
    return json.loads(raw) if raw else {}


async def get_agent_log(
    date_str: str, *, offset: int = 0, limit: int = 0,
) -> list[dict] | tuple[list[dict], int]:
    """Read agent log from Redis.

    Without limit (default): returns all entries oldest-first (legacy).
    With limit > 0: returns (entries_newest_first, total_count) for pagination.
    """
    r = get_redis()
    key = f"strat5:agent_log:{date_str}"

    if limit <= 0:
        entries = await r.lrange(key, 0, -1)
        return [json.loads(e) for e in entries]

    total = await r.llen(key)
    if total == 0 or offset >= total:
        return [], total

    # List is stored chronologically (RPUSH). Slice from the tail for newest-first.
    start = max(0, total - offset - limit)
    end = total - offset - 1
    entries = await r.lrange(key, start, end)
    parsed = [json.loads(e) for e in entries]
    parsed.reverse()
    return parsed, total


async def get_global_cues(date_str: str) -> dict:
    """Read global cues snapshot from Redis."""
    r = get_redis()
    raw = await r.get(f"strat5:global_cues:{date_str}")
    return json.loads(raw) if raw else {}


async def get_agent_status(date_str: str) -> str:
    """Read agent status (ACTIVE/PAUSED/HALTED) from Redis."""
    r = get_redis()
    status = await r.get(f"strat5:agent_status:{date_str}")
    return status or "ACTIVE"


async def set_agent_status(date_str: str, status: str) -> None:
    """Set agent status (ACTIVE/PAUSED/HALTED) in Redis."""
    r = get_redis()
    await r.set(f"strat5:agent_status:{date_str}", status, ex=REDIS_TTL)


async def get_setup_performance(end_date: date, days: int = 5) -> dict:
    """Compute per-setup win rates and P&L for Strategy 5 over a date range."""
    from sqlalchemy import and_, func, select

    from app.core.database import async_session_factory
    from app.models.signal import Signal
    from app.models.trade import Trade

    start_date = end_date - timedelta(days=days + 2)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.strategy_name == "intraday_futures",
                    func.date(Trade.entry_time) >= start_date,
                    func.date(Trade.entry_time) <= end_date,
                    Trade.status == "CLOSED",
                )
            )
        )
        trades = result.scalars().all()

        signal_setup_map: dict[str, str] = {}
        signal_ids = [t.signal_id for t in trades if t.signal_id]
        if signal_ids:
            sig_result = await session.execute(
                select(Signal.id, Signal.indicators).where(Signal.id.in_(signal_ids))
            )
            for sig_id, indicators in sig_result.all():
                if isinstance(indicators, dict):
                    signal_setup_map[str(sig_id)] = indicators.get("setup_type", "ORB")

    setups: dict[str, dict] = {}
    for t in trades:
        setup = signal_setup_map.get(str(t.signal_id), "ORB") if t.signal_id else "ORB"
        if setup not in setups:
            setups[setup] = {"wins": 0, "losses": 0, "net_pnl": 0.0, "trades": 0}
        setups[setup]["trades"] += 1
        pnl = float(t.pnl or 0)
        setups[setup]["net_pnl"] += pnl
        if t.pnl and t.pnl > 0:
            setups[setup]["wins"] += 1
        elif t.pnl is not None:
            setups[setup]["losses"] += 1

    for s in setups.values():
        total = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / max(total, 1) * 100, 1)
        s["avg_pnl"] = round(s["net_pnl"] / max(s["trades"], 1), 2)
        s["net_pnl"] = round(s["net_pnl"], 2)

    total_wins = sum(s["wins"] for s in setups.values())
    total_losses = sum(s["losses"] for s in setups.values())
    total_pnl = sum(s["net_pnl"] for s in setups.values())
    total_trades = sum(s["trades"] for s in setups.values())

    return {
        "period": {
            "start": str(start_date),
            "end": str(end_date),
            "days": days,
        },
        "setups": setups,
        "overall": {
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": round(total_wins / max(total_wins + total_losses, 1) * 100, 1),
            "net_pnl": round(total_pnl, 2),
            "trades": total_trades,
        },
    }
    today = date.fromisoformat(date_str)
    await _append_agent_log(today, "SYSTEM", f"Agent status changed to {status}")
