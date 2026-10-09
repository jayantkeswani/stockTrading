"""TradeResponse serializes the stored contract symbol (closed-trade "+ Add to watchlist")."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.trade import Trade
from app.schemas.trade import TradeResponse


def _trade(**kw) -> Trade:
    base = dict(
        id=uuid.uuid4(), strategy_name="intraday_hunter_v2", symbol="SENSEX",
        expiry_date=date(2026, 10, 15), strike_price=Decimal("71900.00"), option_type="CE",
        side="BUY", quantity=20, lots=2, entry_price=Decimal("574.80"),
        stop_loss=Decimal("488.58"), status="CLOSED", is_paper=True, source="YOLO",
        entry_time=datetime(2026, 10, 9, 3, 48, tzinfo=timezone.utc),
        created_at=datetime(2026, 10, 9, 3, 48, tzinfo=timezone.utc),
        fyers_option_symbol="BSE:SENSEX26O1571900CE",
    )
    base.update(kw)
    return Trade(**base)


def test_fyers_option_symbol_is_serialized():
    body = TradeResponse.model_validate(_trade()).model_dump(mode="json")
    assert body["fyers_option_symbol"] == "BSE:SENSEX26O1571900CE"
    assert body["strike_price"] == "71900.00"  # Decimal → string: why the UI must Number() it


def test_legacy_row_without_symbol_is_null():
    body = TradeResponse.model_validate(_trade(fyers_option_symbol=None)).model_dump(mode="json")
    assert body["fyers_option_symbol"] is None
