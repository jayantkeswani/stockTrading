"""Fundamental data task — periodically fetches CAN SLIM data from yfinance + NSE.

Pattern follows oi_snapshot_task.py: periodic APScheduler task -> fetch -> persist.
Runs on startup to ensure data exists, then refreshes every 6 hours.

Data flow:
    scheduler -> fetch_fundamentals()
        -> for each CAN SLIM-configured symbol:
            -> yfinance: quarterly/annual earnings, stock info, price history
            -> NSE: shareholding pattern
            -> compute CAN SLIM component scores (C/A/N/S/L/I)
            -> upsert into stock_fundamentals table
        -> strategy_runner reads from stock_fundamentals when building MarketContext
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

RATE_LIMIT_DELAY_SECONDS = 5  # Delay between stock fetches (Yahoo Finance rate limiting)


async def fetch_fundamentals() -> int:
    """Fetch fundamental data for all CAN SLIM-configured symbols.

    Reads symbols from strategy_configs WHERE strategy_name='can_slim'.
    After per-symbol fetches, enriches with:
    - Percentile-ranked RS ratings (across the full universe)
    - F&O lot sizes from NSE

    Returns number of symbols successfully updated.
    """
    symbols = await _get_canslim_symbols()
    if not symbols:
        logger.debug("No CAN SLIM symbols configured — skipping fundamental fetch")
        return 0

    logger.info("Fetching fundamentals for %d CAN SLIM symbols", len(symbols))

    # Fetch F&O lot sizes once (used for all symbols)
    from app.data_sources import nse_client
    lot_sizes = await nse_client.get_fo_lot_sizes()
    if lot_sizes:
        logger.info("Loaded F&O lot sizes for %d symbols", len(lot_sizes))

    updated = 0
    raw_rs_scores: dict[str, float] = {}

    for symbol in symbols:
        try:
            raw_rs = await _fetch_and_store_symbol(symbol, lot_sizes)
            if raw_rs is not None:
                raw_rs_scores[symbol] = raw_rs
            updated += 1
        except Exception:
            logger.exception("Failed to fetch fundamentals for %s", symbol)

        # Rate limit between stocks
        if symbol != symbols[-1]:
            await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)

    # Percentile-rank RS ratings across all stocks and update DB
    if raw_rs_scores:
        await _update_rs_percentile_ranks(raw_rs_scores)

    logger.info("Fundamental data refresh complete: %d/%d symbols updated", updated, len(symbols))
    return updated


async def _get_canslim_symbols() -> list[str]:
    """Get symbols configured for the CAN SLIM strategy."""
    from app.core.database import async_session_factory
    from app.models.strategy_config import StrategyConfig
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig.symbols).where(
                StrategyConfig.strategy_name == "can_slim"
            )
        )
        row = result.scalar_one_or_none()

    return row or []


async def _fetch_and_store_symbol(
    symbol: str, lot_sizes: dict[str, int] | None = None
) -> float | None:
    """Fetch all fundamental data for a single symbol and upsert into DB.

    Returns the raw RS score for percentile ranking (or None if unavailable).
    The raw score is stored temporarily; it will be overwritten by the
    percentile-ranked value in _update_rs_percentile_ranks().
    """
    from app.data_sources import yfinance_client, nse_client
    from app.strategies.canslim.scoring import (
        score_a,
        score_c,
        score_i,
        score_l,
        score_n,
        score_s,
    )
    from app.core.constants import CANSLIM_SCORE_WEIGHTS

    logger.debug("Fetching fundamentals for %s", symbol)

    # Fetch data from multiple sources concurrently
    quarterly_task = yfinance_client.get_quarterly_earnings(symbol)
    annual_task = yfinance_client.get_annual_financials(symbol)
    info_task = yfinance_client.get_stock_info(symbol)
    price_task = yfinance_client.get_price_history(symbol, period="1y")
    shareholding_task = nse_client.get_shareholding_pattern(symbol)

    quarterly, annual, info, price_history, shareholding = await asyncio.gather(
        quarterly_task, annual_task, info_task, price_task, shareholding_task,
        return_exceptions=True,
    )

    # Handle exceptions from gather
    if isinstance(quarterly, Exception):
        logger.warning("Quarterly earnings fetch failed for %s: %s", symbol, quarterly)
        quarterly = []
    if isinstance(annual, Exception):
        logger.warning("Annual financials fetch failed for %s: %s", symbol, annual)
        annual = []
    if isinstance(info, Exception):
        logger.warning("Stock info fetch failed for %s: %s", symbol, info)
        info = None
    if isinstance(price_history, Exception):
        logger.warning("Price history fetch failed for %s: %s", symbol, price_history)
        price_history = []
    if isinstance(shareholding, Exception):
        logger.warning("Shareholding fetch failed for %s: %s", symbol, shareholding)
        shareholding = []

    # Extract values for scoring
    # C - Current quarterly
    latest_qtr_eps_growth = None
    latest_qtr_rev_growth = None
    eps_accelerating = None
    if quarterly and len(quarterly) >= 1:
        latest_qtr_eps_growth = quarterly[0].yoy_eps_growth_pct
        latest_qtr_rev_growth = quarterly[0].yoy_revenue_growth_pct
        if len(quarterly) >= 2:
            prev_growth = quarterly[1].yoy_eps_growth_pct
            if latest_qtr_eps_growth is not None and prev_growth is not None:
                eps_accelerating = latest_qtr_eps_growth > prev_growth

    # A - Annual
    annual_eps_growth_3yr = None
    roe = None
    opm = None
    de = None
    if annual and len(annual) >= 3:
        # 3-year EPS growth: compare latest to 3 years ago
        latest_rev = annual[0].revenue_cr
        oldest_rev = annual[2].revenue_cr
        if oldest_rev and oldest_rev > 0 and latest_rev:
            annual_eps_growth_3yr = ((latest_rev / oldest_rev) ** (1 / 3) - 1) * 100
    if annual:
        roe = annual[0].roe_pct
        opm = annual[0].operating_margin_pct
        de = annual[0].debt_to_equity

    # N - New highs
    price_52w_high = info.fifty_two_week_high if info else None
    current_price = info.current_price if info else None
    pct_from_52w_high = None
    if price_52w_high and current_price and price_52w_high > 0:
        pct_from_52w_high = ((price_52w_high - current_price) / price_52w_high) * 100

    # S - Supply
    free_float_pct = info.free_float_pct if info else None

    # L - Relative Strength (raw score — will be percentile-ranked after all symbols)
    raw_rs: float | None = None
    if price_history and len(price_history) >= 60:
        from app.indicators.relative_strength import compute_rs_raw_score
        closes = [bar.close for bar in price_history]
        raw_rs = compute_rs_raw_score(closes)

    # Store raw RS temporarily; L score uses 50 (neutral) as placeholder
    # It will be recomputed with the percentile rank in _update_rs_percentile_ranks()
    rs_for_scoring = 50.0  # Placeholder until percentile ranking

    # I - Institutional
    fii_pct = None
    fii_change_qoq = None
    mf_pct = None
    mf_change_qoq = None
    promoter_pct = None
    if shareholding and len(shareholding) >= 1:
        latest = shareholding[0]
        fii_pct = latest.fii_pct
        mf_pct = latest.mf_pct
        promoter_pct = latest.promoter_pct
        if len(shareholding) >= 2:
            prev = shareholding[1]
            fii_change_qoq = (latest.fii_pct or 0) - (prev.fii_pct or 0)
            mf_change_qoq = (latest.mf_pct or 0) - (prev.mf_pct or 0)

    # Compute individual scores (L score is placeholder, recomputed after ranking)
    c_sc = score_c(latest_qtr_eps_growth, latest_qtr_rev_growth, eps_accelerating)
    a_sc = score_a(annual_eps_growth_3yr, roe, opm)
    n_sc = score_n(pct_from_52w_high)
    s_sc = score_s(free_float_pct, 1.0, de)  # volume_ratio=1.0 (computed at eval time)
    l_sc = score_l(rs_for_scoring)
    i_sc = score_i(fii_change_qoq, mf_change_qoq)

    # Composite score (M is computed at eval time since it's market-wide)
    from app.strategies.canslim.scoring import compute_canslim_total
    # Compute without M (set M=50 as neutral default for storage)
    total = compute_canslim_total(c_sc, a_sc, n_sc, s_sc, l_sc, i_sc, 50.0)

    # Resolve lot size
    lot_size = lot_sizes.get(symbol) if lot_sizes else None

    # Upsert into stock_fundamentals
    await _upsert_fundamental(
        symbol=symbol,
        market_cap_cr=info.market_cap_cr if info else None,
        latest_qtr_eps_growth_pct=latest_qtr_eps_growth,
        latest_qtr_revenue_growth_pct=latest_qtr_rev_growth,
        eps_accelerating=eps_accelerating,
        annual_eps_growth_3yr_pct=annual_eps_growth_3yr,
        roe_pct=roe,
        operating_margin_pct=opm,
        free_float_pct=free_float_pct,
        debt_to_equity=de,
        relative_strength_rating=rs_for_scoring,
        fii_pct=fii_pct,
        fii_change_qoq=fii_change_qoq,
        mf_pct=mf_pct,
        mf_change_qoq=mf_change_qoq,
        promoter_pct=promoter_pct,
        c_score=c_sc,
        a_score=a_sc,
        n_score=n_sc,
        s_score=s_sc,
        l_score=l_sc,
        i_score=i_sc,
        canslim_score=total,
        price_52w_high=price_52w_high,
        pct_from_52w_high=pct_from_52w_high,
        lot_size=lot_size,
    )

    # Store quarterly history
    if quarterly:
        await _store_fundamental_history(symbol, quarterly, shareholding)

    return raw_rs


async def _update_rs_percentile_ranks(raw_rs_scores: dict[str, float]) -> None:
    """Percentile-rank raw RS scores and update DB with ranked values.

    Recomputes L score and total CAN SLIM score for each symbol.
    """
    from app.indicators.relative_strength import percentile_rank_rs
    from app.strategies.canslim.scoring import score_l, compute_canslim_total
    from app.core.database import async_session_factory
    from app.models.fundamental_data import StockFundamental
    from sqlalchemy import select

    ranked = percentile_rank_rs(raw_rs_scores)
    logger.info(
        "RS percentile ranks: top=%s (%.1f), bottom=%s (%.1f)",
        max(ranked, key=ranked.get),
        max(ranked.values()),
        min(ranked, key=ranked.get),
        min(ranked.values()),
    )

    async with async_session_factory() as session:
        for symbol, rs_pct in ranked.items():
            result = await session.execute(
                select(StockFundamental).where(StockFundamental.symbol == symbol)
            )
            row = result.scalar_one_or_none()
            if not row:
                continue

            # Update RS rating with percentile rank
            row.relative_strength_rating = Decimal(str(rs_pct))

            # Recompute L score and total with the real RS rating
            l_sc = score_l(rs_pct)
            row.l_score = Decimal(str(round(l_sc, 2)))

            # Recompute total using existing component scores
            total = compute_canslim_total(
                float(row.c_score or 0),
                float(row.a_score or 0),
                float(row.n_score or 0),
                float(row.s_score or 0),
                l_sc,
                float(row.i_score or 0),
                50.0,  # M computed at eval time
            )
            row.canslim_score = Decimal(str(round(total, 2)))

        await session.commit()

    logger.info("Updated RS percentile ranks for %d symbols", len(ranked))


async def _upsert_fundamental(symbol: str, **kwargs) -> None:
    """Insert or update a stock_fundamentals row."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session_factory
    from app.models.fundamental_data import StockFundamental

    now = datetime.now(IST)

    values = {
        "symbol": symbol,
        "yfinance_ticker": f"{symbol}.NS",
        "last_refreshed_at": now,
        "is_fo_eligible": True,
    }

    for key, val in kwargs.items():
        if val is not None:
            values[key] = Decimal(str(val)) if isinstance(val, float) else val

    async with async_session_factory() as session:
        stmt = pg_insert(StockFundamental).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={k: v for k, v in values.items() if k != "symbol"},
        )
        await session.execute(stmt)
        await session.commit()


async def _store_fundamental_history(symbol, quarterly, shareholding) -> None:
    """Store quarterly snapshots in fundamental_history for trend analysis."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session_factory
    from app.models.fundamental_data import FundamentalHistory

    rows = []
    for q in quarterly[:8]:  # Keep last 8 quarters
        row = {
            "symbol": symbol,
            "quarter_end": q.quarter_end,
            "eps": Decimal(str(q.eps)) if q.eps else None,
            "revenue_cr": Decimal(str(q.revenue_cr)) if q.revenue_cr else None,
        }

        # Match shareholding data to this quarter
        for sh in shareholding:
            if abs((sh.quarter_end - q.quarter_end).days) < 45:
                row["fii_pct"] = Decimal(str(sh.fii_pct)) if sh.fii_pct else None
                row["mf_pct"] = Decimal(str(sh.mf_pct)) if sh.mf_pct else None
                row["promoter_pct"] = Decimal(str(sh.promoter_pct)) if sh.promoter_pct else None
                break

        rows.append(row)

    if not rows:
        return

    async with async_session_factory() as session:
        for row in rows:
            stmt = pg_insert(FundamentalHistory).values(**row)
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_fundamental_history_symbol_quarter"
            )
            await session.execute(stmt)
        await session.commit()


async def start_fundamental_data_scheduler():
    """Start the fundamental data scheduler (06:00, 12:00, 18:00 IST)."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    for hour in (6, 12, 18):
        _scheduler.add_job(
            fetch_fundamentals,
            trigger=CronTrigger(hour=hour, minute=0, timezone=IST),
            id=f"fundamental_data_fetch_{hour}",
            name=f"Fetch CAN SLIM fundamental data ({hour:02d}:00 IST)",
            replace_existing=True,
        )
    _scheduler.start()
    logger.info("Fundamental data scheduler started (06:00, 12:00, 18:00 IST)")


async def stop_fundamental_data_scheduler():
    """Stop the fundamental data scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Fundamental data scheduler stopped")
