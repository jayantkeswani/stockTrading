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
import io
import json
import logging
import re
import time
import urllib.request
from datetime import datetime

from app.core.constants import IST

logger = logging.getLogger(__name__)

def _build_symbol_master_sources() -> list[tuple[str, str, str]]:
    from app.config import settings
    if settings.market_mode == "simulated":
        base = f"{settings.simulator_url}/sym_details"
    else:
        base = "https://public.fyers.in/sym_details"
    return [
        ("NSE", "CM", f"{base}/NSE_CM.csv"),
        ("NSE", "FO", f"{base}/NSE_FO.csv"),
        ("BSE", "CM", f"{base}/BSE_CM.csv"),
        ("BSE", "FO", f"{base}/BSE_FO.csv"),
    ]


SYMBOL_MASTER_SOURCES = _build_symbol_master_sources()

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
        lot_size = int(row[3]) if row[3].strip() else 1
        strike = float(row[15]) if row[15].strip() else -1.0
        option_type = row[16].strip() if len(row) > 16 else "XX"
        expiry_epoch = row[8].strip() if len(row) > 8 else ""

        # Determine segment label from authoritative per-row fields, NOT from the
        # instrument-type code alone. Fyers col[2] uses 11=index-future,
        # 13=stock-future, 14=index-option, 15=stock-option; a bare 11/14
        # whitelist mis-tagged every stock future and stock option as EQ — which
        # made resolve_futures_contract("BSE") fall through to BSE:BANKEX...FUT.
        # Strike / option-type and the symbol suffix are exchange- and
        # code-agnostic, so derive from those instead.
        if option_type in ("CE", "PE") or strike > 0:
            seg_label = "OPT"
        elif fyers_symbol.upper().endswith("FUT"):
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
        """True if symbol master has been loaded and contains at least one symbol."""
        return self._loaded and len(self._symbols) > 0

    @property
    def count(self) -> int:
        """Total number of symbols currently in memory."""
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
                    raw = await r.get(REDIS_KEY)
                    if raw:
                        self._symbols = json.loads(raw)
                        self._build_index()
                        logger.info(
                            "Symbol master loaded from Redis (%d symbols, %.0fs old)",
                            len(self._symbols), age,
                        )
                        return
            except Exception as e:
                logger.warning("Failed to load symbol master from Redis: %s — will re-download", e)

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
        self._log_segment_sanity()

        # Store in Redis as plain JSON (decode_responses=True on pool requires string values)
        try:
            from app.core.redis import get_redis

            r = get_redis()
            payload = json.dumps(all_symbols)
            await r.set(REDIS_KEY, payload)
            await r.set(REDIS_TS_KEY, str(time.time()))
            logger.info(
                "Symbol master stored in Redis (%d symbols, %.1f KB)",
                len(all_symbols), len(payload) / 1024,
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

    def _log_segment_sanity(self):
        """Log segment-tag distribution and warn on implausible counts.

        A guardrail against silent mis-segmentation (e.g. all stock futures
        landing in EQ). The NSE F&O universe always carries hundreds of stock
        futures and tens of thousands of options; a near-zero FUT/OPT count
        means the parser is mis-tagging and downstream resolvers will pick the
        wrong contract.
        """
        from collections import Counter

        counts = Counter((s["e"], s["g"]) for s in self._symbols)
        logger.info(
            "Symbol master segments: %s",
            {f"{e}:{g}": n for (e, g), n in sorted(counts.items())},
        )
        nse_fut = counts.get(("NSE", "FUT"), 0)
        nse_opt = counts.get(("NSE", "OPT"), 0)
        if nse_fut < 100:
            logger.warning(
                "Symbol master sanity: only %d NSE FUT symbols (expected >100) "
                "— segment parsing may be broken; futures resolution at risk",
                nse_fut,
            )
        if nse_opt < 1000:
            logger.warning(
                "Symbol master sanity: only %d NSE OPT symbols (expected >1000) "
                "— segment parsing may be broken",
                nse_opt,
            )

    def search(
        self,
        query: str,
        limit: int = 25,
    ) -> list[dict]:
        """Search symbols by query string.

        Supports:
          - "TCS"              → equity + futures + options for TCS
          - "TCS FUT"          → TCS futures only
          - "NIFTY 24000"      → NIFTY options near strike 24000
          - "NIFTY 24000CE"    → NIFTY 24000 CE options
          - "NIFTY 24000 CE"   → same as above
          - "RELIANCE"         → RELIANCE equity + derivatives
          - "industries"       → substring match on display name
          - "liance"           → substring match on short name
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

        # Parse remaining parts for strike/segment/option_type filters
        if len(parts) > 1:
            second = parts[1]
            if second == "FUT":
                target_segment = "FUT"
            elif second in ("CE", "PE"):
                target_type = second
                target_segment = "OPT"
            else:
                match = re.match(r"^(\d+(?:\.\d+)?)(CE|PE)?$", second)
                if match:
                    target_strike = float(match.group(1))
                    target_segment = "OPT"
                    if match.group(2):
                        target_type = match.group(2)

        if len(parts) > 2:
            if parts[2] in ("CE", "PE"):
                target_type = parts[2]
                target_segment = "OPT"
            elif parts[2] == "FUT":
                target_segment = "FUT"

        # Score each symbol: higher = better match
        # 100=exact short name, 80=prefix short name, 60=substring short name,
        # 40=substring display name, 20=substring fyers symbol
        scored: list[tuple[int, dict]] = []
        for sym in self._symbols:
            sn = sym["n"].upper()
            dn = sym["d"].upper()
            fs = sym["s"].upper()

            if sn == name_query:
                score = 100
            elif sn.startswith(name_query):
                score = 80
            elif name_query in sn:
                score = 60
            elif name_query in dn:
                score = 40
            elif name_query in fs:
                score = 20
            else:
                continue

            # Apply segment filter
            if target_segment and sym["g"] != target_segment:
                if target_segment != "OPT" or target_strike is not None:
                    continue
                elif sym["g"] != "EQ":
                    continue

            # Apply strike proximity filter
            if target_strike is not None:
                if sym["k"] is None:
                    continue
                if abs(sym["k"] - target_strike) > 500:
                    continue

            # Apply option type filter
            if target_type and sym.get("t") != target_type:
                continue

            scored.append((score, sym))

        # Sort: score desc, then EQ > FUT > OPT, then strike proximity
        seg_order = {"EQ": 0, "FUT": 1, "OPT": 2}

        def sort_key(item: tuple[int, dict]) -> tuple:
            sc, s = item
            strike_dist = abs(s["k"] - target_strike) if target_strike and s["k"] else 0
            return (-sc, seg_order.get(s["g"], 3), strike_dist, s.get("k") or 0)

        scored.sort(key=sort_key)
        return [sym for _, sym in scored[:limit]]


# Singleton
symbol_master = SymbolMaster()
