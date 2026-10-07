"""Sizing and portfolio circuit-breaker for live spot orders."""

from math import floor

from bot.config import Config


class RiskManager:
    def __init__(self, initial_equity: float, settings: Config) -> None:
        self.settings, self.peak_equity, self.halted = settings, initial_equity, False

    def update_equity(self, equity: float) -> None:
        self.peak_equity = max(self.peak_equity, equity)
        self.halted = (
            equity > 0
            and (self.peak_equity - equity) / self.peak_equity
            >= self.settings.max_drawdown_pct
        )

    def size_usd(
        self,
        available_usd: float,
        equity: float,
        signal_strength: float,
        volatility_pct: float,
    ) -> float:
        if self.halted or equity <= 0:
            return 0.0
        conviction = 0.80 + min(max(signal_strength, 0.0), 1.0) * 0.50
        volatility_adjustment = max(0.45, 1 / (1 + max(volatility_pct, 0.0) * 0.25))
        size = (
            equity
            * self.settings.base_position_pct
            * conviction
            * volatility_adjustment
        )
        size = min(
            size,
            equity * self.settings.max_position_pct,
            available_usd * (1 - self.settings.reserve_pct),
        )
        return size if size >= self.settings.min_order_usd else 0.0

    def quantity(self, usd_amount: float, price: float, precision: int) -> float:
        if price <= 0:
            return 0.0
        raw = usd_amount / (price * (1 + self.settings.commission_rate))
        factor = 10**precision
        return floor(raw * factor) / factor
