"""Backtest Strategy 5 signals: replay exits against historical 1m candles.

Queries live-generated signals from the DB (not re-generated), filters by
confidence threshold, then simulates exits using the same priority order as
trade_monitor: SL hit → target hit → trailing SL → time exit at 3:15 PM.

P&L is computed on equity 1m candles (stock futures track spot intraday,
basis is negligible).  Lot sizes from signal metadata.

Thesis-invalidation exit (--invalidation): adds an *earlier* exit that fires when
the NIFTY intraday bias (the same regime read the live S5 entry gate uses) flips
against the trade for N consecutive candles. Each trade is simulated twice —
baseline and with-invalidation — so the report quantifies reversal savings (loser
cut early) vs retracement cost (premature exit that would have recovered). Guards:
--inval-persist N (consecutive candles), --inval-quorum (also require the stock to
reclaim/lose its own VWAP), --inval-moderate (MODERATE+ instead of STRONG bias).

SL modes (--sl-mode):
  - "close" (default): trailing SL fires on candle close only.  Matches the
    live agent's 2-second tick polling — brief wick touches are missed.
    Initial (hard) SL still uses wicks (a real stop order would fill).
  - "wick": both initial and trailing SL fire on candle high/low (legacy).

Usage:
    cd backend && source .venv/bin/activate

    # Single day, confidence >= 60
    python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05

    # Date range
    python scripts/backtest_strategy5.py --confidence 70 --start 2026-05-01 --end 2026-05-05

    # Filter by setup type
    python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05 --setup ORB

    # Filter by symbol
    python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05 --symbols VEDL,TCS

    # Override lots
    python scripts/backtest_strategy5.py --confidence 70 --start 2026-05-04 --lots 1

    # Legacy wick-based SL mode
    python scripts/backtest_strategy5.py --confidence 70 --start 2026-05-05 --sl-mode wick

    # Sweep mode: test multiple thresholds
    python scripts/backtest_strategy5.py --sweep --start 2026-05-01 --end 2026-05-05

    # Thesis-invalidation exit: compare baseline vs invalidation on the same trades
    python scripts/backtest_strategy5.py --confidence 70 --start 2026-04-29 \
        --end 2026-06-02 --invalidation --inval-persist 3 --inval-quorum

    # Sweep invalidation persistence x quorum at a fixed confidence
    python scripts/backtest_strategy5.py --inval-sweep --confidence 70 \
        --start 2026-04-29 --end 2026-06-02
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.backtest.context_builder import _get_global_cues, _get_previous_day_levels
from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
from app.core.enums import DayBias
from app.indicators.candle_patterns import Candle
from app.indicators.intraday_bias import IntradayBias, compute_intraday_bias
from app.indicators.vwap import calculate_vwap
from app.services.strategy_params import get_strategy_params


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class SimTrade:
    symbol: str
    signal_type: str
    setup_type: str
    confidence: float
    entry_price: float
    stop_loss: float
    target_price: float
    entry_time: datetime
    exit_price: float = 0.0
    exit_time: datetime | None = None
    exit_reason: str = ""
    lots: int = 1
    lot_size: int = 1
    pnl_per_lot: float = 0.0
    pnl_total: float = 0.0
    hwm: float = 0.0
    sl_trail_history: list[float] = field(default_factory=list)
    # Invalidation comparison (populated only when --invalidation is set)
    inval_exit_price: float = 0.0
    inval_exit_time: datetime | None = None
    inval_exit_reason: str = ""
    inval_pnl_total: float = 0.0
    inval_changed: bool = False
    inval_delta: float = 0.0
    actual_pnl: float = 0.0  # real realized P&L (only when --source shadow)


@dataclass
class BacktestReport:
    threshold: float
    start_date: date
    end_date: date
    trades: list[SimTrade]

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_per_lot > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_per_lot < 0)

    @property
    def breakeven(self) -> int:
        return sum(1 for t in self.trades if t.pnl_per_lot == 0)

    @property
    def hit_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.total_trades else 0.0

    @property
    def net_pnl(self) -> float:
        return sum(t.pnl_total for t in self.trades)

    @property
    def avg_win(self) -> float:
        winners = [t.pnl_per_lot for t in self.trades if t.pnl_per_lot > 0]
        return sum(winners) / len(winners) if winners else 0.0

    @property
    def avg_loss(self) -> float:
        losers = [t.pnl_per_lot for t in self.trades if t.pnl_per_lot < 0]
        return sum(losers) / len(losers) if losers else 0.0

    @property
    def profit_factor(self) -> float:
        gross_wins = sum(t.pnl_total for t in self.trades if t.pnl_total > 0)
        gross_losses = abs(sum(t.pnl_total for t in self.trades if t.pnl_total < 0))
        return gross_wins / gross_losses if gross_losses > 0 else float("inf")

    @property
    def expectancy(self) -> float:
        return self.net_pnl / self.total_trades if self.total_trades else 0.0


# ── DB queries ──────────────────────────────────────────────────────────

async def fetch_signals(
    session,
    start_date: date,
    end_date: date,
    min_confidence: float,
    setup_filter: str | None = None,
    symbol_filter: list[str] | None = None,
) -> list[dict]:
    from sqlalchemy import text

    query = """
        SELECT id, symbol, signal_type, entry_price, stop_loss, target_price,
               confidence, generated_at, indicators
        FROM signals
        WHERE strategy_name = 'intraday_futures'
          AND confidence >= :min_conf
          AND generated_at >= :start_ts
          AND generated_at < :end_ts
        ORDER BY generated_at
    """
    start_ts = datetime.combine(start_date, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time(0, 0), tzinfo=IST)

    result = await session.execute(
        text(query), {"min_conf": min_confidence, "start_ts": start_ts, "end_ts": end_ts}
    )
    rows = result.all()

    signals = []
    for r in rows:
        indicators = r.indicators or {}
        setup = indicators.get("setup_type", "UNKNOWN")
        if setup_filter and setup != setup_filter:
            continue
        if symbol_filter and r.symbol not in symbol_filter:
            continue

        lot_size = indicators.get("futures_lot_size", 1)
        signals.append({
            "id": str(r.id),
            "symbol": r.symbol,
            "signal_type": r.signal_type,
            "setup_type": setup,
            "entry_price": float(r.entry_price),
            "stop_loss": float(r.stop_loss),
            "target_price": float(r.target_price) if r.target_price else None,
            "confidence": float(r.confidence),
            "generated_at": r.generated_at,
            # Signals carry no lots (sizing happens at execution) — 1-lot baseline,
            # override via --lots. P&L per-lot is the size-independent metric.
            "lots": 1,
            "lot_size": int(lot_size) if lot_size else 1,
        })

    return signals


async def fetch_shadow_trades(session, start_date, end_date, min_confidence):
    """Load the ACTUAL shadow intraday_futures trades — the real, deduped execution
    population the dashboard shows. Replay from the real fill price + entry timestamp,
    using the structural SL/target from the signal snapshot. Shadow is always 1 lot
    (quantity == lot size). Carries `actual_net` (real realized P&L) for an engine check.

    Returns dicts in the same shape as fetch_signals so run_backtest is unchanged.
    """
    from sqlalchemy import text

    start_ts = datetime.combine(start_date, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    result = await session.execute(
        text("""
            SELECT symbol, side, entry_price, entry_time, quantity, signal_confidence,
                   net_pnl,
                   (signal_snapshot->>'stop_loss')::float AS snap_sl,
                   (signal_snapshot->>'target_price')::float AS snap_tgt,
                   signal_snapshot->'indicators'->>'setup_type' AS setup
            FROM trades
            WHERE strategy_name = 'intraday_futures' AND source = 'SHADOW'
              AND entry_time >= :start_ts AND entry_time < :end_ts
              AND signal_confidence >= :min_conf
              AND signal_snapshot ? 'stop_loss'
            ORDER BY entry_time
        """),
        {"min_conf": min_confidence, "start_ts": start_ts, "end_ts": end_ts},
    )
    trades = []
    for r in result.all():
        if r.snap_sl is None:
            continue
        trades.append({
            "symbol": r.symbol,
            "signal_type": "BUY_FUT" if r.side == "BUY" else "SELL_FUT",
            "setup_type": r.setup or "SHADOW",
            "entry_price": float(r.entry_price),
            "stop_loss": float(r.snap_sl),
            "target_price": float(r.snap_tgt) if r.snap_tgt is not None else None,
            "confidence": float(r.signal_confidence) if r.signal_confidence is not None else 0.0,
            "generated_at": r.entry_time,  # REAL execution timestamp
            "lots": 1,
            "lot_size": int(r.quantity) if r.quantity else 1,
            "actual_net": float(r.net_pnl) if r.net_pnl is not None else 0.0,
        })
    return trades


async def fetch_candles_after(
    session, symbol: str, from_ts: datetime, to_ts: datetime
) -> list[tuple[datetime, float, float, float, float]]:
    """Fetch 1m candles as (timestamp, open, high, low, close) tuples."""
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                timestamp, open, high, low, close
            FROM market_data_1m
            WHERE symbol = :symbol
              AND timestamp >= :from_ts
              AND timestamp <= :to_ts
            ORDER BY date_trunc('minute', timestamp), volume DESC, timestamp
        """),
        {"symbol": symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    return [
        (r.timestamp, float(r.open), float(r.high), float(r.low), float(r.close))
        for r in result.all()
    ]


# ── Intraday-bias / VWAP series for the thesis-invalidation exit ─────────

def _min_key(ts: datetime) -> datetime:
    """Floor a timestamp to its minute in UTC — a stable dict key across tz reprs."""
    return ts.astimezone(timezone.utc).replace(second=0, microsecond=0)


async def _fetch_day_candles_ts(
    session, symbol: str, from_ts: datetime, to_ts: datetime
) -> list[tuple[datetime, Candle]]:
    """Fetch (timestamp, Candle) per minute, earliest row per minute (matches
    context_builder — the backfill row carries accurate delta volume)."""
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                timestamp, open, high, low, close, volume
            FROM market_data_1m
            WHERE symbol = :symbol
              AND timestamp >= :from_ts AND timestamp <= :to_ts
            ORDER BY date_trunc('minute', timestamp), timestamp
        """),
        {"symbol": symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    return [
        (
            r.timestamp,
            Candle(open=float(r.open), high=float(r.high), low=float(r.low),
                   close=float(r.close), volume=int(r.volume or 0)),
        )
        for r in result.all()
    ]


async def _global_cues_cached(session, ts: datetime, cache: dict):
    """GlobalCues at-or-before ts, cached per 15-min bucket (cues refresh every 15 min)."""
    u = ts.astimezone(timezone.utc)
    bucket = u.replace(second=0, microsecond=0, minute=(u.minute // 15) * 15)
    if bucket not in cache:
        cache[bucket] = await _get_global_cues(session, ts)
    return cache[bucket]


async def _fetch_fut_volume(
    session, fut_symbol: str, from_ts: datetime, to_ts: datetime
) -> dict[datetime, int]:
    """Per-minute traded volume from a futures alias (e.g. ``NIFTY_FUT``), earliest
    row per minute. Index *spot* volume in market_data_1m is ~always zero, so the
    near-month future's volume is what makes the index VWAP meaningful — this
    mirrors strategy_runner._calculate_vwap_from_buffer (futures volume for indices).
    """
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                date_trunc('minute', timestamp) AS m, volume AS v
            FROM market_data_1m
            WHERE symbol = :sym AND timestamp >= :from_ts AND timestamp <= :to_ts
            ORDER BY date_trunc('minute', timestamp), timestamp
        """),
        {"sym": fut_symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    return {_min_key(r.m): int(r.v or 0) for r in result.all()}


async def build_index_bias_series(
    session, index_symbol: str, d: date, gms_cache: dict
) -> dict[datetime, IntradayBias]:
    """Compute an index's intraday bias at every minute of day d.

    Mirrors strategy_runner / context_builder: bias is recomputed from the index's
    candles-up-to-now + VWAP + previous-day levels + global cues — the same regime
    read the live gates use (S5 uses NIFTY's; S2 uses the traded index's own).
    The VWAP is weighted by the near-month FUTURES volume (``{index}_FUT``), because
    index spot volume in market_data_1m is ~always zero (matches the live VWAP).
    """
    day_start = datetime.combine(d, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(d, MARKET_CLOSE, tzinfo=IST)
    candles_ts = await _fetch_day_candles_ts(session, index_symbol, day_start, day_end)
    if not candles_ts:
        return {}

    fut_vol = await _fetch_fut_volume(session, f"{index_symbol}_FUT", day_start, day_end)
    prev_day = await _get_previous_day_levels(session, index_symbol, d)

    series: dict[datetime, IntradayBias] = {}
    running: list[Candle] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[int] = []
    for ts_i, c in candles_ts:
        running.append(c)
        highs.append(c.high)
        lows.append(c.low)
        closes.append(c.close)
        vols.append(fut_vol.get(_min_key(ts_i), 0))
        vwap = calculate_vwap(highs, lows, closes, vols)
        gcues = await _global_cues_cached(session, ts_i, gms_cache)
        series[_min_key(ts_i)] = compute_intraday_bias(
            prev_day=prev_day,
            candles_1m=running,
            vwap=vwap,
            current_price=c.close,
            global_cues=gcues,
            as_of=ts_i,
        )
    return series


async def build_stock_vwap_series(
    session, symbol: str, d: date
) -> dict[datetime, float]:
    """Cumulative VWAP at every minute for ``symbol`` on day d (for the quorum check)."""
    day_start = datetime.combine(d, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(d, MARKET_CLOSE, tzinfo=IST)
    candles_ts = await _fetch_day_candles_ts(session, symbol, day_start, day_end)

    series: dict[datetime, float] = {}
    cum_tpv = 0.0
    cum_v = 0.0
    for ts_i, c in candles_ts:
        tp = (c.high + c.low + c.close) / 3.0
        cum_tpv += tp * c.volume
        cum_v += c.volume
        series[_min_key(ts_i)] = (cum_tpv / cum_v) if cum_v > 0 else c.close
    return series


# ── Exit simulator ──────────────────────────────────────────────────────

# Optional numeric override for the invalidation trigger: when > 0, fire on an
# opposing NIFTY bias whose |score| >= this value (instead of the STRONG/MODERATE
# strength label). Lets the backtest test recalibrated band edges directly.
_INVAL_SCORE_MIN = 0.0


def simulate_exit(
    entry_price: float,
    stop_loss: float,
    target_price: float | None,
    is_long: bool,
    candles: list[tuple[datetime, float, float, float, float]],
    close_deadline: datetime,
    breakeven_pct: float = 0.5,
    trail_pct: float = 0.3,
    sl_mode: str = "close",
    nifty_bias_by_min: dict | None = None,
    stock_vwap_by_min: dict | None = None,
    inval_persist: int = 0,
    inval_require_quorum: bool = False,
    inval_strong_only: bool = True,
) -> tuple[float, datetime, str, float, list[float]]:
    """Walk candles and simulate trade_monitor exit logic.

    sl_mode controls trailing SL trigger:
      - "close": trailing SL fires on candle close (matches live agent tick-poll
        behavior where brief wicks are missed). Initial SL still uses wicks.
      - "wick": both initial and trailing SL fire on candle high/low (legacy).

    Thesis-invalidation exit (opt-in, disabled when inval_persist <= 0): on candle
    close, if the NIFTY intraday bias has flipped against the trade for
    `inval_persist` consecutive candles, exit early with reason "INVALIDATION".
    inval_strong_only requires the opposing bias to be STRONG (mirrors the live
    S5 entry gate); inval_require_quorum additionally requires the stock to have
    reclaimed (short) / lost (long) its own VWAP. SL/target/time still take
    precedence within the same candle, so invalidation only ever exits *earlier*
    than the baseline would have.

    Returns (exit_price, exit_time, exit_reason, hwm, sl_trail_history).
    """
    current_sl = stop_loss
    hwm = entry_price
    sl_history = [stop_loss]
    use_close_for_trail = sl_mode == "close"
    inval_count = 0

    for ts, o, h, l, c in candles:
        # Update HWM
        if is_long:
            if h > hwm:
                hwm = h
        else:
            if l < hwm:
                hwm = l

        is_trailing = current_sl != stop_loss

        # 1. SL check
        # Initial (hard) SL always uses wicks — a true stop order would fill.
        # Trailing SL uses close when sl_mode="close" to match the live agent's
        # tick-poll behavior (brief wick touches are often missed).
        if is_trailing and use_close_for_trail:
            sl_triggered = (is_long and c <= current_sl) or (not is_long and c >= current_sl)
        else:
            sl_triggered = (is_long and l <= current_sl) or (not is_long and h >= current_sl)

        if sl_triggered:
            reason = "TRAILING_SL" if is_trailing else "SL"
            exit_px = current_sl if not is_trailing else c
            return exit_px, ts, reason, hwm, sl_history

        # 2. Target check (wick-based)
        if target_price is not None:
            if is_long and h >= target_price:
                return target_price, ts, "TARGET", hwm, sl_history
            if not is_long and l <= target_price:
                return target_price, ts, "TARGET", hwm, sl_history

        # 2.5 Thesis-invalidation exit (opt-in, candle close): NIFTY regime has
        # flipped against the trade for `inval_persist` consecutive candles.
        if inval_persist > 0 and nifty_bias_by_min is not None:
            nb = nifty_bias_by_min.get(_min_key(ts))
            if nb is not None:
                opposing = (
                    (is_long and nb.bias == DayBias.BEARISH)
                    or (not is_long and nb.bias == DayBias.BULLISH)
                )
                if _INVAL_SCORE_MIN > 0:
                    trig = opposing and abs(nb.score) >= _INVAL_SCORE_MIN
                else:
                    trig = opposing and (not inval_strong_only or nb.strength == "STRONG")
                if trig:
                    inval_count += 1
                else:
                    inval_count = 0
                if inval_count >= inval_persist:
                    quorum_ok = True
                    if inval_require_quorum:
                        svwap = (
                            stock_vwap_by_min.get(_min_key(ts))
                            if stock_vwap_by_min else None
                        )
                        if svwap is None:
                            quorum_ok = False
                        elif is_long:
                            quorum_ok = c < svwap   # long thesis broken: lost VWAP
                        else:
                            quorum_ok = c > svwap   # short thesis broken: reclaimed VWAP
                    if quorum_ok:
                        return c, ts, "INVALIDATION", hwm, sl_history

        # 3. Trailing SL logic
        if is_long:
            gain_pct = (c - entry_price) / entry_price * 100
        else:
            gain_pct = (entry_price - c) / entry_price * 100

        # Breakeven activation
        if gain_pct >= breakeven_pct:
            if is_long and current_sl < entry_price:
                current_sl = entry_price
                sl_history.append(current_sl)
            elif not is_long and current_sl > entry_price:
                current_sl = entry_price
                sl_history.append(current_sl)

        # Progressive trail
        if is_long and current_sl >= entry_price:
            trail_sl = hwm * (1 - trail_pct / 100)
            if trail_sl > current_sl:
                current_sl = trail_sl
                sl_history.append(current_sl)
        elif not is_long and current_sl <= entry_price:
            trail_sl = hwm * (1 + trail_pct / 100)
            if trail_sl < current_sl:
                current_sl = trail_sl
                sl_history.append(current_sl)

        # 4. Time exit
        if ts >= close_deadline:
            return c, ts, "TIME_EXIT", hwm, sl_history

    # Ran out of candles (shouldn't happen if data is complete)
    if candles:
        last = candles[-1]
        return last[4], last[0], "DATA_END", hwm, sl_history
    return entry_price, close_deadline, "NO_DATA", hwm, sl_history


# ── Main backtest logic ─────────────────────────────────────────────────

async def run_backtest(
    start_date: date,
    end_date: date,
    min_confidence: float,
    setup_filter: str | None = None,
    symbol_filter: list[str] | None = None,
    quiet: bool = False,
    lots_override: int | None = None,
    sl_mode: str = "close",
    invalidation: bool = False,
    inval_persist: int = 3,
    inval_quorum: bool = False,
    inval_strong_only: bool = True,
    source: str = "signals",
    _caches: dict | None = None,
) -> BacktestReport:
    strat_params = await get_strategy_params("intraday_futures")
    breakeven_pct = strat_params.get("trailing_sl_breakeven_pct", 0.5)
    trail_pct = strat_params.get("trailing_sl_trail_pct", 0.3)

    # Caches shared across an invalidation sweep so the per-day NIFTY bias series
    # (the expensive part) is computed once and reused for every param combo.
    caches = _caches if _caches is not None else {"bias": {}, "gms": {}, "svwap": {}}

    trades: list[SimTrade] = []

    async with async_session_factory() as session:
        if source == "shadow":
            signals = await fetch_shadow_trades(session, start_date, end_date, min_confidence)
            if symbol_filter:
                signals = [s for s in signals if s["symbol"] in symbol_filter]
            if setup_filter:
                signals = [s for s in signals if s["setup_type"] == setup_filter]
        else:
            signals = await fetch_signals(
                session, start_date, end_date, min_confidence, setup_filter, symbol_filter,
            )

        if not signals:
            if not quiet:
                print(f"\nNo signals found for confidence >= {min_confidence} "
                      f"between {start_date} and {end_date}")
            return BacktestReport(
                threshold=min_confidence, start_date=start_date,
                end_date=end_date, trades=[],
            )

        if not quiet:
            print(f"\nFound {len(signals)} signals (confidence >= {min_confidence})")

        for sig in signals:
            gen_at = sig["generated_at"]
            sig_date = gen_at.date()
            is_long = sig["signal_type"] in ("BUY_FUT", "BUY_CE")

            close_deadline = datetime.combine(
                sig_date, dt_time(15, 15), tzinfo=IST,
            )
            day_end = datetime.combine(sig_date, MARKET_CLOSE, tzinfo=IST)

            candles = await fetch_candles_after(session, sig["symbol"], gen_at, day_end)

            if not candles:
                if not quiet:
                    print(f"  {sig['symbol']} {sig_date}: no candles after {gen_at.strftime('%H:%M')} — skipping")
                continue

            exit_price, exit_time, exit_reason, hwm, sl_history = simulate_exit(
                entry_price=sig["entry_price"],
                stop_loss=sig["stop_loss"],
                target_price=sig["target_price"],
                is_long=is_long,
                candles=candles,
                close_deadline=close_deadline,
                breakeven_pct=breakeven_pct,
                trail_pct=trail_pct,
                sl_mode=sl_mode,
            )

            if is_long:
                pnl_per_lot = (exit_price - sig["entry_price"]) * sig["lot_size"]
            else:
                pnl_per_lot = (sig["entry_price"] - exit_price) * sig["lot_size"]

            effective_lots = lots_override if lots_override is not None else sig["lots"]
            pnl_total = pnl_per_lot * effective_lots

            trade = SimTrade(
                symbol=sig["symbol"],
                signal_type=sig["signal_type"],
                setup_type=sig["setup_type"],
                confidence=sig["confidence"],
                entry_price=sig["entry_price"],
                stop_loss=sig["stop_loss"],
                target_price=sig["target_price"] or 0.0,
                entry_time=gen_at,
                exit_price=round(exit_price, 2),
                exit_time=exit_time,
                exit_reason=exit_reason,
                lots=effective_lots,
                lot_size=sig["lot_size"],
                pnl_per_lot=round(pnl_per_lot, 2),
                pnl_total=round(pnl_total, 2),
                hwm=round(hwm, 2),
                sl_trail_history=sl_history,
            )
            trade.actual_pnl = sig.get("actual_net", 0.0)

            if invalidation:
                bias_key = ("NIFTY", sig_date)
                nbs = caches["bias"].get(bias_key)
                if nbs is None:
                    nbs = await build_index_bias_series(session, "NIFTY", sig_date, caches["gms"])
                    caches["bias"][bias_key] = nbs
                svwap = None
                if inval_quorum:
                    ck = (sig["symbol"], sig_date)
                    svwap = caches["svwap"].get(ck)
                    if svwap is None:
                        svwap = await build_stock_vwap_series(session, sig["symbol"], sig_date)
                        caches["svwap"][ck] = svwap

                iv_price, iv_time, iv_reason, _, _ = simulate_exit(
                    entry_price=sig["entry_price"],
                    stop_loss=sig["stop_loss"],
                    target_price=sig["target_price"],
                    is_long=is_long,
                    candles=candles,
                    close_deadline=close_deadline,
                    breakeven_pct=breakeven_pct,
                    trail_pct=trail_pct,
                    sl_mode=sl_mode,
                    nifty_bias_by_min=nbs,
                    stock_vwap_by_min=svwap,
                    inval_persist=inval_persist,
                    inval_require_quorum=inval_quorum,
                    inval_strong_only=inval_strong_only,
                )
                if is_long:
                    iv_pnl_per_lot = (iv_price - sig["entry_price"]) * sig["lot_size"]
                else:
                    iv_pnl_per_lot = (sig["entry_price"] - iv_price) * sig["lot_size"]
                iv_pnl_total = iv_pnl_per_lot * effective_lots
                trade.inval_exit_price = round(iv_price, 2)
                trade.inval_exit_time = iv_time
                trade.inval_exit_reason = iv_reason
                trade.inval_pnl_total = round(iv_pnl_total, 2)
                trade.inval_changed = iv_reason == "INVALIDATION"
                trade.inval_delta = round(iv_pnl_total - trade.pnl_total, 2)

            trades.append(trade)

    return BacktestReport(
        threshold=min_confidence, start_date=start_date,
        end_date=end_date, trades=trades,
    )


# ── Report printing ─────────────────────────────────────────────────────

def print_report(report: BacktestReport, sl_mode: str = "close") -> None:
    if not report.trades:
        return

    sl_label = f"  [sl_mode={sl_mode}]" if sl_mode != "close" else ""
    print(f"\n{'='*80}")
    print(f"  STRATEGY 5 BACKTEST — Confidence >= {report.threshold}{sl_label}")
    print(f"  {report.start_date} to {report.end_date}")
    print(f"{'='*80}\n")

    # Per-trade table
    print(f"  {'Symbol':<12} {'Setup':<18} {'Dir':<6} {'Conf':>5} "
          f"{'Entry':>9} {'Exit':>9} {'Reason':<12} {'P&L/lot':>10} {'Lots':>4} {'Total':>10}")
    print(f"  {'-'*12} {'-'*18} {'-'*6} {'-'*5} "
          f"{'-'*9} {'-'*9} {'-'*12} {'-'*10} {'-'*4} {'-'*10}")

    for t in report.trades:
        direction = "LONG" if t.signal_type in ("BUY_FUT", "BUY_CE") else "SHORT"
        pnl_marker = "+" if t.pnl_per_lot > 0 else ""
        total_marker = "+" if t.pnl_total > 0 else ""
        print(
            f"  {t.symbol:<12} {t.setup_type:<18} {direction:<6} {t.confidence:>5.1f} "
            f"{t.entry_price:>9.2f} {t.exit_price:>9.2f} {t.exit_reason:<12} "
            f"{pnl_marker}{t.pnl_per_lot:>9.2f} {t.lots:>4} {total_marker}{t.pnl_total:>9.2f}"
        )

    # Summary
    print(f"\n  {'─'*80}")
    print(f"  Total trades:    {report.total_trades}")
    print(f"  Wins / Losses:   {report.wins} / {report.losses}"
          f"  (breakeven: {report.breakeven})")
    print(f"  Hit rate:        {report.hit_rate:.1f}%")
    print(f"  Avg win:         {report.avg_win:>+.2f} / lot")
    print(f"  Avg loss:        {report.avg_loss:>+.2f} / lot")
    print(f"  Profit factor:   {report.profit_factor:.2f}")
    print(f"  Expectancy:      {report.expectancy:>+.2f} / trade")
    print(f"  Net P&L:         Rs {report.net_pnl:>+,.2f}")

    # Per-setup breakdown
    setups: dict[str, list[SimTrade]] = {}
    for t in report.trades:
        setups.setdefault(t.setup_type, []).append(t)

    if len(setups) > 1:
        print(f"\n  Per-Setup Breakdown:")
        print(f"  {'Setup':<18} {'Trades':>6} {'Wins':>5} {'Rate':>6} {'Net P&L':>12}")
        print(f"  {'-'*18} {'-'*6} {'-'*5} {'-'*6} {'-'*12}")
        for setup, setup_trades in sorted(setups.items()):
            wins = sum(1 for t in setup_trades if t.pnl_per_lot > 0)
            rate = wins / len(setup_trades) * 100 if setup_trades else 0
            net = sum(t.pnl_total for t in setup_trades)
            print(f"  {setup:<18} {len(setup_trades):>6} {wins:>5} {rate:>5.1f}% {net:>+11,.2f}")

    # Per-exit-reason breakdown
    exit_reasons: dict[str, list[SimTrade]] = {}
    for t in report.trades:
        exit_reasons.setdefault(t.exit_reason, []).append(t)

    print(f"\n  Exit Reason Breakdown:")
    print(f"  {'Reason':<14} {'Count':>6} {'Net P&L':>12}")
    print(f"  {'-'*14} {'-'*6} {'-'*12}")
    for reason, reason_trades in sorted(exit_reasons.items()):
        net = sum(t.pnl_total for t in reason_trades)
        print(f"  {reason:<14} {len(reason_trades):>6} {net:>+11,.2f}")

    # Per-date summary
    dates: dict[date, list[SimTrade]] = {}
    for t in report.trades:
        d = t.entry_time.date()
        dates.setdefault(d, []).append(t)

    if len(dates) > 1:
        print(f"\n  Daily Summary:")
        print(f"  {'Date':<12} {'Trades':>6} {'Wins':>5} {'Rate':>6} {'Net P&L':>12}")
        print(f"  {'-'*12} {'-'*6} {'-'*5} {'-'*6} {'-'*12}")
        for d, day_trades in sorted(dates.items()):
            wins = sum(1 for t in day_trades if t.pnl_per_lot > 0)
            rate = wins / len(day_trades) * 100 if day_trades else 0
            net = sum(t.pnl_total for t in day_trades)
            print(f"  {d}   {len(day_trades):>6} {wins:>5} {rate:>5.1f}% {net:>+11,.2f}")

    print()


def print_invalidation_report(
    report: BacktestReport,
    persist: int,
    quorum: bool,
    strong_only: bool,
) -> None:
    """Compare baseline exits vs the thesis-invalidation exit on the same trades."""
    trades = report.trades
    if not trades:
        return

    n = len(trades)
    base_net = sum(t.pnl_total for t in trades)
    inval_net = sum(t.inval_pnl_total for t in trades)
    changed = [t for t in trades if t.inval_changed]
    helped = [t for t in changed if t.inval_delta > 0]
    hurt = [t for t in changed if t.inval_delta < 0]
    reversal_savings = sum(t.inval_delta for t in helped)
    retracement_cost = sum(t.inval_delta for t in hurt)  # <= 0

    base_wins = sum(1 for t in trades if t.pnl_total > 0)
    inval_wins = sum(1 for t in trades if t.inval_pnl_total > 0)

    qlabel = "ON" if quorum else "off"
    slabel = "STRONG-only" if strong_only else "MODERATE+"
    print(f"\n{'='*84}")
    print(f"  THESIS-INVALIDATION EXIT  vs  BASELINE")
    print(f"  trigger: NIFTY bias opposes trade for {persist} candle(s) "
          f"[{slabel}, quorum={qlabel}]")
    print(f"  Confidence >= {report.threshold:g}   {report.start_date} to {report.end_date}")
    print(f"{'='*84}\n")

    print(f"  Baseline net P&L (SL/target/trail/time):   Rs {base_net:>+15,.2f}   "
          f"hit {base_wins}/{n} ({base_wins / n * 100:.1f}%)")
    print(f"  With invalidation exit:                    Rs {inval_net:>+15,.2f}   "
          f"hit {inval_wins}/{n} ({inval_wins / n * 100:.1f}%)")
    print(f"  Net impact of invalidation:                Rs {inval_net - base_net:>+15,.2f}")
    if any(t.actual_pnl for t in trades):
        actual = sum(t.actual_pnl for t in trades)
        print(f"  Actual shadow net (real fills/exits):      Rs {actual:>+15,.2f}   "
              f"(engine check: re-sim baseline vs live)")

    print(f"\n  Trades changed by invalidation: {len(changed)} / {n}")
    print(f"    Helped (cut a wrong-way trade early): {len(helped):>3}   "
          f"reversal savings  Rs {reversal_savings:>+13,.2f}")
    print(f"    Hurt  (premature exit on retracement):{len(hurt):>3}   "
          f"retracement cost  Rs {retracement_cost:>+13,.2f}")

    print(f"\n  By direction:")
    for label, want_long in (("LONG", True), ("SHORT", False)):
        sub = [t for t in trades
               if (t.signal_type in ("BUY_FUT", "BUY_CE")) == want_long]
        if not sub:
            continue
        b = sum(t.pnl_total for t in sub)
        iv = sum(t.inval_pnl_total for t in sub)
        ch = sum(1 for t in sub if t.inval_changed)
        print(f"    {label:<6} base Rs {b:>+13,.2f}  ->  inval Rs {iv:>+13,.2f}   "
              f"(delta {iv - b:>+13,.2f}, {ch} changed)")

    print(f"\n  Daily baseline -> invalidation:")
    days: dict[date, list[SimTrade]] = {}
    for t in trades:
        days.setdefault(t.entry_time.date(), []).append(t)
    for d in sorted(days):
        dt = days[d]
        b = sum(t.pnl_total for t in dt)
        iv = sum(t.inval_pnl_total for t in dt)
        ch = sum(1 for t in dt if t.inval_changed)
        mark = f"   <-- {ch} changed" if ch else ""
        print(f"    {d}   base {b:>+13,.2f}  ->  inval {iv:>+13,.2f}   "
              f"(delta {iv - b:>+12,.2f}){mark}")

    if changed:
        print(f"\n  Changed trades (worst delta first):")
        print(f"    {'Date':<11} {'Symbol':<11} {'Dir':<5} {'BaseExit':<12} "
              f"{'BasePnL':>11} {'InvalAt':>8} {'InvalPnL':>11} {'Delta':>11}")
        for t in sorted(changed, key=lambda x: x.inval_delta):
            direction = "LONG" if t.signal_type in ("BUY_FUT", "BUY_CE") else "SHORT"
            itime = (
                t.inval_exit_time.astimezone(IST).strftime("%H:%M")
                if t.inval_exit_time else "-"
            )
            print(f"    {str(t.entry_time.date()):<11} {t.symbol:<11} {direction:<5} "
                  f"{t.exit_reason:<12} {t.pnl_total:>+11,.0f} {itime:>8} "
                  f"{t.inval_pnl_total:>+11,.0f} {t.inval_delta:>+11,.0f}")

    print()


async def run_sweep(
    start_date: date,
    end_date: date,
    setup_filter: str | None = None,
    symbol_filter: list[str] | None = None,
    lots_override: int | None = None,
    sl_mode: str = "close",
) -> None:
    """Run backtest at multiple confidence thresholds."""
    thresholds = [40, 50, 55, 60, 65, 70, 75, 80, 85, 90]

    lots_label = f" (lots={lots_override})" if lots_override is not None else ""
    sl_label = f" [sl_mode={sl_mode}]"
    print(f"\n{'='*80}")
    print(f"  CONFIDENCE SWEEP — {start_date} to {end_date}{lots_label}{sl_label}")
    print(f"{'='*80}\n")

    print(f"  {'Threshold':>9} {'Trades':>7} {'Wins':>5} {'Rate':>7} "
          f"{'Avg Win':>9} {'Avg Loss':>9} {'PF':>6} {'Net P&L':>12}")
    print(f"  {'-'*9} {'-'*7} {'-'*5} {'-'*7} "
          f"{'-'*9} {'-'*9} {'-'*6} {'-'*12}")

    for threshold in thresholds:
        report = await run_backtest(
            start_date, end_date, threshold, setup_filter, symbol_filter,
            quiet=True, lots_override=lots_override, sl_mode=sl_mode,
        )
        if report.total_trades == 0:
            print(f"  {threshold:>8.0f}% {0:>7} {'—':>5} {'—':>7} "
                  f"{'—':>9} {'—':>9} {'—':>6} {'—':>12}")
            continue

        pf = f"{report.profit_factor:.2f}" if report.profit_factor != float("inf") else "∞"
        print(
            f"  {threshold:>8.0f}% {report.total_trades:>7} {report.wins:>5} "
            f"{report.hit_rate:>6.1f}% {report.avg_win:>+8.2f} "
            f"{report.avg_loss:>+8.2f} {pf:>6} {report.net_pnl:>+11,.2f}"
        )

    print()


async def run_inval_sweep(
    start_date: date,
    end_date: date,
    min_confidence: float,
    setup_filter: str | None = None,
    symbol_filter: list[str] | None = None,
    lots_override: int | None = None,
    sl_mode: str = "close",
    strong_only: bool = True,
    source: str = "signals",
) -> None:
    """Sweep the invalidation persistence x quorum grid at a fixed confidence.

    The per-day NIFTY bias series is the expensive part; it is computed once and
    reused across every param combo via a shared cache.
    """
    caches: dict = {"bias": {}, "gms": {}, "svwap": {}}

    base = await run_backtest(
        start_date, end_date, min_confidence, setup_filter, symbol_filter,
        quiet=True, lots_override=lots_override, sl_mode=sl_mode, source=source, _caches=caches,
    )
    base_net = base.net_pnl
    n = base.total_trades
    actual_net = sum(t.actual_pnl for t in base.trades)

    slabel = "STRONG-only" if strong_only else "MODERATE+"
    lots_label = f" (lots={lots_override})" if lots_override is not None else ""
    print(f"\n{'='*92}")
    print(f"  INVALIDATION SWEEP — Confidence >= {min_confidence:g}{lots_label}  "
          f"[bias {slabel}]   {start_date} to {end_date}")
    print(f"{'='*92}\n")
    if n == 0:
        print("  No trades in range.\n")
        return
    print(f"  Baseline re-sim (no invalidation): Rs {base_net:>+14,.2f}   "
          f"{n} trades, hit {base.hit_rate:.1f}%")
    if any(t.actual_pnl for t in base.trades):
        print(f"  Actual shadow net (real fills/exits): Rs {actual_net:>+14,.2f}   "
              f"(engine check — re-sim vs live)")
    print()
    print(f"  {'persist':>7} {'quorum':>6} {'changed':>7} {'savings':>13} "
          f"{'cost':>13} {'net delta':>12} {'inval net':>14} {'hit%':>6}")
    print(f"  {'-'*7} {'-'*6} {'-'*7} {'-'*13} {'-'*13} {'-'*12} {'-'*14} {'-'*6}")

    for quorum in (False, True):
        for persist in (1, 2, 3, 5):
            r = await run_backtest(
                start_date, end_date, min_confidence, setup_filter, symbol_filter,
                quiet=True, lots_override=lots_override, sl_mode=sl_mode,
                invalidation=True, inval_persist=persist, inval_quorum=quorum,
                inval_strong_only=strong_only, source=source, _caches=caches,
            )
            changed = [t for t in r.trades if t.inval_changed]
            savings = sum(t.inval_delta for t in changed if t.inval_delta > 0)
            cost = sum(t.inval_delta for t in changed if t.inval_delta < 0)
            inval_net = sum(t.inval_pnl_total for t in r.trades)
            iv_wins = sum(1 for t in r.trades if t.inval_pnl_total > 0)
            iv_hit = iv_wins / r.total_trades * 100 if r.total_trades else 0.0
            print(f"  {persist:>7} {('ON' if quorum else 'off'):>6} {len(changed):>7} "
                  f"{savings:>+13,.0f} {cost:>+13,.0f} {inval_net - base_net:>+12,.0f} "
                  f"{inval_net:>+14,.0f} {iv_hit:>5.1f}%")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────

def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(
        description="Backtest Strategy 5 signals against historical candle data"
    )
    parser.add_argument("--confidence", type=float, default=60,
                        help="Minimum confidence threshold (default: 60)")
    parser.add_argument("--start", type=parse_date, required=True,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=parse_date, default=None,
                        help="End date inclusive (default: same as start)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbol filter (e.g. VEDL,TCS)")
    parser.add_argument("--setup", type=str, default=None,
                        help="Setup type filter (ORB, VWAP_BOUNCE, PDH_PDL, GAP_CONTINUATION)")
    parser.add_argument("--lots", type=int, default=None,
                        help="Override lot count per trade (default: use signal's recommended lots)")
    parser.add_argument("--sweep", action="store_true",
                        help="Run confidence sweep (ignores --confidence)")
    parser.add_argument("--sl-mode", choices=["close", "wick"], default="close",
                        help="Trailing SL trigger: 'close' (candle close, matches live agent) "
                             "or 'wick' (candle high/low, legacy). Default: close")
    parser.add_argument("--invalidation", action="store_true",
                        help="Compare baseline exits vs adding a thesis-invalidation exit "
                             "(NIFTY intraday-bias flip against the trade)")
    parser.add_argument("--inval-persist", type=int, default=3,
                        help="Consecutive candles of opposing NIFTY bias before invalidation "
                             "fires (default: 3)")
    parser.add_argument("--inval-quorum", action="store_true",
                        help="Also require the stock to reclaim/lose its own VWAP before "
                             "invalidation fires (quorum guard against retracements)")
    parser.add_argument("--inval-moderate", action="store_true",
                        help="Trigger on MODERATE+ opposing bias (default: STRONG only)")
    parser.add_argument("--inval-score", type=float, default=0.0,
                        help="Override the invalidation trigger: fire on opposing NIFTY bias "
                             "with |score| >= this value (e.g. 0.25 = recalibrated MODERATE band). "
                             "0 = use the STRONG/--inval-moderate strength label (default)")
    parser.add_argument("--inval-sweep", action="store_true",
                        help="Sweep invalidation persistence x quorum at --confidence")
    parser.add_argument("--source", choices=["signals", "shadow"], default="signals",
                        help="Replay raw signals (default) or the actual SHADOW trades "
                             "(real fill price + entry timestamp, deduped execution population)")

    args = parser.parse_args()
    end_date = args.end or args.start
    symbol_filter = args.symbols.split(",") if args.symbols else None
    globals()["_INVAL_SCORE_MIN"] = args.inval_score

    async def _run():
        if args.inval_sweep:
            await run_inval_sweep(
                args.start, end_date, args.confidence, args.setup, symbol_filter,
                args.lots, sl_mode=args.sl_mode, strong_only=not args.inval_moderate,
                source=args.source,
            )
        elif args.sweep:
            await run_sweep(args.start, end_date, args.setup, symbol_filter, args.lots,
                            sl_mode=args.sl_mode)
        else:
            report = await run_backtest(
                args.start, end_date, args.confidence, args.setup, symbol_filter,
                lots_override=args.lots, sl_mode=args.sl_mode,
                invalidation=args.invalidation, inval_persist=args.inval_persist,
                inval_quorum=args.inval_quorum, inval_strong_only=not args.inval_moderate,
                source=args.source,
            )
            print_report(report, sl_mode=args.sl_mode)
            if args.invalidation:
                print_invalidation_report(
                    report, args.inval_persist, args.inval_quorum,
                    not args.inval_moderate,
                )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
