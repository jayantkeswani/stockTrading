"""Tests for the symbol master parser — segment tagging from real Fyers rows.

Regression coverage for the BSE -> BANKEX futures mis-resolution: stock futures
(Fyers col[2]=13) and stock options (col[2]=15) must be tagged FUT/OPT, not EQ.
The old parser whitelisted only 11=FUT and 14=OPT, dumping everything else into
EQ — which made the FUT-filtered search invisible to the real stock contract and
let an exchange prefix ("BSE:" in BSE:BANKEX...FUT) win instead.
"""

from app.data_feed.symbol_master import SymbolMaster, _parse_csv_row


def _row(*, fyers_symbol, display, short_name, code, strike="-1.0", opt_type="XX", lot="500"):
    """Build a Fyers symbol-master CSV row (21 columns) for the fields we parse."""
    row = [""] * 21
    row[0] = "100000000000001"   # fytoken
    row[1] = display             # display name
    row[2] = str(code)           # instrument-type code (deliberately NOT trusted)
    row[3] = lot                 # lot size
    row[8] = "1782813600"        # expiry epoch (25 Jun 2026-ish)
    row[9] = fyers_symbol        # Fyers symbol
    row[13] = short_name         # short / underlying name
    row[15] = strike             # strike (-1.0 = non-option)
    row[16] = opt_type           # CE / PE / XX
    return row


class TestSegmentTagging:
    """col[2] is unreliable; segment must come from strike/opt-type/suffix."""

    def test_stock_future_code_13_tagged_fut(self):
        # The exact case that broke: stock future arrives with code 13, not 11.
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:RELIANCE26JUNFUT", display="RELIANCE 30 Jun 26 FUT",
                 short_name="RELIANCE", code=13),
            "NSE", "FO",
        )
        assert r["g"] == "FUT"

    def test_index_future_code_11_tagged_fut(self):
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:NIFTY26JUNFUT", display="NIFTY 30 Jun 26 FUT",
                 short_name="NIFTY", code=11),
            "NSE", "FO",
        )
        assert r["g"] == "FUT"

    def test_bse_stock_future_tagged_fut(self):
        # BSE Ltd stock future — must NOT collapse into EQ.
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:BSE26JUNFUT", display="BSE 30 Jun 26 FUT",
                 short_name="BSE", code=13),
            "NSE", "FO",
        )
        assert r["g"] == "FUT"
        assert r["n"] == "BSE"

    def test_stock_option_code_15_tagged_opt(self):
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:RELIANCE26JUN3000CE", display="RELIANCE 30 Jun 26 3000 CE",
                 short_name="RELIANCE", code=15, strike="3000.0", opt_type="CE"),
            "NSE", "FO",
        )
        assert r["g"] == "OPT"
        assert r["t"] == "CE"
        assert r["k"] == 3000.0

    def test_index_option_code_14_tagged_opt(self):
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:NIFTY2562624000PE", display="NIFTY 26 Jun 25 24000 PE",
                 short_name="NIFTY", code=14, strike="24000.0", opt_type="PE"),
            "NSE", "FO",
        )
        assert r["g"] == "OPT"
        assert r["t"] == "PE"

    def test_equity_tagged_eq(self):
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:RELIANCE-EQ", display="RELIANCE", short_name="RELIANCE",
                 code=10, lot="1"),
            "NSE", "CM",
        )
        assert r["g"] == "EQ"

    def test_bse_ltd_equity_tagged_eq(self):
        r = _parse_csv_row(
            _row(fyers_symbol="NSE:BSE-EQ", display="BSE LTD", short_name="BSE",
                 code=10, lot="1"),
            "NSE", "CM",
        )
        assert r["g"] == "EQ"


class TestSearchFutFilter:
    """A FUT-filtered search must surface the real stock future, not an index
    future that only matches via the exchange prefix."""

    def _master(self):
        rows = [
            _row(fyers_symbol="NSE:BSE26JUNFUT", display="BSE 30 Jun 26 FUT",
                 short_name="BSE", code=13),
            _row(fyers_symbol="NSE:BSE-EQ", display="BSE LTD", short_name="BSE",
                 code=10, lot="1"),
            _row(fyers_symbol="BSE:BANKEX26JUNFUT", display="BANKEX 25 Jun 26 FUT",
                 short_name="BANKEX", code=11),
            _row(fyers_symbol="BSE:SENSEX26JUNFUT", display="SENSEX 25 Jun 26 FUT",
                 short_name="SENSEX", code=11),
        ]
        sm = SymbolMaster()
        sm._symbols = [p for r in rows if (p := _parse_csv_row(r, "NSE", "FO"))]
        sm._build_index()
        return sm

    def test_search_bse_fut_top_hit_is_stock_future(self):
        sm = self._master()
        results = sm.search("BSE FUT", limit=10)
        assert results, "expected at least one FUT match"
        # Exact underlying name match (score 100) must outrank the BSE: prefix
        # substring match (score 20) on BANKEX/SENSEX.
        assert results[0]["s"] == "NSE:BSE26JUNFUT"
        assert results[0]["n"] == "BSE"
