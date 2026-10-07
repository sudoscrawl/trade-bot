from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.data.db.engine import Base


class State(Base):
    __tablename__ = "state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
