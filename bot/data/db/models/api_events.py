from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class ApiEvents(Base):
    __tablename__ = "api_events"

    query_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_api_events_ts", "timestamp"),)
