"""Singleton trading configuration table.

A single row (id=1) stores all user-editable trading parameters.
Seeded on first startup from .env defaults; editable via /api/v1/settings/trading.
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TradingConfig(Base, TimestampMixin):
    __tablename__ = "trading_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_trading_config_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    capital: Mapped[int] = mapped_column(Integer, nullable=False)
    max_daily_drawdown_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    max_risk_per_trade_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_trading: Mapped[bool] = mapped_column(Boolean, nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(10), nullable=False)  # MANUAL|SEMI|YOLO
    min_confidence_to_persist: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("30.00"))
    min_confidence_for_shadow: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("70.00"))
    min_confidence_for_execution: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("70.00"))
