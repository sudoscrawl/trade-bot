"""Multi-signal momentum strategy with long and short trade support."""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from bot.config import Config

logger = logging.getLogger(__name__)

Signal = Literal["BUY", "SELL", "SHORT", "COVER", "HOLD"]


# ── Technical indicator functions ────────────────────────────────────────────


def ema(prices: list[float], period: int) -> float:
    """Exponential moving average over the full *prices* list."""
    if len(prices) < period:
        return float("nan")
    multiplier = 2 / (period + 1)
    result = prices[0]
    for price in prices[1:]:
        result = price * multiplier + result * (1 - multiplier)
    return result


def rsi(prices: list[float], period: int) -> float:
    """Relative strength index using simple gain/loss averaging."""
    if len(prices) < period + 1:
        return 50.0
    relevant = prices[-(period + 1) :]
    changes = [relevant[i + 1] - relevant[i] for i in range(len(relevant) - 1)]
    gain = sum(c for c in changes if c > 0) / period
    loss = sum(-c for c in changes if c < 0) / period
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def bollinger_bands(
    prices: list[float], period: int, num_std: float = 2.0
) -> tuple[float, float, float]:
    """Return (upper, middle, lower) Bollinger Bands."""
    if len(prices) < period:
        return float("nan"), float("nan"), float("nan")
    window = prices[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std = math.sqrt(variance)
    return middle + num_std * std, middle, middle - num_std * std


def atr(prices: list[float], period: int) -> float:
    """Simplified Average True Range using close-to-close ranges."""
    if len(prices) < period + 1:
        return 0.0
    true_ranges = [abs(prices[i] - prices[i - 1]) for i in range(-period, 0)]
    return sum(true_ranges) / len(true_ranges)


def rate_of_change(prices: list[float], period: int) -> float:
    """Price rate of change over *period* bars, as a percentage."""
    if len(prices) < period + 1 or prices[-(period + 1)] == 0:
        return 0.0
    return (prices[-1] - prices[-(period + 1)]) / prices[-(period + 1)] * 100


# ── Per-pair state ───────────────────────────────────────────────────────────


@dataclass
class PairState:
    prices: deque[float] = field(default_factory=lambda: deque(maxlen=300))

    # Long position tracking
    entry_price: float = 0.0
    hold_cycles: int = 0
    trailing_high: float = 0.0

    # Short position tracking
    short_entry_price: float = 0.0
    short_hold_cycles: int = 0
    trailing_low: float = float("inf")

    # Trend / crossover tracking
    ticks_above_slow: int = 0
    ticks_below_slow: int = 0

    # Cooldown after losing trades
    cooldown_cycles: int = 0

    # Restore guard
    restored: bool = False

    @property
    def in_long(self) -> bool:
        return self.entry_price > 0.0

    @property
    def in_short(self) -> bool:
        return self.short_entry_price > 0.0

    @property
    def in_position(self) -> bool:
        return self.in_long or self.in_short


# ── Strategy ─────────────────────────────────────────────────────────────────


class MomentumStrategy:
    def __init__(self, settings: Config) -> None:
        self.settings = settings
        self._states: dict[str, PairState] = {}

    def _state(self, pair: str) -> PairState:
        return self._states.setdefault(pair, PairState())

    # ── Lifecycle notifications from the execution layer ─────────────────

    def restore(self, pair: str, prices: list[float]) -> None:
        state = self._state(pair)
        state.prices.extend(reversed([p for p in prices if p > 0]))
        state.restored = True

    def notify_bought(self, pair: str, price: float) -> None:
        state = self._state(pair)
        state.entry_price = price
        state.hold_cycles = 0
        state.trailing_high = price

    def notify_sold(self, pair: str, was_loss: bool) -> None:
        state = self._state(pair)
        state.entry_price = 0.0
        state.hold_cycles = 0
        state.trailing_high = 0.0
        if was_loss:
            state.cooldown_cycles = self.settings.loss_cooldown_cycles

    def notify_shorted(self, pair: str, price: float) -> None:
        state = self._state(pair)
        state.short_entry_price = price
        state.short_hold_cycles = 0
        state.trailing_low = price

    def notify_covered(self, pair: str, was_loss: bool) -> None:
        state = self._state(pair)
        state.short_entry_price = 0.0
        state.short_hold_cycles = 0
        state.trailing_low = float("inf")
        if was_loss:
            state.cooldown_cycles = self.settings.loss_cooldown_cycles

    # ── Core signal generation ───────────────────────────────────────────

    def update(self, pair: str, price: float) -> Signal:
        if price <= 0:
            return "HOLD"

        state, cfg = self._state(pair), self.settings
        state.prices.append(price)
        prices = list(state.prices)

        if len(prices) < cfg.min_history:
            return "HOLD"
        if state.restored:
            state.restored = False
            return "HOLD"

        # ── Compute indicators ───────────────────────────────────────
        fast = ema(prices, cfg.fast_ema_period)
        slow = ema(prices, cfg.slow_ema_period)
        prev_fast = ema(prices[:-1], cfg.fast_ema_period)
        prev_slow = ema(prices[:-1], cfg.slow_ema_period)
        current_rsi = rsi(prices, cfg.rsi_period)
        bb_upper, bb_mid, bb_lower = bollinger_bands(prices, cfg.bb_period, cfg.bb_std)
        roc = rate_of_change(prices, cfg.roc_period)
        separation = (fast - slow) / slow * 100 if slow > 0 else 0.0

        # Track how long fast EMA has been above/below slow EMA
        if fast > slow:
            state.ticks_above_slow += 1
            state.ticks_below_slow = 0
        else:
            state.ticks_below_slow += 1
            state.ticks_above_slow = 0

        state.cooldown_cycles = max(0, state.cooldown_cycles - 1)

        # ── SELL path (exit long) ────────────────────────────────────
        if state.in_long:
            state.hold_cycles += 1
            state.trailing_high = max(state.trailing_high, price)
            pnl = (price - state.entry_price) / state.entry_price * 100

            # 1. Hard take-profit
            if pnl >= cfg.take_profit_pct:
                logger.info("%s TAKE-PROFIT: pnl=%.2f%%", pair, pnl)
                return "SELL"

            # 2. Hard stop-loss
            if pnl <= -cfg.stop_loss_pct:
                logger.warning("%s STOP-LOSS: pnl=%.2f%%", pair, pnl)
                return "SELL"

            # 3. Trailing stop — once profit exceeds activation threshold,
            #    exit if price drops trailing_stop_pct from the high.
            if state.trailing_high > 0:
                trail_pnl = (
                    (state.trailing_high - state.entry_price) / state.entry_price * 100
                )
                if trail_pnl >= cfg.trailing_activate_pct:
                    drawdown_from_high = (
                        (state.trailing_high - price) / state.trailing_high * 100
                    )
                    if drawdown_from_high >= cfg.trailing_stop_pct:
                        logger.info(
                            "%s TRAILING-STOP: peak_pnl=%.2f%% drawdown=%.2f%%",
                            pair,
                            trail_pnl,
                            drawdown_from_high,
                        )
                        return "SELL"

            # 4. Bearish EMA crossover exit — momentum reversed, held long enough
            crossed_down = prev_fast >= prev_slow and fast < slow
            if (
                crossed_down
                and state.hold_cycles >= cfg.min_hold_cycles
                and current_rsi < cfg.rsi_sell_threshold
            ):
                logger.info(
                    "%s EMA-CROSSDOWN EXIT: pnl=%.2f%% rsi=%.1f",
                    pair,
                    pnl,
                    current_rsi,
                )
                return "SELL"

            # 5. Stagnant exit — position hasn't gone anywhere, EMA turned against
            if (
                state.hold_cycles >= cfg.stagnant_exit_cycles
                and state.ticks_below_slow >= 2
                and pnl < cfg.min_profit_pct
            ):
                logger.info(
                    "%s STAGNANT EXIT: pnl=%.2f%% cycles=%d",
                    pair,
                    pnl,
                    state.hold_cycles,
                )
                return "SELL"

            return "HOLD"

        # ── COVER path (exit short) ──────────────────────────────────
        if state.in_short:
            state.short_hold_cycles += 1
            state.trailing_low = min(state.trailing_low, price)
            pnl = (state.short_entry_price - price) / state.short_entry_price * 100

            # 1. Take-profit on short
            if pnl >= cfg.short_take_profit_pct:
                logger.info("%s SHORT TAKE-PROFIT: pnl=%.2f%%", pair, pnl)
                return "COVER"

            # 2. Stop-loss on short
            if pnl <= -cfg.short_stop_loss_pct:
                logger.warning("%s SHORT STOP-LOSS: pnl=%.2f%%", pair, pnl)
                return "COVER"

            # 3. Trailing stop for shorts — price rose from trailing low
            if state.trailing_low < float("inf") and state.trailing_low > 0:
                trail_pnl = (
                    (state.short_entry_price - state.trailing_low)
                    / state.short_entry_price
                    * 100
                )
                if trail_pnl >= cfg.trailing_activate_pct:
                    bounce_from_low = (
                        (price - state.trailing_low) / state.trailing_low * 100
                    )
                    if bounce_from_low >= cfg.trailing_stop_pct:
                        logger.info(
                            "%s SHORT TRAILING-STOP: peak_pnl=%.2f%% bounce=%.2f%%",
                            pair,
                            trail_pnl,
                            bounce_from_low,
                        )
                        return "COVER"

            # 4. Bullish EMA crossover — trend reversing against short
            crossed_up = prev_fast <= prev_slow and fast > slow
            if (
                crossed_up
                and state.short_hold_cycles >= cfg.min_hold_cycles
                and current_rsi > cfg.rsi_cover_threshold
            ):
                logger.info(
                    "%s EMA-CROSSUP COVER: pnl=%.2f%% rsi=%.1f",
                    pair,
                    pnl,
                    current_rsi,
                )
                return "COVER"

            # 5. Stagnant short exit
            if (
                state.short_hold_cycles >= cfg.stagnant_exit_cycles
                and state.ticks_above_slow >= 2
                and pnl < cfg.min_profit_pct
            ):
                logger.info(
                    "%s SHORT STAGNANT EXIT: pnl=%.2f%% cycles=%d",
                    pair,
                    pnl,
                    state.short_hold_cycles,
                )
                return "COVER"

            return "HOLD"

        # ── ENTRY paths (no open position) ───────────────────────────

        # --- SHORT FIRST (bearish-biased market) ---
        # In a crashing market, shorts should trigger easily.
        # We use an OR-based approach: any two bearish signals trigger a SHORT.
        crossed_down = prev_fast >= prev_slow and fast < slow
        confirmed_down = state.ticks_below_slow >= cfg.confirm_ticks
        bb_short = not math.isnan(bb_upper) and price >= bb_mid  # above midline
        momentum_short = roc < cfg.roc_short_threshold  # strong downward momentum
        rsi_bearish = current_rsi < 55.0  # not overbought
        trend_bearish = fast < slow  # fast below slow = downtrend

        # Score how many bearish signals are present
        bearish_signals = sum([
            crossed_down or confirmed_down,     # EMA trend confirmation
            trend_bearish,                       # currently in downtrend
            momentum_short,                      # negative rate of change
            rsi_bearish,                         # RSI favours shorts
            bb_short,                            # price above BB midline (room to fall)
            separation <= -cfg.ema_separation_pct,  # meaningful EMA gap
        ])

        # SHORT if at least 2 bearish signals fire and not in cooldown
        if bearish_signals >= 2 and state.cooldown_cycles == 0:
            logger.info(
                "%s SHORT signal: rsi=%.1f sep=%.3f%% roc=%.2f bearish_count=%d",
                pair,
                current_rsi,
                separation,
                roc,
                bearish_signals,
            )
            return "SHORT"

        # --- BUY (very selective — only extreme reversals in a crash) ---
        # In a crash, longs are dangerous. Only enter on very strong bounce signals:
        #   - RSI deeply oversold AND bouncing up
        #   - Bullish EMA crossover confirmed
        #   - Price below lower Bollinger Band (extreme value)
        #   - Strong positive momentum
        crossed_up = prev_fast <= prev_slow and fast > slow
        confirmed_up = (
            cfg.confirm_ticks <= state.ticks_above_slow <= cfg.confirm_ticks + 2
        )
        bb_buy = not math.isnan(bb_lower) and price <= bb_lower  # at or below lower BB
        momentum_buy = roc > cfg.roc_buy_threshold  # strong upward momentum
        rsi_oversold_bounce = current_rsi < 35.0  # deeply oversold

        bullish_signals = sum([
            crossed_up or confirmed_up,
            momentum_buy,
            bb_buy,
            rsi_oversold_bounce,
        ])

        # BUY only if 3+ strong bullish signals fire (very conservative)
        if (
            bullish_signals >= 3
            and state.cooldown_cycles == 0
            and cfg.rsi_buy_min <= current_rsi <= cfg.rsi_buy_max
            and separation >= cfg.ema_separation_pct
        ):
            logger.info(
                "%s BUY signal: rsi=%.1f sep=%.3f%% roc=%.2f bullish_count=%d",
                pair,
                current_rsi,
                separation,
                roc,
                bullish_signals,
            )
            return "BUY"

        return "HOLD"

    # ── Indicator snapshot for scoring / logging ─────────────────────────

    def indicators(self, pair: str) -> dict[str, float | int | bool]:
        state, cfg = self._state(pair), self.settings
        prices = list(state.prices)
        if len(prices) < cfg.min_history:
            return {"warming_up": True, "prices_collected": len(prices)}

        fast = ema(prices, cfg.fast_ema_period)
        slow = ema(prices, cfg.slow_ema_period)
        bb_upper, bb_mid, bb_lower = bollinger_bands(prices, cfg.bb_period, cfg.bb_std)
        current_atr_val = atr(prices, cfg.atr_period)
        roc_val = rate_of_change(prices, cfg.roc_period)

        return {
            "warming_up": False,
            "rsi": round(rsi(prices, cfg.rsi_period), 2),
            "fast_ema": fast,
            "slow_ema": slow,
            "ema_sep_pct": (fast - slow) / slow * 100 if slow else 0.0,
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "atr": current_atr_val,
            "roc": roc_val,
            "entry_price": state.entry_price,
            "short_entry_price": state.short_entry_price,
            "hold_cycles": state.hold_cycles,
            "short_hold_cycles": state.short_hold_cycles,
            "in_long": state.in_long,
            "in_short": state.in_short,
        }
