"""Historical MarketContext builder for backtest replay.

Mirrors strategy_runner._build_market_context but sources all data from the
DB at a specific as_of timestamp instead of Redis / live feeds.

The builder is pure async — it takes a DB session so callers control
transaction scope and can batch-query across many timesteps efficiently.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.utils import is_trading_day
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import calculate_cpr
from app.indicators.global_market import GlobalCues, combined_global_score
from app.indicators.intraday_bias import compute_intraday_bias
from app.indicators.open_interest import analyze_option_chain
from app.indicators.previous_day import analyze_previous_day
from app.indicators.vwap import VWAPResult, calculate_vwap
from app.models.global_market_snapshot import GlobalMarketSnapshot
from app.models.market_data import MarketData1m
from app.models.oi_snapshot import OISnapshot
from app.strategies.base import MarketContext

logger = logging.getLogger(__name__)


async def build_historical_context(
    symbol: str,
    as_of: datetime,
    session: AsyncSession,
) -> MarketContext | None:
    """Build a MarketContext as it would have looked at as_of.

    Returns None if insufficient historical data exists for the given point.
    as_of must be timezone-aware (IST).
    """
    today = as_of.date()
    day_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    # --- 1m candles for today up to as_of ---
    today_candles = await _fetch_candles_1m(session, symbol, day_start, as_of)
    if not today_candles:
        return None

    current_price = float(today_candles[-1].close)

    # --- 5m bars from today's 1m candles ---
    candles_5m = _aggregate_to_5m(today_candles)
    if len(candles_5m) < 5:
        return None

    # --- VWAP from today's 1m candles ---
    highs = [c.high for c in today_candles]
    lows = [c.low for c in today_candles]
    closes = [c.close for c in today_candles]
    volumes = [c.volume for c in today_candles]
    vwap_result = calculate_vwap(highs, lows, closes, volumes)

    # --- Previous trading day levels ---
    prev_day_levels = await _get_previous_day_levels(session, symbol, today)

    # --- CPR from previous day ---
    cpr_result = None
    if prev_day_levels:
        cpr_result = calculate_cpr(
            high=prev_day_levels.pdh,
            low=prev_day_levels.pdl,
            close=prev_day_levels.pdc,
        )

    # --- OI analysis (latest snapshot <= as_of) ---
    oi_analysis = await _get_oi_analysis(session, symbol, as_of)

    # --- India VIX from INDIA VIX symbol in MarketData1m (when available) ---
    india_vix = await _get_india_vix(session, as_of)

    # --- Daily candles (for CAN SLIM — skip for VWAP-only backtest) ---
    # candles_daily = None, volume_avg_20d = None (extend later if needed)

    # --- Global cues from DB snapshot <= as_of ---
    global_cues = await _get_global_cues(session, as_of)

    # --- Intraday bias (same computation as live strategy_runner) ---
    intraday_bias = compute_intraday_bias(
        prev_day=prev_day_levels,
        candles_1m=today_candles,
        vwap=vwap_result,
        current_price=current_price,
        global_cues=global_cues,
    )

    return MarketContext(
        symbol=symbol,
        current_price=current_price,
        candles_5m=candles_5m,
        vwap=vwap_result,
        previous_day=prev_day_levels,
        cpr=cpr_result,
        oi_analysis=oi_analysis,
        india_vix=india_vix,
        current_time_ist=as_of.isoformat(),
        global_cues=global_cues,
        intraday_bias=intraday_bias,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_candles_1m(
    session: AsyncSession,
    symbol: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[Candle]:
    """Fetch 1m candles from MarketData1m between from_ts and to_ts (inclusive).

    Uses DISTINCT ON (minute) keeping the highest-volume row per minute.
    This guards against duplicate entries that arise when a backfill candle
    (clean :00 timestamp, real volume) and a live WS candle (sub-minute
    timestamp, vol=0) land in the same minute before the feed_manager
    normalization fix was deployed.
    """
    from sqlalchemy import text
    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                open, high, low, close, volume
            FROM market_data_1m
            WHERE symbol = :symbol
              AND timestamp BETWEEN :from_ts AND :to_ts
            ORDER BY date_trunc('minute', timestamp), volume DESC, timestamp
        """),
        {"symbol": symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    rows = result.all()
    return [
        Candle(
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=int(r.volume or 0),
        )
        for r in rows
    ]


def _aggregate_to_5m(candles_1m: list[Candle]) -> list[Candle]:
    """Aggregate 1m candles into 5m bars.

    Groups by (index // 5) in the ordered list — consistent with
    strategy_runner._aggregate_5m_candles which bins by minute position.
    """
    if not candles_1m:
        return []

    bars: list[Candle] = []
    group: list[Candle] = []

    for i, c in enumerate(candles_1m):
        group.append(c)
        # Complete a 5m bar every 5 candles OR at the last candle
        if len(group) == 5 or i == len(candles_1m) - 1:
            bars.append(Candle(
                open=group[0].open,
                high=max(g.high for g in group),
                low=min(g.low for g in group),
                close=group[-1].close,
                volume=sum(g.volume for g in group),
            ))
            group = []

    return bars


async def _get_previous_day_levels(
    session: AsyncSession,
    symbol: str,
    today: date,
) -> object | None:
    """Query the previous trading day's OHLC (holiday-aware) and return PreviousDayLevels."""
    from app.services.candle_backfill import _previous_trading_day

    prev_day = _previous_trading_day(today)
    prev_day_start = datetime.combine(prev_day, MARKET_OPEN, tzinfo=IST)
    prev_day_end = datetime.combine(prev_day, MARKET_CLOSE, tzinfo=IST)

    result = await session.execute(
        select(
            MarketData1m.open,
            MarketData1m.high,
            MarketData1m.low,
            MarketData1m.close,
        )
        .where(
            and_(
                MarketData1m.symbol == symbol,
                MarketData1m.timestamp >= prev_day_start,
                MarketData1m.timestamp <= prev_day_end,
            )
        )
        .order_by(MarketData1m.timestamp)
    )
    rows = result.all()
    if not rows:
        return None

    day_open = float(rows[0].open)
    day_high = max(float(r.high) for r in rows)
    day_low = min(float(r.low) for r in rows)
    day_close = float(rows[-1].close)

    return analyze_previous_day(
        open_price=day_open,
        high=day_high,
        low=day_low,
        close=day_close,
    )


async def _get_oi_analysis(
    session: AsyncSession,
    symbol: str,
    as_of: datetime,
) -> object | None:
    """Return OI analysis from the latest OISnapshot at or before as_of."""
    ts_result = await session.execute(
        select(func.max(OISnapshot.timestamp)).where(
            and_(
                OISnapshot.symbol == symbol,
                OISnapshot.timestamp <= as_of,
            )
        )
    )
    latest_ts = ts_result.scalar_one_or_none()
    if latest_ts is None:
        return None

    rows_result = await session.execute(
        select(OISnapshot).where(
            and_(
                OISnapshot.symbol == symbol,
                OISnapshot.timestamp == latest_ts,
            )
        )
    )
    snapshots = rows_result.scalars().all()
    if not snapshots:
        return None

    strike_map: dict[float, dict] = {}
    for snap in snapshots:
        sp = float(snap.strike_price)
        entry = strike_map.setdefault(sp, {
            "strike_price": sp, "ce_oi": 0, "pe_oi": 0, "ce_volume": 0, "pe_volume": 0,
        })
        if snap.option_type == "CE":
            entry["ce_oi"] = snap.open_interest
            entry["ce_volume"] = snap.volume
        elif snap.option_type == "PE":
            entry["pe_oi"] = snap.open_interest
            entry["pe_volume"] = snap.volume

    return analyze_option_chain(list(strike_map.values()))


async def _get_india_vix(
    session: AsyncSession,
    as_of: datetime,
) -> float | None:
    """Return India VIX close from the most recent INDIA VIX candle <= as_of."""
    result = await session.execute(
        select(MarketData1m.close)
        .where(
            and_(
                MarketData1m.symbol == "INDIA VIX",
                MarketData1m.timestamp <= as_of,
            )
        )
        .order_by(MarketData1m.timestamp.desc())
        .limit(1)
    )
    val = result.scalar_one_or_none()
    return float(val) if val is not None else None


async def _get_global_cues(
    session: AsyncSession,
    as_of: datetime,
) -> GlobalCues | None:
    """Return global market cues from the latest GlobalMarketSnapshot <= as_of."""
    result = await session.execute(
        select(GlobalMarketSnapshot)
        .where(GlobalMarketSnapshot.timestamp <= as_of)
        .order_by(GlobalMarketSnapshot.timestamp.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    cues = GlobalCues(
        dow_futures_pct=float(row.dow_futures_pct) if row.dow_futures_pct is not None else None,
        sp500_close_pct=float(row.sp500_close_pct) if row.sp500_close_pct is not None else None,
        nasdaq_close_pct=float(row.nasdaq_close_pct) if row.nasdaq_close_pct is not None else None,
        nifty_pct=float(row.nifty_pct) if row.nifty_pct is not None else None,
        crude_pct=float(row.crude_pct) if row.crude_pct is not None else None,
        usdinr_pct=float(row.usdinr_pct) if row.usdinr_pct is not None else None,
        dxy_pct=float(row.dxy_pct) if row.dxy_pct is not None else None,
        us_vix=float(row.us_vix) if row.us_vix is not None else None,
        pre_open_gap_pct=float(row.pre_open_gap_pct) if row.pre_open_gap_pct is not None else None,
        dow_futures_price=float(row.dow_futures_price) if row.dow_futures_price is not None else None,
        sp500_price=float(row.sp500_price) if row.sp500_price is not None else None,
        nasdaq_price=float(row.nasdaq_price) if row.nasdaq_price is not None else None,
        nifty_price=float(row.nifty_price) if row.nifty_price is not None else None,
        crude_price=float(row.crude_price) if row.crude_price is not None else None,
        usdinr_price=float(row.usdinr_price) if row.usdinr_price is not None else None,
        dxy_price=float(row.dxy_price) if row.dxy_price is not None else None,
    )
    cues.global_score = combined_global_score(cues)
    return cues
