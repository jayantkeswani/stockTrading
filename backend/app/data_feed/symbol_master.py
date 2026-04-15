"""Fyers Symbol Master — local cache of all tradeable instruments.

Downloads CSV symbol master files from Fyers public endpoint daily,
parses them, and stores in Redis for fast local search. This avoids
hitting the Fyers API for every search query.

CSV source URLs (no auth required):
  - https://public.fyers.in/sym_details/NSE_CM.csv  (NSE equities)
  - https://public.fyers.in/sym_details/NSE_FO.csv  (NSE F&O)
  - https://public.fyers.in/sym_details/BSE_CM.csv  (BSE equities)
  - https://public.fyers.in/sym_details/BSE_FO.csv  (BSE F&O)

CSV columns (0-indexed, no header row):
  0: Fytoken           9: Fyers symbol (e.g. NSE:TCS-EQ)
  1: Display name      10: Exchange code
  2: Instrument type   11: Segment code
  3: Lot size          12: Scrip code
  4: Tick size         13: Short/underlying name
  8: Expiry epoch      15: Strike price (-1 = non-option)
                       16: Option type (CE/PE/XX)
"""

import asyncio
import csv
import gzip
import io
import json
import logging
import re
import time
import urllib.request
from datetime import datetime

from app.core.constants import IST

logger = logging.getLogger(__name__)

SYMBOL_MASTER_SOURCES = [
    ("NSE", "CM", "https://public.fyers.in/sym_details/NSE_CM.csv"),
    ("NSE", "FO", "https://public.fyers.in/sym_details/NSE_FO.csv"),
    ("BSE", "CM", "https://public.fyers.in/sym_details/BSE_CM.csv"),
    ("BSE", "FO", "https://public.fyers.in/sym_details/BSE_FO.csv"),
]

REDIS_KEY = "symbols:master"
REDIS_TS_KEY = "symbols:master:updated_at"
# Refresh interval: 24 hours
REFRESH_INTERVAL_SECONDS = 86400


def _parse_csv_row(row: list[str], exchange: str, segment: str) -> dict | None:
    """Parse a single CSV row into a compact symbol dict."""
    try:
        if len(row) < 17:
            return None

        fyers_symbol = row[9].strip()
        if not fyers_symbol:
            return None

        display_name = row[1].strip()
        short_name = row[13].strip()
        instrument_type = int(row[2]) if row[2].strip() else 0
        lot_size = int(row[3]) if row[3].strip() else 1
        strike = float(row[15]) if row[15].strip() else -1.0
        option_type = row[16].strip() if len(row) > 16 else "XX"
        expiry_epoch = row[8].strip() if len(row) > 8 else ""

        # Determine segment label
        if instrument_type == 14:
            seg_label = "OPT"
        elif instrument_type == 11:
            seg_label = "FUT"
        else:
            seg_label = "EQ"

        # Parse expiry date from epoch
        expiry_str = ""
        if expiry_epoch and expiry_epoch != "":
            try:
                expiry_ts = int(expiry_epoch)
                if expiry_ts > 0:
                    expiry_str = datetime.fromtimestamp(expiry_ts, tz=IST).strftime("%d %b %Y")
            except (ValueError, OSError):
                pass

        return {
            "s": fyers_symbol,       # symbol (Fyers format)
            "d": display_name,       # display name
            "n": short_name,         # short/underlying name (for search)
            "e": exchange,           # exchange
            "g": seg_label,          # segment: EQ/FUT/OPT
            "l": lot_size,           # lot size
            "k": strike if strike > 0 else None,  # strike price
            "t": option_type if option_type in ("CE", "PE") else None,  # option type
            "x": expiry_str,         # expiry date string
        }
    except Exception:
        return None


def _download_and_parse(exchange: str, segment: str, url: str) -> list[dict]:
    """Download a CSV file and parse into symbol dicts."""
    symbols = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StockTrading/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        reader = csv.reader(io.StringIO(content))
        for row in reader:
            parsed = _parse_csv_row(row, exchange, segment)
            if parsed:
                symbols.append(parsed)

        logger.info("Parsed %d symbols from %s_%s", len(symbols), exchange, segment)
    except Exception:
        logger.exception("Failed to download symbol master from %s", url)
    return symbols


class SymbolMaster:
    """In-memory + Redis backed symbol master for fast local search."""

    def __init__(self):
        self._symbols: list[dict] = []
        self._by_name: dict[str, list[int]] = {}  # short_name -> indices
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded and len(self._symbols) > 0

    @property
    def count(self) -> int:
        return len(self._symbols)

    async def load(self):
        """Load symbol master — try Redis cache first, download if stale/missing."""
        from app.core.redis import get_redis

        r = get_redis()

        # Check if Redis has a fresh copy
        ts = await r.get(REDIS_TS_KEY)
        if ts:
            try:
                age = time.time() - float(ts)
                if age < REFRESH_INTERVAL_SECONDS:
                    # Load from Redis
                    compressed = await r.get(REDIS_KEY)
                    if compressed:
                        data = gzip.decompress(compressed)
                        self._symbols = json.loads(data)
                        self._build_index()
                        logger.info(
                            "Symbol master loaded from Redis (%d symbols, %.0fs old)",
                            len(self._symbols), age,
                        )
                        return
            except Exception:
                logger.warning("Failed to load symbol master from Redis, will re-download")

        # Download fresh data
        await self.refresh()

    async def refresh(self):
        """Download symbol master from Fyers and store in Redis.

        Downloads run in a thread pool to avoid blocking the event loop.
        """
        logger.info("Downloading symbol master from Fyers...")
        all_symbols: list[dict] = []

        for exchange, segment, url in SYMBOL_MASTER_SOURCES:
            symbols = await asyncio.to_thread(_download_and_parse, exchange, segment, url)
            all_symbols.extend(symbols)

        if not all_symbols:
            logger.error("Symbol master download returned 0 symbols — keeping old data")
            return

        self._symbols = all_symbols
        self._build_index()
        self._loaded = True

        # Store in Redis (gzip compressed)
        try:
            from app.core.redis import get_redis

            r = get_redis()
            compressed = gzip.compress(json.dumps(all_symbols).encode())
            await r.set(REDIS_KEY, compressed)
            await r.set(REDIS_TS_KEY, str(time.time()))
            logger.info(
                "Symbol master stored in Redis (%d symbols, %.1f KB compressed)",
                len(all_symbols), len(compressed) / 1024,
            )
        except Exception:
            logger.exception("Failed to store symbol master in Redis")

    def _build_index(self):
        """Build name-to-index lookup for fast search."""
        self._by_name = {}
        for i, sym in enumerate(self._symbols):
            name = sym["n"].upper()
            self._by_name.setdefault(name, []).append(i)
        self._loaded = True

    def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Search symbols by query string.

        Supports:
          - "TCS"              → equity + futures + options for TCS
          - "TCS FUT"          → TCS futures only
          - "NIFTY 24000"      → NIFTY options near strike 24000
          - "NIFTY 24000CE"    → NIFTY 24000 CE options
          - "NIFTY 24000 CE"   → same as above
          - "RELIANCE"         → RELIANCE equity + derivatives
        """
        if not self._symbols:
            return []

        parts = query.upper().split()
        if not parts:
            return []

        name_query = parts[0]
        target_strike: float | None = None
        target_type: str | None = None
        target_segment: str | None = None

        # Parse remaining parts
        if len(parts) > 1:
            second = parts[1]
            # Check for segment filter
            if second == "FUT":
                target_segment = "FUT"
            elif second in ("CE", "PE"):
                target_type = second
                target_segment = "OPT"
            else:
                # Try strike + optional type suffix: "24000CE" or "24000"
                match = re.match(r"^(\d+(?:\.\d+)?)(CE|PE)?$", second)
                if match:
                    target_strike = float(match.group(1))
                    target_segment = "OPT"
                    if match.group(2):
                        target_type = match.group(2)

        # Third part: "NIFTY 24000 CE"
        if len(parts) > 2:
            if parts[2] in ("CE", "PE"):
                target_type = parts[2]
                target_segment = "OPT"
            elif parts[2] == "FUT":
                target_segment = "FUT"

        # Find matching symbols by name (exact match on short name first)
        indices = self._by_name.get(name_query, [])

        # If no exact match, try prefix match
        if not indices:
            for name, idxs in self._by_name.items():
                if name.startswith(name_query):
                    indices.extend(idxs)

        results = []
        for i in indices:
            sym = self._symbols[i]

            # Filter by segment
            if target_segment and sym["g"] != target_segment:
                # But always include EQ if user just typed the name with no filters
                if target_segment != "OPT" or target_strike is not None:
                    continue
                elif sym["g"] != "EQ":
                    continue

            # Filter by strike proximity
            if target_strike is not None:
                if sym["k"] is None:
                    # Not an option — skip unless it's EQ/FUT with no other filters
                    continue
                if abs(sym["k"] - target_strike) > 500:
                    continue

            # Filter by option type
            if target_type and sym.get("t") != target_type:
                continue

            results.append(sym)

        # Sort results: EQ first, then FUT, then OPT by strike proximity
        def sort_key(s: dict) -> tuple:
            seg_order = {"EQ": 0, "FUT": 1, "OPT": 2}.get(s["g"], 3)
            strike_dist = abs(s["k"] - target_strike) if target_strike and s["k"] else 0
            return (seg_order, strike_dist, s.get("k") or 0)

        results.sort(key=sort_key)
        return results[:limit]


# Singleton
symbol_master = SymbolMaster()
