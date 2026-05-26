"""Sector classification lookup for F&O stocks.

Two-layer lookup: DB-backed cache (auto-populated by sector_update_task)
takes priority, static JSON fallback for symbols not yet in DB.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_db_sectors: dict[str, str] = {}


@lru_cache(maxsize=1)
def _load_classification() -> dict[str, str]:
    """Load static sector map from JSON (base layer)."""
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    symbol_to_sector: dict[str, str] = {}
    for sector, symbols in data.items():
        for sym in symbols:
            symbol_to_sector[sym] = sector
    return symbol_to_sector


def get_sector(symbol: str) -> str | None:
    """Return sector for a stock. DB cache first, static JSON fallback."""
    return _db_sectors.get(symbol) or _load_classification().get(symbol)


async def load_db_sectors() -> None:
    """Load sectors from stock_fundamentals into module cache."""
    global _db_sectors
    try:
        from app.core.database import async_session_factory
        from app.models.fundamental_data import StockFundamental
        from sqlalchemy import select

        async with async_session_factory() as session:
            rows = (await session.execute(
                select(StockFundamental.symbol, StockFundamental.sector)
                .where(StockFundamental.sector.isnot(None))
            )).all()
            _db_sectors = {row.symbol: row.sector for row in rows}
            logger.info("Loaded %d sector classifications from DB", len(_db_sectors))
    except Exception:
        logger.debug("Could not load sectors from DB — using static JSON only")


def get_sector_stocks(sector: str) -> list[str]:
    """Return all stocks in a given sector."""
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    json_stocks = data.get(sector, [])
    db_stocks = [sym for sym, sec in _db_sectors.items() if sec == sector]
    return list(set(json_stocks + db_stocks))


def get_all_sectors() -> list[str]:
    """Return all sector names."""
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    all_sectors = set(data.keys())
    all_sectors.update(_db_sectors.values())
    return sorted(all_sectors)
