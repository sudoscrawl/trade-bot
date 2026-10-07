"""Import all ORM models so Alembic sees the complete metadata."""

from bot.data.db.models.api_events import ApiEvents
from bot.data.db.models.equity import Equity
from bot.data.db.models.predictions import Prediction
from bot.data.db.models.prices import Prices
from bot.data.db.models.state import State
from bot.data.db.models.trades import Trades

__all__ = ["ApiEvents", "Equity", "Prediction", "Prices", "State", "Trades"]
