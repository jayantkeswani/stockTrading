#!/usr/bin/env python3
"""Backtest CLI — replay historical data against a strategy and print a report.

Usage:
    source backend/.venv/bin/activate

    python scripts/backtest.py \\
        --strategy vwap_pullback \\
        --symbol NIFTY \\
        --start 2025-10-01 \\
        --end 2026-04-24

    # Fast mode (no Fyers API calls — delta approximation)
    python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY \\
        --start 2025-10-01 --end 2026-04-24 --mode fast

    # Include out-of-window signals (no window filter)
    python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY \\
        --start 2025-10-01 --end 2026-04-24 --no-window-filter

Available strategies: vwap_pullback, can_slim, orb
"""

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy DB/redis loggers during replay
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger("backtest-cli")


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run a strategy backtest")
    parser.add_argument("--strategy", "-s", required=True,
                        help="Strategy name (e.g. vwap_pullback, can_slim)")
    parser.add_argument("--symbol", "-y", required=True,
                        help="Symbol to backtest (e.g. NIFTY, BANKNIFTY, SENSEX)")
    parser.add_argument("--start", required=True, type=_parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_parse_date, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--mode", choices=["accurate", "fast"], default="fast",
        help="Backtest mode (default: fast — delta approximation, works for all historical dates). "
             "Use 'accurate' only for recently active contracts (Fyers retains ~few weeks).",
    )
    parser.add_argument(
        "--no-window-filter", action="store_true",
        help="Evaluate every market minute (default: only within Strategy 2 trade windows)",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Skip saving results to backtest_results/ (default: always save)",
    )
    parser.add_argument("--save-json", metavar="PATH",
                        help="Also save a standalone summary JSON to this path")
    args = parser.parse_args()

    from app.strategies.registry import get_strategy
    from app.core.enums import StrategyName

    # Resolve strategy
    try:
        strategy_enum = StrategyName(args.strategy)
    except ValueError:
        logger.error("Unknown strategy '%s'. Options: %s", args.strategy,
                     [e.value for e in StrategyName])
        sys.exit(1)

    strategy = get_strategy(strategy_enum)
    if strategy is None:
        logger.error("Strategy '%s' not registered in the registry", args.strategy)
        sys.exit(1)

    from app.backtest.harness import Backtester
    from app.backtest.report import print_report

    backtester = Backtester(
        mode=args.mode,
        window_filter=not args.no_window_filter,
    )

    logger.info(
        "Starting backtest: %s | %s | %s → %s | mode=%s",
        args.strategy, args.symbol, args.start, args.end, args.mode,
    )

    report = await backtester.run(strategy, args.symbol, args.start, args.end)
    print_report(report)

    # Auto-save to backtest_results/ (unless suppressed)
    if not args.no_save:
        from app.backtest.result_saver import save_run
        run_dir = save_run(report, report._signals_meta, report.trades)
        print(f"\n  Results saved to: {run_dir}")

    # Optional standalone JSON summary
    if args.save_json:
        import json
        from dataclasses import asdict
        out = {k: v for k, v in asdict(report).items() if k not in ("trades", "_signals_meta")}
        with open(args.save_json, "w") as f:
            json.dump(out, f, indent=2, default=str)
        logger.info("Summary JSON saved to %s", args.save_json)


if __name__ == "__main__":
    asyncio.run(main())
