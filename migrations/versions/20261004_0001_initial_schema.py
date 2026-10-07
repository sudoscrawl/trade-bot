"""initial quant bot schema

Revision ID: 20261004_0001
Revises: None
Create Date: 2026-10-04 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261004_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable market, order, equity, and model tables."""
    op.create_table(
        "api_events",
        sa.Column("query_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("query_id"),
    )
    op.create_index("idx_api_events_ts", "api_events", ["timestamp"], unique=False)
    op.create_table(
        "equity",
        sa.Column("timestamp", sa.TIMESTAMP(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("timestamp"),
    )
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp_ms", sa.TIMESTAMP(), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("target_time_ms", sa.DateTime(), nullable=False),
        sa.Column("predicted_return", sa.Float(), nullable=False),
        sa.Column("probability_up", sa.Float(), nullable=False),
        sa.Column("probability_flat", sa.Float(), nullable=False),
        sa.Column("probability_down", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_enabled", sa.Boolean(), nullable=False),
        sa.Column("realized_return", sa.Float(), nullable=True),
        sa.Column("realized_state", sa.Integer(), nullable=True),
        sa.Column("settled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_predictions_pair", "predictions", ["pair", "timestamp_ms"], unique=False
    )
    op.create_index(
        "idx_predictions_target",
        "predictions",
        ["target_time_ms", "settled"],
        unique=False,
    )
    op.create_table(
        "prices",
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("bid", sa.Float(), nullable=False),
        sa.Column("ask", sa.Float(), nullable=False),
        sa.Column("change_24h", sa.Float(), nullable=False),
        sa.Column("unit_trade_value", sa.Float(), nullable=False),
        sa.Column("spread_bps", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("timestamp", "pair"),
    )
    op.create_index("idx_prices_pair_ts", "prices", ["pair", "timestamp"], unique=False)
    op.create_table(
        "state",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "trades",
        sa.Column("trade_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(), nullable=False),
        sa.Column("pair", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("notional", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("trade_id"),
    )
    op.create_index(
        "idx_trades_session_ts", "trades", ["session_id", "timestamp"], unique=False
    )
    op.create_index("idx_trades_ts", "trades", ["timestamp"], unique=False)


def downgrade() -> None:
    """Remove all application tables in reverse dependency order."""
    op.drop_index("idx_trades_ts", table_name="trades")
    op.drop_index("idx_trades_session_ts", table_name="trades")
    op.drop_table("trades")
    op.drop_table("state")
    op.drop_index("idx_prices_pair_ts", table_name="prices")
    op.drop_table("prices")
    op.drop_index("idx_predictions_target", table_name="predictions")
    op.drop_index("idx_predictions_pair", table_name="predictions")
    op.drop_table("predictions")
    op.drop_table("equity")
    op.drop_index("idx_api_events_ts", table_name="api_events")
    op.drop_table("api_events")
