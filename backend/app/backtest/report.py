"""BacktestReport — aggregates SimulatedTrade results into statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.backtest.exit_simulator import SimulatedTrade


@dataclass
class BacktestReport:
    strategy_name: str
    symbol: str
    start_date: str
    end_date: str
    mode: str  # "accurate" or "fast"

    total_signals: int = 0
    total_trades: int = 0        # Signals that fired (entry occurred)
    ce_trades: int = 0
    pe_trades: int = 0

    wins: int = 0
    losses: int = 0
    no_data: int = 0
    accurate_fallbacks: int = 0   # Trades where accurate mode fell back to fast (expired contracts)

    total_pnl_points: float = 0.0   # Sum of pnl_per_lot / lot_size (net points)
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0     # (win_rate * avg_win) + (loss_rate * avg_loss)
    profit_factor: float = 0.0      # gross_wins / abs(gross_losses)

    oi_coverage_pct: float = 0.0    # % of signals where OI data was available

    # Confidence calibration: bucket → (trades, wins)
    confidence_buckets: dict[str, dict] = field(default_factory=dict)

    trades: list[SimulatedTrade] = field(default_factory=list)
    # Carried from harness for save_run (not serialised into the report itself)
    _signals_meta: list[dict] = field(default_factory=list, repr=False)


def build_report(
    strategy_name: str,
    symbol: str,
    start_date: str,
    end_date: str,
    mode: str,
    trades: list[SimulatedTrade],
    signals_meta: list[dict],  # [{confidence, signal_type, oi_available}, ...]
) -> BacktestReport:
    from app.backtest.exit_simulator import ExitReason

    r = BacktestReport(
        strategy_name=strategy_name,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        total_signals=len(signals_meta),
        total_trades=len(trades),
        trades=trades,
    )

    win_pcts: list[float] = []
    loss_pcts: list[float] = []
    gross_wins = 0.0
    gross_losses = 0.0

    oi_available = sum(1 for m in signals_meta if m.get("oi_available"))
    r.oi_coverage_pct = (oi_available / len(signals_meta) * 100) if signals_meta else 0.0

    for i, trade in enumerate(trades):
        meta = signals_meta[i] if i < len(signals_meta) else {}
        signal_type = meta.get("signal_type", "")
        if "CE" in signal_type:
            r.ce_trades += 1
        elif "PE" in signal_type:
            r.pe_trades += 1

        if meta.get("effective_mode") == "fast_fallback":
            r.accurate_fallbacks += 1

        if trade.exit_reason == ExitReason.NO_DATA:
            r.no_data += 1
            continue

        if trade.pnl_pct > 0:
            r.wins += 1
            win_pcts.append(trade.pnl_pct)
            gross_wins += trade.pnl_pct
        else:
            r.losses += 1
            loss_pcts.append(trade.pnl_pct)
            gross_losses += abs(trade.pnl_pct)

        r.total_pnl_points += trade.pnl_per_lot

        # Confidence bucket calibration
        conf = meta.get("confidence")
        if conf is not None:
            bucket = _conf_bucket(conf)
            b = r.confidence_buckets.setdefault(bucket, {"trades": 0, "wins": 0, "hit_rate": 0.0})
            b["trades"] += 1
            if trade.pnl_pct > 0:
                b["wins"] += 1

    settled = r.wins + r.losses
    win_rate = r.wins / settled if settled else 0.0
    loss_rate = r.losses / settled if settled else 0.0

    r.avg_win_pct = sum(win_pcts) / len(win_pcts) if win_pcts else 0.0
    r.avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0
    r.expectancy_pct = (win_rate * r.avg_win_pct) + (loss_rate * (-r.avg_loss_pct))
    r.profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    for bucket_data in r.confidence_buckets.values():
        t = bucket_data["trades"]
        w = bucket_data["wins"]
        bucket_data["hit_rate"] = round(w / t * 100, 1) if t else 0.0

    return r


def _conf_bucket(confidence: float) -> str:
    if confidence < 55:
        return "<55"
    if confidence < 65:
        return "55-64"
    if confidence < 75:
        return "65-74"
    if confidence < 85:
        return "75-84"
    return "85+"


def print_report(r: BacktestReport) -> None:
    settled = r.wins + r.losses
    win_rate = r.wins / settled * 100 if settled else 0.0

    print(f"\n{'='*60}")
    print(f"  Backtest Report: {r.strategy_name} | {r.symbol}")
    print(f"  {r.start_date} → {r.end_date}  [mode: {r.mode}]")
    print(f"{'='*60}")
    print(f"  Signals generated : {r.total_signals}")
    print(f"  Trades simulated  : {r.total_trades}  (CE: {r.ce_trades}, PE: {r.pe_trades})")
    print(f"  No-data skips     : {r.no_data}")
    if r.accurate_fallbacks:
        print(f"  Accurate→fast     : {r.accurate_fallbacks}  (expired contracts, used delta approx)")
    print(f"  Wins / Losses     : {r.wins} / {r.losses}  (hit rate: {win_rate:.1f}%)")
    print(f"  Avg win           : {r.avg_win_pct:+.1f}%")
    print(f"  Avg loss          : {-r.avg_loss_pct:.1f}%")
    print(f"  Expectancy        : {r.expectancy_pct:+.2f}% per trade")
    print(f"  Profit factor     : {r.profit_factor:.2f}")
    print(f"  Total P&L pts     : {r.total_pnl_points:+.0f}")
    print(f"  OI coverage       : {r.oi_coverage_pct:.0f}%")

    if r.confidence_buckets:
        print(f"\n  Confidence calibration:")
        for bucket in sorted(r.confidence_buckets):
            b = r.confidence_buckets[bucket]
            print(f"    [{bucket:>5}]: {b['trades']:3d} trades, {b['hit_rate']:5.1f}% hit rate")
    print()
