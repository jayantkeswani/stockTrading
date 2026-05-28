import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class YoloProfile(Base, TimestampMixin):
    """Configurable profit cap tier for YOLO agent execution."""

    __tablename__ = "yolo_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    profit_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_yolo_profiles_is_active", "is_active"),
    )
