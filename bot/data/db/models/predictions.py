from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, DateTime, Float, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    timestamp_ms: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
    )

    pair: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    target_time_ms: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    predicted_return: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    probability_up: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    probability_flat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    probability_down: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    model_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    realized_return: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    realized_state: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    settled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    __table_args__ = (
        Index(
            "idx_predictions_target",
            "target_time_ms",
            "settled",
        ),
        Index(
            "idx_predictions_pair",
            "pair",
            "timestamp_ms",
        ),
    )
