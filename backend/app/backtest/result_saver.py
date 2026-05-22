"""Save backtest results to structured files for later review and UI display.

Output directory: backtest_results/{strategy}_{symbol}_{start}_{end}_{YYYYMMDD_HHMMSS}/
  summary.json  — all report stats + run metadata
  trades.csv    — one row per simulated trade (full details)
  signals.csv   — one row per generated signal (metadata)

The backtest_results/ directory lives at the repo root so it's easy to find
and git-ignored. It is NOT part of the backend package.
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.backtest.exit_simulator import SimulatedTrade
    from app.backtest.report import BacktestReport

logger = logging.getLogger(__name__)

# Repo root → backtest_results/
_RESULTS_ROOT = Path(__file__).parent.parent.parent.parent.parent / "backtest_results"


def save_run(
    report: "BacktestReport",
    signals_meta: list[dict],
    trades: list["SimulatedTrade"],
) -> Path:
    """Persist a completed backtest run to disk. Returns the run directory path."""
    _RESULTS_ROOT.mkdir(exist_ok=True)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = f"{report.strategy_name}_{report.symbol}_{report.start_date}_{report.end_date}_{run_ts}"
    run_dir = _RESULTS_ROOT / dir_name
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_summary(run_dir, report, run_ts)
    _write_trades_csv(run_dir, trades, signals_meta)
    _write_signals_csv(run_dir, signals_meta)

    logger.info("Backtest results saved to %s", run_dir)
    return run_dir


def _write_summary(run_dir: Path, report: "BacktestReport", run_ts: str) -> None:
    """Write summary.json with all report stats and run metadata to the run directory."""
    settled = report.wins + report.losses
    win_rate = round(report.wins / settled * 100, 2) if settled else 0.0

    summary = {
        "run_timestamp": run_ts,
        "strategy_name": report.strategy_name,
        "symbol": report.symbol,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "mode": report.mode,
        "total_signals": report.total_signals,
        "total_trades": report.total_trades,
        "ce_trades": report.ce_trades,
        "pe_trades": report.pe_trades,
        "no_data": report.no_data,
        "accurate_fallbacks": report.accurate_fallbacks,
        "wins": report.wins,
        "losses": report.losses,
        "win_rate_pct": win_rate,
        "avg_win_pct": round(report.avg_win_pct, 3),
        "avg_loss_pct": round(report.avg_loss_pct, 3),
        "expectancy_pct": round(report.expectancy_pct, 3),
        "profit_factor": round(report.profit_factor, 3) if report.profit_factor != float("inf") else None,
        "total_pnl_points": round(report.total_pnl_points, 2),
        "oi_coverage_pct": round(report.oi_coverage_pct, 1),
        "confidence_buckets": report.confidence_buckets,
    }

    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)


def _write_trades_csv(
    run_dir: Path,
    trades: list["SimulatedTrade"],
    signals_meta: list[dict],
) -> None:
    """Write trades.csv with one row per simulated trade (entry/exit times, PnL, exit reason)."""
    fieldnames = [
        "trade_num",
        "date",
        "entry_time",
        "exit_time",
        "signal_type",
        "direction",
        "strike",
        "expiry",
        "fyers_option_symbol",
        "entry_premium",
        "exit_premium",
        "pnl_per_lot",
        "pnl_pct",
        "exit_reason",
        "mode",
        "effective_mode",
        "lots",
        "confidence",
        "oi_available",
        "index_price_at_entry",
    ]

    with open(run_dir / "trades.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, trade in enumerate(trades):
            meta = signals_meta[i] if i < len(signals_meta) else {}
            entry_ts = trade.entry_ts
            exit_ts = trade.exit_ts

            writer.writerow({
                "trade_num": i + 1,
                "date": entry_ts.strftime("%Y-%m-%d") if entry_ts else "",
                "entry_time": entry_ts.strftime("%H:%M") if entry_ts else "",
                "exit_time": exit_ts.strftime("%H:%M") if exit_ts else "",
                "signal_type": meta.get("signal_type", ""),
                "direction": "CE" if "CE" in meta.get("signal_type", "") else "PE",
                "strike": meta.get("strike", ""),
                "expiry": meta.get("expiry", ""),
                "fyers_option_symbol": trade.fyers_option_symbol or "",
                "entry_premium": round(trade.entry_premium, 2),
                "exit_premium": round(trade.exit_premium, 2) if trade.exit_premium is not None else "",
                "pnl_per_lot": round(trade.pnl_per_lot, 2),
                "pnl_pct": round(trade.pnl_pct, 2),
                "exit_reason": trade.exit_reason.value if hasattr(trade.exit_reason, "value") else str(trade.exit_reason),
                "mode": trade.mode,
                "effective_mode": meta.get("effective_mode", trade.mode),
                "lots": trade.lots,
                "confidence": meta.get("confidence", ""),
                "oi_available": meta.get("oi_available", ""),
                "index_price_at_entry": meta.get("entry_price_index", ""),
            })


def _write_signals_csv(run_dir: Path, signals_meta: list[dict]) -> None:
    """Write signals.csv with one row per generated signal (confidence, OI availability, entry price)."""
    if not signals_meta:
        return

    fieldnames = sorted({k for m in signals_meta for k in m.keys()})
    fieldnames = ["signal_num"] + [f for f in fieldnames if f not in ("signal_num",)]

    with open(run_dir / "signals.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i, meta in enumerate(signals_meta):
            row = {"signal_num": i + 1, **meta}
            writer.writerow(row)
