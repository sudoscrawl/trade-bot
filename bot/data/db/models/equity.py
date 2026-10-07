from datetime import datetime

from sqlalchemy import TIMESTAMP, Float
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class Equity(Base):
    __tablename__ = "equity"

    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP, primary_key=True)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
