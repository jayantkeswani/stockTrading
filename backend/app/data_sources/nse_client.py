"""NSE India client for institutional shareholding data.

Fetches FII/DII/MF/promoter shareholding patterns from NSE India.
Uses httpx with NSE-compatible headers and rate limiting.

The master endpoint (/api/corporate-share-holdings-master) returns aggregate
promoter/public percentages + XBRL file URLs. We fetch the XBRL files for
the latest quarters to extract the detailed FII/DII/MF breakdown.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from app.data_sources.schemas import ShareholdingPattern

logger = logging.getLogger(__name__)

# NSE requires browser-like headers to avoid 403
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NSE_BASE_URL = "https://www.nseindia.com"

# Rate limiting: 2 seconds between requests
_RATE_LIMIT_DELAY = 2.0


async def _get_nse_session() -> httpx.AsyncClient:
    """Create an httpx client with NSE-compatible cookies.

    NSE requires a valid session cookie from the homepage before API calls work.
    """
    client = httpx.AsyncClient(
        headers=NSE_HEADERS,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    )
    # Hit homepage first to get cookies
    try:
        await client.get(NSE_BASE_URL)
    except httpx.HTTPError:
        logger.warning("Could not fetch NSE homepage for session cookies")
    return client


async def get_shareholding_pattern(symbol: str) -> list[ShareholdingPattern]:
    """Fetch quarterly shareholding pattern from NSE for a stock.

    Returns list of ShareholdingPattern sorted by quarter_end descending.

    Two-step process:
    1. Fetch master endpoint for quarterly records (promoter/public + XBRL URLs)
    2. Fetch XBRL files for latest 2 quarters to enrich with FII/DII/MF breakdown
    """
    client = await _get_nse_session()
    try:
        await asyncio.sleep(_RATE_LIMIT_DELAY)
        url = f"{NSE_BASE_URL}/api/corporate-share-holdings-master?index=equities&symbol={symbol}"
        response = await client.get(url)

        if response.status_code != 200:
            logger.warning(
                "NSE shareholding API returned %d for %s", response.status_code, symbol
            )
            return []

        data = response.json()
        patterns = _parse_shareholding_response(data, symbol)

        # Enrich latest 2 quarters with XBRL data (FII/DII/MF breakdown)
        records = data if isinstance(data, list) else data.get("data", [])
        xbrl_urls = [r.get("xbrl") for r in records[:2] if r.get("xbrl")]
        if xbrl_urls and patterns:
            await _enrich_from_xbrl(client, patterns[:2], xbrl_urls)

        return patterns
    except httpx.HTTPError:
        logger.exception("HTTP error fetching shareholding for %s", symbol)
        return []
    except Exception:
        logger.exception("Error parsing shareholding for %s", symbol)
        return []
    finally:
        await client.aclose()


async def _enrich_from_xbrl(
    client: httpx.AsyncClient,
    patterns: list[ShareholdingPattern],
    xbrl_urls: list[str],
) -> None:
    """Fetch XBRL files and enrich patterns with FII/DII/MF breakdown.

    XBRL files are hosted on nsearchives.nseindia.com (no auth required).
    Values in XBRL are decimals (0.7177 = 71.77%).
    """
    for i, url in enumerate(xbrl_urls):
        if i >= len(patterns):
            break
        try:
            resp = await client.get(url, timeout=httpx.Timeout(15.0))
            if resp.status_code != 200:
                logger.debug("XBRL fetch returned %d for %s", resp.status_code, url)
                continue

            breakdown = _parse_xbrl_shareholding(resp.text)
            if not breakdown:
                continue

            pattern = patterns[i]
            if breakdown.get("fii") is not None:
                pattern.fii_pct = breakdown["fii"]
            if breakdown.get("dii") is not None:
                pattern.dii_pct = breakdown["dii"]
            if breakdown.get("mf") is not None:
                pattern.mf_pct = breakdown["mf"]
            if breakdown.get("pledge") is not None:
                pattern.pledge_pct = breakdown["pledge"]

        except Exception:
            logger.debug("Failed to parse XBRL from %s", url, exc_info=True)


def _parse_xbrl_shareholding(xml_text: str) -> dict[str, float] | None:
    """Parse XBRL XML to extract institutional shareholding percentages.

    NSE XBRL stores all percentage values in elements named
    'ShareholdingAsAPercentageOfTotalNumberOfShares', differentiated by
    contextRef attribute (e.g. 'InstitutionsForeign_ContextI').
    Values are decimals (0-1 scale), converted to percentages.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    result: dict[str, float] = {}

    # Map contextRef prefixes to our fields
    # We want the aggregate _ContextI values (not per-shareholder breakdowns)
    context_mappings = {
        "InstitutionsForeign_ContextI": "fii",
        "InstitutionsDomestic_ContextI": "dii",
        "MutualFundsOrUTI_ContextI": "mf",
    }

    pct_tag_suffix = "ShareholdingAsAPercentageOfTotalNumberOfShares"
    pledge_tag_suffix = "PercentageOfSharesPledgedOrOtherwiseEncumbered"

    for elem in root.iter():
        tag = elem.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]

        ctx = elem.get("contextRef", "")

        if tag == pct_tag_suffix:
            for ctx_key, field in context_mappings.items():
                if ctx == ctx_key and field not in result:
                    val = _safe_float(elem.text)
                    if val is not None:
                        result[field] = round(val * 100, 2)
                    break

        elif tag == pledge_tag_suffix and "pledge" not in result:
            # Pledge is on the promoter context
            if "PromoterAndPromoterGroup" in ctx or ctx == "MainI":
                val = _safe_float(elem.text)
                if val is not None:
                    result["pledge"] = round(val * 100, 2)

    return result if result else None


def _parse_shareholding_response(
    data: dict | list, symbol: str
) -> list[ShareholdingPattern]:
    """Parse NSE shareholding API response into ShareholdingPattern objects.

    NSE returns shareholding data in varying formats. We attempt to extract
    promoter, FII, DII, MF, and public percentages from whatever format we get.
    """
    patterns: list[ShareholdingPattern] = []

    # The API may return a list of quarterly data or nested structure
    records = data if isinstance(data, list) else data.get("data", [])

    for record in records:
        try:
            # Extract date
            date_str = record.get("date") or record.get("quarter_end") or ""
            if not date_str:
                continue

            from datetime import datetime
            if isinstance(date_str, str):
                # Try multiple date formats
                q_date = None
                for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        q_date = datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue
                if q_date is None:
                    continue
            else:
                q_date = date_str

            # Extract holding percentages
            # New NSE endpoint uses pr_and_prgrp / public_val; old used promotersPer etc.
            promoter = _safe_float(
                record.get("pr_and_prgrp") or record.get("promotersPer") or record.get("promoter_pct")
            )
            fii = _safe_float(record.get("fiiPer") or record.get("fii_pct") or record.get("fpi_pct"))
            dii = _safe_float(record.get("diiPer") or record.get("dii_pct"))
            mf = _safe_float(record.get("mfPer") or record.get("mf_pct"))
            public = _safe_float(
                record.get("public_val") or record.get("publicPer") or record.get("public_pct")
            )
            pledge = _safe_float(record.get("pledgedPer") or record.get("pledge_pct"))

            patterns.append(ShareholdingPattern(
                quarter_end=q_date,
                promoter_pct=promoter or 0,
                fii_pct=fii or 0,
                dii_pct=dii or 0,
                mf_pct=mf or 0,
                public_pct=public or 0,
                pledge_pct=pledge,
            ))
        except (ValueError, TypeError, KeyError):
            continue

    patterns.sort(key=lambda p: p.quarter_end, reverse=True)
    return patterns


async def get_fo_lot_sizes() -> dict[str, int]:
    """Download F&O lot sizes from NSE.

    Returns dict of symbol -> lot_size for all F&O eligible stocks.
    Source: NSE market lots CSV or API.
    """
    client = await _get_nse_session()
    try:
        await asyncio.sleep(_RATE_LIMIT_DELAY)
        # Try the lot size CSV endpoint
        url = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"
        response = await client.get(url)

        if response.status_code != 200:
            logger.warning("NSE lot sizes CSV returned %d", response.status_code)
            return {}

        return _parse_lot_sizes_csv(response.text)
    except httpx.HTTPError:
        logger.exception("HTTP error fetching F&O lot sizes")
        return {}
    finally:
        await client.aclose()


def _parse_lot_sizes_csv(csv_text: str) -> dict[str, int]:
    """Parse the NSE fo_mktlots.csv file.

    Format: Header rows, then: SYMBOL, UNDERLYING, lot_size_col1, lot_size_col2, ...
    The first numeric column after the symbol is the current lot size.
    """
    lot_sizes: dict[str, int] = {}
    lines = csv_text.strip().split("\n")

    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue

        symbol = parts[1].strip().upper()
        if not symbol or symbol == "UNDERLYING" or symbol == "Symbol":
            continue

        # Find first non-empty numeric value after the symbol columns
        for val in parts[2:]:
            val = val.strip()
            if val and val.isdigit():
                lot_sizes[symbol] = int(val)
                break

    return lot_sizes


def _safe_float(val) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
