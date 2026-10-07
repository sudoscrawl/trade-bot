from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from bot.data.db.engine import session_maker
from bot.data.db.models.api_events import ApiEvents
from bot.data.db.models.equity import Equity
from bot.data.db.models.prices import Prices
from bot.data.db.models.state import State
from bot.data.db.models.trades import Trades


def _timestamp(value: int | datetime | None = None) -> datetime:
    """Convert an epoch-millisecond timestamp to an aware UTC datetime."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


def _price_dict(price: Prices) -> dict[str, Any]:
    return {
        "timestamp": price.timestamp,
        "pair": price.pair,
        "price": price.price,
        "bid": price.bid,
        "ask": price.ask,
        "change_24h": price.change_24h,
        "unit_trade_value": price.unit_trade_value,
        "spread_bps": price.spread_bps,
    }


def _trade_dict(trade: Trades) -> dict[str, Any]:
    return {
        "trade_id": trade.trade_id,
        "timestamp": trade.timestamp,
        "pair": trade.pair,
        "side": trade.side,
        "quantity": trade.quantity,
        "price": trade.price,
        "notional": trade.notional,
        "fee": trade.fee,
        "mode": trade.mode,
        "order_id": trade.order_id,
        "status": trade.status,
        "session_id": trade.session_id,
    }


class DB:
    """Stateless, synchronous query service using ``session_maker`` transactions."""

    @staticmethod
    def set_state(key: str, value: str) -> None:
        with session_maker.begin() as session:
            state = session.get(State, key)
            if state is None:
                session.add(State(key=key, value=value))
            else:
                state.value = value

    @staticmethod
    def get_state(key: str, default: str | None = None) -> str | None:
        with session_maker() as session:
            state = session.get(State, key)
            return state.value if state is not None else default

    @staticmethod
    def get_all_state() -> dict[str, str]:
        with session_maker() as session:
            rows = session.scalars(select(State)).all()
            return {row.key: row.value for row in rows}

    @staticmethod
    def set_multiple_states(states: Mapping[str, str]) -> None:
        with session_maker.begin() as session:
            keys = list(states.keys())
            existing = {
                row.key: row
                for row in session.scalars(
                    select(State).where(State.key.in_(keys))
                ).all()
            }
            for key, value in states.items():
                if key in existing:
                    existing[key].value = value
                else:
                    session.add(State(key=key, value=value))

    @staticmethod
    def insert_prices(
        tickers: Mapping[str, Mapping[str, Any]],
        timestamp_ms: int | datetime | None = None,
    ) -> None:
        """Insert one price snapshot for all tracked tickers in a single batch."""
        timestamp = _timestamp(timestamp_ms)
        with session_maker.begin() as session:
            rows = []
            for pair, ticker in tickers.items():
                bid = float(ticker.get("MaxBid", 0.0))
                ask = float(ticker.get("MinAsk", 0.0))
                price = float(ticker.get("LastPrice", 0.0))
                spread_bps = (ask - bid) / price * 10_000 if price > 0 else 0.0
                rows.append(
                    Prices(
                        timestamp=timestamp,
                        pair=pair,
                        price=price,
                        bid=bid,
                        ask=ask,
                        change_24h=float(ticker.get("Change", 0.0)),
                        unit_trade_value=float(ticker.get("UnitTradeValue", 0.0)),
                        spread_bps=round(spread_bps, 4),
                    )
                )
            session.add_all(rows)

    @staticmethod
    def get_prices(pair: str, limit: int = 100) -> list[dict[str, Any]]:
        with session_maker() as session:
            prices = session.scalars(
                select(Prices)
                .where(Prices.pair == pair)
                .order_by(Prices.timestamp.desc())
                .limit(limit)
            ).all()
            return [_price_dict(price) for price in prices]

    @staticmethod
    def get_recent_prices_bulk(
        pairs: list[str], limit_per_pair: int = 40
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch recent price snapshots for multiple pairs in one query."""
        result: dict[str, list[dict[str, Any]]] = {p: [] for p in pairs}
        if not pairs:
            return result
        with session_maker() as session:
            prices = session.scalars(
                select(Prices)
                .where(Prices.pair.in_(pairs))
                .order_by(Prices.timestamp.desc())
            ).all()
            for price in prices:
                if len(result.get(price.pair, [])) < limit_per_pair:
                    result.setdefault(price.pair, []).append(_price_dict(price))
        return result

    @staticmethod
    def insert_trade(
        pair: str,
        side: str,
        quantity: float,
        price: float,
        fee: float,
        mode: str,
        order_id: str | None = None,
        status: str = "FILLED",
        timestamp_ms: int | datetime | None = None,
        session_id: str | None = None,
    ) -> None:
        with session_maker.begin() as session:
            session.add(
                Trades(
                    timestamp=_timestamp(timestamp_ms),
                    pair=pair,
                    side=side.upper(),
                    quantity=quantity,
                    price=price,
                    notional=quantity * price,
                    fee=fee,
                    mode=mode,
                    # The ORM model is non-nullable, unlike the sample's SQLite
                    # schema, so an absent exchange order id is stored as empty.
                    order_id=order_id or "",
                    status=status,
                    session_id=session_id,
                )
            )

    @staticmethod
    def insert_trade_from_order(
        order_result: Mapping[str, Any],
        mode: str = "LIVE",
        session_id: str | None = None,
        fallback_pair: str = "",
        fallback_side: str = "",
        fallback_price: float = 0.0,
        fallback_qty: float = 0.0,
    ) -> None:
        """Persist a trade from a ``place_order`` API response."""
        detail = order_result.get(
            "OrderDetail", order_result.get("order_detail", order_result)
        )
        if not isinstance(detail, Mapping):
            detail = order_result

        pair = str(
            detail.get("Pair")
            or detail.get("pair")
            or order_result.get("Pair")
            or order_result.get("pair")
            or fallback_pair
        )
        side = str(
            detail.get("Side")
            or detail.get("side")
            or order_result.get("Side")
            or order_result.get("side")
            or fallback_side
        )
        quantity = float(
            detail.get("FilledQuantity")
            or detail.get("Quantity")
            or detail.get("quantity")
            or detail.get("ShortQty")
            or order_result.get("Quantity")
            or fallback_qty
            or 0.0
        )
        price = float(
            detail.get("FilledAverPrice")
            or detail.get("filled_aver_price")
            or detail.get("AvgPrice")
            or detail.get("avg_price")
            or detail.get("Price")
            or detail.get("price")
            or detail.get("EntryPrice")
            or order_result.get("Price")
            or fallback_price
            or 0.0
        )
        fee = float(
            detail.get("CommissionChargeValue")
            or detail.get("OpenFee")
            or detail.get("fee")
            or detail.get("Fee")
            or 0.0
        )
        order_id = str(
            detail.get("OrderID")
            or detail.get("order_id")
            or detail.get("ID")
            or detail.get("id")
            or order_result.get("OrderID")
            or ""
        )
        status = str(
            detail.get("Status")
            or detail.get("status")
            or order_result.get("Status")
            or "FILLED"
        )
        ts = (
            detail.get("CreateTimestamp")
            or detail.get("timestamp")
            or order_result.get("timestamp")
        )

        if pair and quantity > 0 and price > 0:
            DB.insert_trade(
                pair=pair,
                side=side.upper(),
                quantity=quantity,
                price=price,
                fee=fee,
                mode=mode,
                order_id=order_id,
                status=status,
                timestamp_ms=ts,
                session_id=session_id,
            )

    @staticmethod
    def get_trades(limit: int = 50) -> list[dict[str, Any]]:
        with session_maker() as session:
            trades = session.scalars(
                select(Trades).order_by(Trades.timestamp.desc()).limit(limit)
            ).all()
            return [_trade_dict(trade) for trade in trades]

    @staticmethod
    def get_session_trades(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with session_maker() as session:
            trades = session.scalars(
                select(Trades)
                .where(Trades.session_id == session_id)
                .order_by(Trades.timestamp.desc())
                .limit(limit)
            ).all()
            return [_trade_dict(trade) for trade in trades]

    @staticmethod
    def insert_equity(
        equity: float, timestamp_ms: int | datetime | None = None
    ) -> None:
        timestamp = _timestamp(timestamp_ms)
        with session_maker.begin() as session:
            row = session.get(Equity, timestamp)
            if row is None:
                session.add(Equity(timestamp=timestamp, equity=round(equity, 4)))
            else:
                row.equity = round(equity, 4)

    @staticmethod
    def get_equity_history(limit: int = 200) -> list[dict[str, Any]]:
        with session_maker() as session:
            rows = session.scalars(
                select(Equity).order_by(Equity.timestamp.desc()).limit(limit)
            ).all()
            return [{"timestamp": row.timestamp, "equity": row.equity} for row in rows]

    @staticmethod
    def log_api_event(
        endpoint: str,
        success: bool,
        message: str = "",
        timestamp_ms: int | datetime | None = None,
    ) -> None:
        with session_maker.begin() as session:
            session.add(
                ApiEvents(
                    timestamp=_timestamp(timestamp_ms),
                    endpoint=endpoint,
                    success=success,
                    message=message,
                )
            )

    @staticmethod
    def stats() -> dict[str, int | float | None]:
        with session_maker() as session:
            latest_equity = session.scalar(
                select(Equity.equity).order_by(Equity.timestamp.desc()).limit(1)
            )
            return {
                "price_rows": session.scalar(select(func.count()).select_from(Prices))
                or 0,
                "trade_rows": session.scalar(select(func.count()).select_from(Trades))
                or 0,
                "equity_rows": session.scalar(select(func.count()).select_from(Equity))
                or 0,
                "api_events": session.scalar(
                    select(func.count()).select_from(ApiEvents)
                )
                or 0,
                "latest_equity": latest_equity,
            }
