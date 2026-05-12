"""Backtest Strategy 5 signals: replay exits against historical 1m candles.

Queries live-generated signals from the DB (not re-generated), filters by
confidence threshold, then simulates exits using the same priority order as
trade_monitor: SL hit → target hit → trailing SL → time exit at 3:15 PM.

P&L is computed on equity 1m candles (stock futures track spot intraday,
basis is negligible).  Lot sizes from signal metadata.

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
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
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
               confidence, generated_at, lots, indicators
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
            "lots": r.lots or 1,
            "lot_size": int(lot_size) if lot_size else 1,
        })

    return signals


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


# ── Exit simulator ──────────────────────────────────────────────────────

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
) -> tuple[float, datetime, str, float, list[float]]:
    """Walk candles and simulate trade_monitor exit logic.

    sl_mode controls trailing SL trigger:
      - "close": trailing SL fires on candle close (matches live agent tick-poll
        behavior where brief wicks are missed). Initial SL still uses wicks.
      - "wick": both initial and trailing SL fire on candle high/low (legacy).

    Returns (exit_price, exit_time, exit_reason, hwm, sl_trail_history).
    """
    current_sl = stop_loss
    hwm = entry_price
    sl_history = [stop_loss]
    use_close_for_trail = sl_mode == "close"

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
) -> BacktestReport:
    strat_params = await get_strategy_params("intraday_futures")
    breakeven_pct = strat_params.get("trailing_sl_breakeven_pct", 0.5)
    trail_pct = strat_params.get("trailing_sl_trail_pct", 0.3)

    trades: list[SimTrade] = []

    async with async_session_factory() as session:
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

    args = parser.parse_args()
    end_date = args.end or args.start
    symbol_filter = args.symbols.split(",") if args.symbols else None

    async def _run():
        if args.sweep:
            await run_sweep(args.start, end_date, args.setup, symbol_filter, args.lots,
                            sl_mode=args.sl_mode)
        else:
            report = await run_backtest(
                args.start, end_date, args.confidence, args.setup, symbol_filter,
                lots_override=args.lots, sl_mode=args.sl_mode,
            )
            print_report(report, sl_mode=args.sl_mode)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
