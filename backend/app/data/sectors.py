"""Sector classification lookup for F&O stocks."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_classification() -> dict[str, str]:
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    symbol_to_sector: dict[str, str] = {}
    for sector, symbols in data.items():
        for sym in symbols:
            symbol_to_sector[sym] = sector
    return symbol_to_sector


def get_sector(symbol: str) -> str | None:
    """Return the sector for a given stock symbol, or None if unknown."""
    return _load_classification().get(symbol)


def get_sector_stocks(sector: str) -> list[str]:
    """Return all stocks in a given sector."""
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    return data.get(sector, [])


def get_all_sectors() -> list[str]:
    """Return all sector names."""
    path = Path(__file__).parent / "sector_classification.json"
    with open(path) as f:
        data: dict[str, list[str]] = json.load(f)
    return list(data.keys())
