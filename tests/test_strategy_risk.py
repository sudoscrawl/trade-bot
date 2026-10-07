from bot.config import config
from bot.risk import RiskManager
from bot.strategy import MomentumStrategy


def settings(**updates):
    return config.model_copy(update=updates)


def test_strategy_sells_at_hard_stop() -> None:
    strategy = MomentumStrategy(settings(min_history=5, stop_loss_pct=2.0))
    for price in (100, 101, 102, 103, 104):
        strategy.update("BTC/USD", price)
    strategy.notify_bought("BTC/USD", 100)

    assert strategy.update("BTC/USD", 97) == "SELL"


def test_strategy_takes_profit() -> None:
    strategy = MomentumStrategy(settings(min_history=5, take_profit_pct=3.0))
    for price in (100, 101, 102, 103, 104):
        strategy.update("BTC/USD", price)
    strategy.notify_bought("BTC/USD", 100)

    assert strategy.update("BTC/USD", 104) == "SELL"


def test_restored_prices_do_not_emit_an_immediate_signal() -> None:
    strategy = MomentumStrategy(settings(min_history=5))
    strategy.restore("BTC/USD", [100, 101, 102, 103, 104])

    assert strategy.update("BTC/USD", 105) == "HOLD"


def test_risk_halts_and_caps_position_size() -> None:
    risk = RiskManager(1_000, settings(max_position_pct=0.18, max_drawdown_pct=0.15))
    assert risk.size_usd(1_000, 1_000, 1.0, 0.0) <= 180

    risk.update_equity(850)
    assert risk.halted
    assert risk.size_usd(1_000, 850, 1.0, 0.0) == 0.0


def test_short_stop_loss() -> None:
    strategy = MomentumStrategy(settings(min_history=5, short_stop_loss_pct=2.0))
    for price in (100, 99, 98, 97, 96):
        strategy.update("BTC/USD", price)
    strategy.notify_shorted("BTC/USD", 100)

    # Price goes up — short loses
    assert strategy.update("BTC/USD", 103) == "COVER"


def test_short_take_profit() -> None:
    strategy = MomentumStrategy(settings(min_history=5, short_take_profit_pct=3.0))
    for price in (100, 99, 98, 97, 96):
        strategy.update("BTC/USD", price)
    strategy.notify_shorted("BTC/USD", 100)

    # Price drops — short profits
    assert strategy.update("BTC/USD", 96) == "COVER"


def test_trailing_stop_activates_and_triggers() -> None:
    strategy = MomentumStrategy(
        settings(
            min_history=5,
            trailing_activate_pct=1.0,
            trailing_stop_pct=1.0,
            take_profit_pct=10.0,  # high so it doesn't interfere
        )
    )
    for price in (100, 101, 102, 103, 104):
        strategy.update("BTC/USD", price)
    strategy.notify_bought("BTC/USD", 100)

    # Price rises to 102 — trailing activates (2% gain from entry of 100)
    strategy.update("BTC/USD", 102)
    # Price drops to 101.5 — 0.49% drawdown from high of 102, below 1.0% threshold
    assert strategy.update("BTC/USD", 101.5) == "HOLD"
    # Price drops to 100.9 — (102-100.9)/102 = 1.08% drawdown, exceeds 1.0%
    assert strategy.update("BTC/USD", 100.9) == "SELL"


def test_hold_during_warmup() -> None:
    strategy = MomentumStrategy(settings(min_history=10))
    # Feed fewer prices than min_history
    for price in (100, 101, 102):
        assert strategy.update("BTC/USD", price) == "HOLD"

    indicators = strategy.indicators("BTC/USD")
    assert indicators["warming_up"] is True


def test_indicators_contain_new_fields() -> None:
    strategy = MomentumStrategy(settings(min_history=5))
    for price in range(100, 130):
        strategy.update("BTC/USD", float(price))

    ind = strategy.indicators("BTC/USD")
    assert ind["warming_up"] is False
    assert "bb_upper" in ind
    assert "bb_mid" in ind
    assert "bb_lower" in ind
    assert "atr" in ind
    assert "roc" in ind
    assert "in_long" in ind
    assert "in_short" in ind


def test_negative_price_returns_hold() -> None:
    strategy = MomentumStrategy(settings(min_history=5))
    assert strategy.update("BTC/USD", -1.0) == "HOLD"
    assert strategy.update("BTC/USD", 0.0) == "HOLD"
