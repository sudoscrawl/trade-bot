from bot.config import config
from bot.main import _save_state, _score_opportunity, _select_tracked_symbols
from bot.risk import RiskManager
from bot.services.db_queries import DB


def test_select_tracked_symbols_tracks_all_exchange_coins():
    mock_tickers = {
        f"COIN{i}/USD": {
            "LastPrice": 10.0 + i,
            "UnitTradeValue": 1000.0 * i,
            "Change": 0.05,
        }
        for i in range(88)
    }
    mock_exchange_info = {
        "TradePairs": {
            f"COIN{i}/USD": {"AmountPrecision": 4, "MiniOrder": 1.0} for i in range(88)
        }
    }

    selected = _select_tracked_symbols(mock_exchange_info, mock_tickers)
    assert len(selected) == 88


def test_select_tracked_symbols_selects_at_least_15_coins():
    # Mock tickers with 25 pairs
    mock_tickers = {
        f"COIN{i}/USD": {
            "LastPrice": 10.0 + i,
            "UnitTradeValue": 1000.0 * (25 - i),
            "Change": 0.05,
        }
        for i in range(25)
    }
    mock_exchange_info = {
        "TradePairs": {
            f"COIN{i}/USD": {"AmountPrecision": 4, "MiniOrder": 1.0} for i in range(25)
        }
    }

    selected = _select_tracked_symbols(mock_exchange_info, mock_tickers, min_count=15)
    assert len(selected) >= 15
    for pair in selected:
        assert pair in mock_tickers


def test_score_opportunity_ranks_stronger_momentum_higher():
    # Higher EMA separation, healthy RSI, high volume
    strong_indicators = {"warming_up": False, "ema_sep_pct": 0.08, "rsi": 56.0}
    strong_ticker = {
        "LastPrice": 100.0,
        "MaxBid": 99.98,
        "MinAsk": 100.02,
        "Change": 0.08,
        "UnitTradeValue": 500_000.0,
    }
    strong_score = _score_opportunity(
        "BTC/USD", strong_indicators, strong_ticker, volatility=1.5
    )

    # Weak momentum
    weak_indicators = {"warming_up": False, "ema_sep_pct": 0.005, "rsi": 35.0}
    weak_ticker = {
        "LastPrice": 100.0,
        "MaxBid": 99.0,
        "MinAsk": 101.0,
        "Change": -0.05,
        "UnitTradeValue": 1_000.0,
    }
    weak_score = _score_opportunity(
        "WEAK/USD", weak_indicators, weak_ticker, volatility=0.5
    )

    assert strong_score > weak_score
    assert strong_score > 0.0


def test_score_opportunity_returns_zero_when_warming_up():
    score = _score_opportunity(
        "BTC/USD",
        {"warming_up": True, "prices_collected": 5},
        {"LastPrice": 100.0},
        1.0,
    )
    assert score == 0.0


def test_save_state_persists_to_db():
    risk = RiskManager(50_000.0, config)
    positions = {"BTC/USD": 65000.0}
    candidates = [{"pair": "SOL/USD", "score": 4.5}]
    tracked = ["BTC/USD", "ETH/USD", "SOL/USD"]
    summary = {"tracked_coins_count": 3}

    _save_state(
        session_id="test-session-123",
        risk=risk,
        equity=51000.0,
        available_usd=25000.0,
        positions=positions,
        short_positions={},
        tracked_symbols=tracked,
        candidates=candidates,
        cycle_summary=summary,
    )

    all_state = DB.get_all_state()
    assert "bot_state" in all_state
    assert "tracked_symbols" in all_state
    assert "top_candidates" in all_state
    assert "last_cycle_summary" in all_state


def test_load_positions_adopts_pre_existing_wallet_holdings():
    from bot.main import _load_positions
    from bot.strategy import MomentumStrategy

    strategy = MomentumStrategy(config)
    # Wallet holding BTC and ETH purchased before starting bot
    balance = {
        "SpotWallet": {
            "USD": {"Free": 10000.0, "Lock": 0.0},
            "BTC": {"Free": 0.5, "Lock": 0.0},
            "ETH": {"Free": 4.0, "Lock": 0.0},
        }
    }
    tickers = {
        "BTC/USD": {"LastPrice": 65000.0},
        "ETH/USD": {"LastPrice": 3400.0},
    }

    positions = _load_positions(strategy, balance, tickers)
    assert "BTC/USD" in positions
    assert "ETH/USD" in positions
    assert positions["BTC/USD"] == 65000.0
    assert positions["ETH/USD"] == 3400.0
