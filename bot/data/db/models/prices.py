from datetime import datetime

from sqlalchemy import TIMESTAMP, Float, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class Prices(Base):
    __tablename__ = "prices"

    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, primary_key=True
    )
    pair: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    change_24h: Mapped[float] = mapped_column(Float, nullable=False)
    unit_trade_value: Mapped[float] = mapped_column(Float, nullable=False)
    spread_bps: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("idx_prices_pair_ts", "pair", "timestamp"),)
