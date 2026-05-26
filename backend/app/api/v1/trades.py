import uuid
from datetime import datetime, time as dt_time
from decimal import Decimal

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import StrategyName, TradeSource, TradeStatus
from app.models.market_data import MarketData1m
from app.models.trade import Trade
from app.services.brokerage_calculator import compute_charges
from app.schemas.trade import (
    HoldAnalysisRequest,
    HoldAnalysisResponse,
    MarginAnalysisRequest,
    MarginAnalysisResponse,
    PerTradeHoldResult,
    TradeResponse,
    TradeSummaryResponse,
)

_IST = pytz.timezone("Asia/Kolkata")
_MARKET_CLOSE = dt_time(15, 30)

router = APIRouter()


def _to_response(trade: Trade) -> TradeResponse:
    resp = TradeResponse.model_validate(trade)
    resp.signal_is_permanent_watchlist = trade.is_permanent_watchlist
    return resp


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    status: str | None = None,
    strategy: str | None = None,
    source: str | None = None,
    closed_since: datetime | None = None,
    entry_since: datetime | None = None,
    entry_until: datetime | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    ai_action: str | None = None,
    instrument_type: str | None = None,
    signal_type: str | None = None,
    min_lots: int | None = None,
    max_lots: int | None = None,
    exclude_permanent: bool | None = None,
    limit: int = Query(default=50, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade)
    # Default: exclude shadow trades; pass source="SHADOW" to see only shadows
    if source:
        query = query.where(Trade.source == source)
    else:
        query = query.where(Trade.source != TradeSource.SHADOW.value)
    if status:
        query = query.where(Trade.status == status)
    if strategy:
        query = query.where(Trade.strategy_name == strategy)
    if min_lots is not None:
        query = query.where(Trade.lots >= min_lots)
    if max_lots is not None:
        query = query.where(Trade.lots <= max_lots)
    if exclude_permanent:
        query = query.where(Trade.is_permanent_watchlist == False)  # noqa: E712
    # Sim filters — now on Trade's snapshotted columns
    if min_confidence is not None:
        query = query.where(Trade.signal_confidence >= min_confidence)
    if max_confidence is not None:
        query = query.where(Trade.signal_confidence <= max_confidence)
    if ai_action:
        query = query.where(Trade.signal_ai_action == ai_action)
    if instrument_type:
        query = query.where(Trade.signal_instrument_type == instrument_type)
    if signal_type:
        query = query.where(Trade.signal_type == signal_type)
    if closed_since is not None:
        query = query.where(Trade.exit_time >= closed_since)
        query = query.order_by(desc(Trade.exit_time))
    else:
        if entry_since is not None:
            query = query.where(Trade.entry_time >= entry_since)
        if entry_until is not None:
            query = query.where(Trade.entry_time <= entry_until)
        query = query.order_by(desc(Trade.entry_time))
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return [_to_response(trade) for trade in result.scalars().all()]


@router.get("/summary", response_model=TradeSummaryResponse)
async def trade_summary(
    source: str | None = None,
    exclude_permanent: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).where(Trade.status == TradeStatus.CLOSED)
    if source:
        query = query.where(Trade.source == source)
    else:
        query = query.where(Trade.source != TradeSource.SHADOW.value)
    if exclude_permanent:
        query = query.where(Trade.is_permanent_watchlist == False)  # noqa: E712
    closed = await db.execute(query)
    trades = closed.scalars().all()
    if not trades:
        return TradeSummaryResponse(
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0,
            total_pnl=0, avg_pnl=0, avg_winner=0, avg_loser=0,
            best_trade=0, worst_trade=0, profit_factor=0,
        )

    winners = [t for t in trades if t.pnl and t.pnl > 0]
    losers = [t for t in trades if t.pnl and t.pnl < 0]
    total_pnl = sum(t.pnl for t in trades if t.pnl)
    gross_profit = sum(t.pnl for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0

    total_charges = sum(
        Decimal(str(t.charges_json["total"])) if t.charges_json else Decimal(0)
        for t in trades
    )
    total_net_pnl = sum(
        t.net_pnl if t.net_pnl is not None else (t.pnl or 0)
        for t in trades
    )

    return TradeSummaryResponse(
        total_trades=len(trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=len(winners) / len(trades) * 100 if trades else 0,
        total_pnl=total_pnl,
        avg_pnl=total_pnl / len(trades) if trades else 0,
        avg_winner=gross_profit / len(winners) if winners else 0,
        avg_loser=-gross_loss / len(losers) if losers else 0,
        best_trade=max((t.pnl for t in trades if t.pnl), default=0),
        worst_trade=min((t.pnl for t in trades if t.pnl), default=0),
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else 0,
        total_net_pnl=total_net_pnl,
        total_charges=total_charges,
    )


@router.post("/margin-analysis", response_model=MarginAnalysisResponse)
async def margin_analysis(
    body: MarginAnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trade).where(Trade.id.in_(body.trade_ids))
    )
    trades = result.scalars().all()

    total_margin = sum(Decimal(str(t.margin_required or 0)) for t in trades)

    events: list[tuple[datetime, Decimal]] = []
    for t in trades:
        m = Decimal(str(t.margin_required or 0))
        if m <= 0:
            continue
        events.append((t.entry_time, m))
        if t.exit_time:
            events.append((t.exit_time, -m))
    events.sort(key=lambda e: e[0])

    running = Decimal(0)
    peak = Decimal(0)
    peak_time = None
    for ts, delta in events:
        running += delta
        if running > peak:
            peak = running
            peak_time = ts

    return MarginAnalysisResponse(
        peak_margin=peak,
        peak_time=peak_time,
        total_margin=total_margin,
        trade_count=len(trades),
    )


def _resolve_md_symbol(trade) -> str | None:
    """Return the market_data_1m symbol key for a trade, or None if unavailable."""
    return trade.fyers_option_symbol or trade.symbol


async def _build_reentry_map(
    db: AsyncSession,
    trades: list,
) -> dict[str, datetime]:
    """Build a map of trade_id → next re-entry time on same instrument+side+day.

    Queries the trades table (not just the request batch) so filtered-out
    trades are still accounted for.
    """
    reentry_map: dict[str, datetime] = {}

    groups: dict[tuple[str, str], list] = {}
    for t in trades:
        if t.status != TradeStatus.CLOSED.value or t.exit_time is None:
            continue
        md_sym = _resolve_md_symbol(t)
        if not md_sym:
            continue
        key = (md_sym, t.side)
        groups.setdefault(key, []).append(t)

    for (md_sym, side), group_trades in groups.items():
        dates = {t.exit_time.astimezone(_IST).date() for t in group_trades}
        for trading_date in dates:
            day_start = _IST.localize(datetime.combine(trading_date, dt_time(0, 0)))
            day_end = _IST.localize(datetime.combine(trading_date, dt_time(23, 59, 59)))

            sym_filter = Trade.fyers_option_symbol == md_sym

            rows = await db.execute(
                select(Trade.entry_time)
                .where(sym_filter)
                .where(Trade.side == side)
                .where(Trade.entry_time >= day_start)
                .where(Trade.entry_time <= day_end)
                .order_by(Trade.entry_time)
            )
            day_entries = [r[0] for r in rows.all()]

            day_trades = [
                t for t in group_trades
                if t.exit_time.astimezone(_IST).date() == trading_date
            ]
            for t in day_trades:
                for et in day_entries:
                    if et > t.exit_time:
                        reentry_map[str(t.id)] = et
                        break

    return reentry_map


@router.post("/hold-analysis", response_model=HoldAnalysisResponse)
async def hold_analysis(
    body: HoldAnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    """For each closed trade, query max HIGH / min LOW from 1m candles between exit and hold cutoff.

    Hold cutoff = min(next re-entry on same instrument+side, 15:30 IST same day).
    """
    if body.scenario not in ("best", "worst", "eod", "sl_tgt"):
        raise HTTPException(status_code=400, detail="scenario must be 'best', 'worst', 'eod', or 'sl_tgt'")

    result = await db.execute(
        select(Trade).where(Trade.id.in_(body.trade_ids))
    )
    trades = result.scalars().all()

    reentry_map = await _build_reentry_map(db, trades)

    results: list[PerTradeHoldResult] = []
    for trade in trades:
        if trade.status != TradeStatus.CLOSED.value or trade.exit_time is None:
            results.append(PerTradeHoldResult(trade_id=trade.id, data_found=False))
            continue

        md_symbol = _resolve_md_symbol(trade)
        if not md_symbol:
            results.append(PerTradeHoldResult(trade_id=trade.id, data_found=False))
            continue

        exit_ist = trade.exit_time.astimezone(_IST)
        market_close = _IST.localize(datetime.combine(exit_ist.date(), _MARKET_CLOSE))
        reentry_time = reentry_map.get(str(trade.id))
        cutoff = min(reentry_time, market_close) if reentry_time else market_close

        if cutoff <= trade.entry_time:
            results.append(PerTradeHoldResult(trade_id=trade.id, data_found=False))
            continue

        agg = await db.execute(
            select(func.max(MarketData1m.high), func.min(MarketData1m.low))
            .where(MarketData1m.symbol == md_symbol)
            .where(MarketData1m.timestamp >= trade.entry_time)
            .where(MarketData1m.timestamp <= cutoff)
        )
        row = agg.one_or_none()
        max_high = row[0] if row else None
        min_low = row[1] if row else None

        if max_high is None or min_low is None:
            results.append(PerTradeHoldResult(trade_id=trade.id, data_found=False))
            continue

        hypo_exit = None
        hold_exit_time = None
        hold_outcome: str | None = None

        if body.scenario == "sl_tgt":
            sl = trade.stop_loss
            tgt = trade.target_price
            if sl is not None and tgt is not None:
                candles = await db.execute(
                    select(MarketData1m.high, MarketData1m.low, MarketData1m.timestamp)
                    .where(MarketData1m.symbol == md_symbol)
                    .where(MarketData1m.timestamp >= trade.entry_time)
                    .where(MarketData1m.timestamp <= cutoff)
                    .order_by(MarketData1m.timestamp)
                )
                for high, low, ts in candles.all():
                    if trade.side == "BUY":
                        sl_hit = low <= sl
                        tgt_hit = high >= tgt
                    else:
                        sl_hit = high >= sl
                        tgt_hit = low <= tgt
                    if sl_hit:
                        hypo_exit = sl
                        hold_exit_time = ts
                        hold_outcome = "SL"
                        break
                    if tgt_hit:
                        hypo_exit = tgt
                        hold_exit_time = ts
                        hold_outcome = "TGT"
                        break
            if hypo_exit is None:
                eod_row = await db.execute(
                    select(MarketData1m.close, MarketData1m.timestamp)
                    .where(MarketData1m.symbol == md_symbol)
                    .where(MarketData1m.timestamp >= trade.entry_time)
                    .where(MarketData1m.timestamp <= cutoff)
                    .order_by(MarketData1m.timestamp.desc())
                    .limit(1)
                )
                eod = eod_row.one_or_none()
                hypo_exit = eod[0] if eod else None
                hold_exit_time = eod[1] if eod else None
        elif body.scenario == "eod":
            eod_row = await db.execute(
                select(MarketData1m.close, MarketData1m.timestamp)
                .where(MarketData1m.symbol == md_symbol)
                .where(MarketData1m.timestamp >= trade.entry_time)
                .where(MarketData1m.timestamp <= cutoff)
                .order_by(MarketData1m.timestamp.desc())
                .limit(1)
            )
            eod = eod_row.one_or_none()
            hypo_exit = eod[0] if eod else None
            hold_exit_time = eod[1] if eod else None
        else:
            hypo_exit = max_high if body.scenario == "best" else min_low
            target_col = MarketData1m.high if body.scenario == "best" else MarketData1m.low
            time_row = await db.execute(
                select(MarketData1m.timestamp)
                .where(MarketData1m.symbol == md_symbol)
                .where(MarketData1m.timestamp >= trade.entry_time)
                .where(MarketData1m.timestamp <= cutoff)
                .where(target_col == hypo_exit)
                .order_by(MarketData1m.timestamp)
                .limit(1)
            )
            hold_exit_time = time_row.scalar_one_or_none()

        if hypo_exit is None:
            results.append(PerTradeHoldResult(trade_id=trade.id, data_found=False))
            continue

        entry = Decimal(str(trade.entry_price))
        qty = int(trade.quantity)
        diff = (entry - hypo_exit) if trade.side == "SELL" else (hypo_exit - entry)
        hold_pnl = diff * qty

        instrument_type = "OPTION" if trade.option_type else "FUTURE"
        charges = compute_charges(instrument_type, entry, hypo_exit, qty, trade.side)
        hold_net_pnl = hold_pnl - charges.total

        results.append(PerTradeHoldResult(
            trade_id=trade.id,
            hold_exit_price=hypo_exit,
            hold_pnl=hold_pnl,
            hold_net_pnl=hold_net_pnl,
            hold_charges_json=charges.to_dict(),
            hold_exit_time=hold_exit_time,
            hold_outcome=hold_outcome,
            data_found=True,
        ))

    return HoldAnalysisResponse(results=results)


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _to_response(trade)


