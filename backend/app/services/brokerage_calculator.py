"""Brokerage charges and taxes calculator for Indian F&O trades (Zerodha rate structure).

Pure module — no DB, no Redis, no side effects. All arithmetic in Decimal.
Rates as of Jan 2026 — update constants when NSE revises.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

# --- Rate constants (update when NSE/Zerodha revises) ---
_BROKERAGE_PER_LEG = Decimal("20")

_STT_OPTIONS_SELL = Decimal("0.000625")       # 0.0625% on sell-side premium turnover
_STT_FUTURES_SELL = Decimal("0.000125")       # 0.0125% on sell-side turnover

_EXCHANGE_TXN_OPTIONS = Decimal("0.000495")   # 0.0495% NSE options
_EXCHANGE_TXN_FUTURES = Decimal("0.00002")    # 0.002% NSE futures

_GST_RATE = Decimal("0.18")                   # 18% on (brokerage + exchange txn)

_SEBI_PER_CRORE = Decimal("10")               # Rs 10 per crore of turnover
_ONE_CRORE = Decimal("10000000")

_STAMP_OPTIONS_BUY = Decimal("0.00003")       # 0.003% on buy-side turnover
_STAMP_FUTURES_BUY = Decimal("0.00002")       # 0.002% on buy-side turnover

_TWO_DECIMAL = Decimal("0.01")


def _round2(val: Decimal) -> Decimal:
    return val.quantize(_TWO_DECIMAL, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ChargesBreakdown:
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    gst: Decimal
    sebi_charges: Decimal
    stamp_duty: Decimal
    total: Decimal

    def to_dict(self) -> dict:
        """JSONB-safe dict with float values."""
        return {
            "brokerage": float(self.brokerage),
            "stt": float(self.stt),
            "exchange_txn": float(self.exchange_txn),
            "gst": float(self.gst),
            "sebi_charges": float(self.sebi_charges),
            "stamp_duty": float(self.stamp_duty),
            "total": float(self.total),
        }


def compute_charges(
    instrument_type: str,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: int,
    side: str,
) -> ChargesBreakdown:
    """Compute roundtrip brokerage charges for an Indian F&O trade.

    Args:
        instrument_type: "OPTION" or "FUTURE"
        entry_price: Price at entry (premium for options, price for futures)
        exit_price: Price at exit
        quantity: Total quantity (lots x lot_size)
        side: "BUY" or "SELL" — the entry direction
    """
    entry_price = Decimal(str(entry_price))
    exit_price = Decimal(str(exit_price))
    qty = Decimal(str(quantity))

    entry_turnover = entry_price * qty
    exit_turnover = exit_price * qty
    total_turnover = entry_turnover + exit_turnover

    brokerage = _BROKERAGE_PER_LEG * 2

    if instrument_type == "OPTION":
        # Options: always BUY to open, SELL to close
        sell_turnover = exit_turnover
        buy_turnover = entry_turnover
        stt = _round2(sell_turnover * _STT_OPTIONS_SELL)
        exchange_txn = _round2(total_turnover * _EXCHANGE_TXN_OPTIONS)
        stamp_duty = _round2(buy_turnover * _STAMP_OPTIONS_BUY)
    else:
        # Futures: side determines which leg is buy vs sell
        if side == "BUY":
            buy_turnover = entry_turnover
            sell_turnover = exit_turnover
        else:
            sell_turnover = entry_turnover
            buy_turnover = exit_turnover
        stt = _round2(sell_turnover * _STT_FUTURES_SELL)
        exchange_txn = _round2(total_turnover * _EXCHANGE_TXN_FUTURES)
        stamp_duty = _round2(buy_turnover * _STAMP_FUTURES_BUY)

    gst = _round2((brokerage + exchange_txn) * _GST_RATE)
    sebi_charges = _round2(total_turnover / _ONE_CRORE * _SEBI_PER_CRORE)

    total = brokerage + stt + exchange_txn + gst + sebi_charges + stamp_duty

    return ChargesBreakdown(
        brokerage=brokerage,
        stt=stt,
        exchange_txn=exchange_txn,
        gst=gst,
        sebi_charges=sebi_charges,
        stamp_duty=stamp_duty,
        total=total,
    )
