import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class StrategyConfig(Base, TimestampMixin):
    __tablename__ = "strategy_configs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    strategy_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    symbols: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["NIFTY"])
    timeframes: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["5m"])
    auto_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
