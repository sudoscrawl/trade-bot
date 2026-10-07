"""Live execution loop. Run with ``python -m bot.main`` after configuration."""

import json
import logging
import signal
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bot.config import config
from bot.data.db.migrations import assert_schema_current
from bot.helpers.client import free_balance, portfolio_value_usd, ticker_data
from bot.risk import RiskManager
from bot.services.db_queries import DB
from bot.services.roostoo import RoostooClient
from bot.strategy import MomentumStrategy

logger = logging.getLogger(__name__)


def _exchange_rules(info: Mapping[str, Any], pair: str) -> tuple[int, float]:
    pairs_info = info.get("TradePairs", {}) if isinstance(info, Mapping) else {}
    rules = pairs_info.get(pair, {}) if isinstance(pairs_info, Mapping) else {}
    if not isinstance(rules, Mapping):
        return 6, 0.0
    return int(rules.get("AmountPrecision", 6)), float(rules.get("MiniOrder", 0.0))


def _order_detail(order: Mapping[str, Any]) -> Mapping[str, Any]:
    detail = order.get("OrderDetail", order)
    return detail if isinstance(detail, Mapping) else {}


def _filled_price(order: Mapping[str, Any], fallback: float) -> float:
    detail = _order_detail(order)
    price_val = detail.get("FilledAverPrice") or detail.get("Price")
    return float(price_val) if price_val is not None else fallback


def _signal_strength(indicators: Mapping[str, Any]) -> float:
    separation = abs(float(indicators.get("ema_sep_pct", 0.0)))
    rsi_value = float(indicators.get("rsi", 50.0))
    return min(
        1.0, separation / 0.12 * 0.6 + max(0.0, 1 - abs(rsi_value - 52) / 20) * 0.4
    )


def _volatility_pct(pair: str, prices: list[float] | None = None) -> float:
    if prices is None:
        db_rows = DB.get_prices(pair, limit=12)
        prices = [float(row["price"]) for row in db_rows]
    if len(prices) < 3:
        return 1.0
    recent = prices[-12:]
    returns = [
        abs(recent[index] / recent[index - 1] - 1) * 100
        for index in range(1, len(recent))
        if recent[index - 1] > 0
    ]
    return sum(returns) / len(returns) if returns else 1.0


def _select_tracked_symbols(
    exchange_info: Mapping[str, Any],
    tickers: Mapping[str, Mapping[str, Any]],
    min_count: int = 15,
) -> list[str]:
    """Select active trading pairs to monitor and trade across the exchange."""
    valid_pairs = set(tickers.keys())
    if exchange_info and isinstance(exchange_info, Mapping):
        pairs_info = exchange_info.get("TradePairs", {})
        if isinstance(pairs_info, Mapping) and pairs_info:
            valid_pairs = (
                valid_pairs.intersection(set(pairs_info.keys())) or valid_pairs
            )

    if getattr(config, "track_all_coins", True):
        # Track every single active coin listed on the exchange (~88 pairs)
        return sorted(
            [
                p
                for p in valid_pairs
                if float(tickers.get(p, {}).get("LastPrice", 0.0)) > 0
            ],
            key=lambda p: float(tickers.get(p, {}).get("UnitTradeValue", 0.0)),
            reverse=True,
        )

    # 1. Start with configured symbols that are actively trading
    selected: list[str] = [
        s
        for s in config.symbols
        if s in valid_pairs and float(tickers.get(s, {}).get("LastPrice", 0.0)) > 0
    ]

    # 2. Ensure we have at least `min_count` coins by adding highest USD-volume pairs
    target_count = max(min_count, len(config.symbols))
    if len(selected) < target_count or getattr(config, "auto_select_top_symbols", True):
        sorted_pairs = sorted(
            valid_pairs,
            key=lambda p: float(tickers.get(p, {}).get("UnitTradeValue", 0.0)),
            reverse=True,
        )
        for p in sorted_pairs:
            if (
                p not in selected
                and float(tickers.get(p, {}).get("LastPrice", 0.0)) > 0
            ):
                selected.append(p)
            if len(selected) >= target_count:
                break

    return selected


def _score_opportunity(
    pair: str,
    indicators: Mapping[str, Any],
    ticker: Mapping[str, Any],
    volatility: float,
) -> float:
    """Calculate a money-making opportunity score (higher = higher profit potential)."""
    if indicators.get("warming_up", False):
        return 0.0

    ema_sep = max(0.0, float(indicators.get("ema_sep_pct", 0.0)))
    rsi_val = float(indicators.get("rsi", 50.0))
    change_24h = float(ticker.get("Change", 0.0)) * 100.0
    unit_trade_val = float(ticker.get("UnitTradeValue", 0.0))
    price = float(ticker.get("LastPrice", 0.0))
    bid = float(ticker.get("MaxBid", 0.0))
    ask = float(ticker.get("MinAsk", 0.0))

    if price <= 0:
        return 0.0

    # 1. EMA trend strength (positive separation is rewarded)
    trend_factor = max(0.1, 1.0 + ema_sep * 10.0)

    # 2. RSI momentum positioning (optimal zone: 45 to 65)
    rsi_factor = max(0.1, 1.0 - abs(rsi_val - 55.0) / 30.0)

    # 3. 24h momentum (rewards positive trend)
    change_factor = max(0.2, 1.0 + (change_24h / 15.0))

    # 4. Volume / Liquidity factor
    volume_factor = (
        min(2.5, max(0.5, (unit_trade_val / 20_000.0) ** 0.25))
        if unit_trade_val > 0
        else 0.5
    )

    # 5. Volatility / Upside potential
    vol_factor = min(2.0, max(0.6, volatility / 1.2))

    # 6. Spread penalty (penalize wide bid-ask spreads)
    spread_bps = (ask - bid) / price * 10_000 if price > 0 else 0.0
    spread_factor = max(0.3, 1.0 - (spread_bps / 150.0))

    score = (
        trend_factor
        * rsi_factor
        * change_factor
        * volume_factor
        * vol_factor
        * spread_factor
    )
    return round(max(0.0, score), 4)


def _score_short_opportunity(
    pair: str,
    indicators: Mapping[str, Any],
    ticker: Mapping[str, Any],
    volatility: float,
) -> float:
    """Calculate a short opportunity score (higher = stronger bearish signal)."""
    if indicators.get("warming_up", False):
        return 0.0

    ema_sep = abs(min(0.0, float(indicators.get("ema_sep_pct", 0.0))))
    rsi_val = float(indicators.get("rsi", 50.0))
    change_24h = float(ticker.get("Change", 0.0)) * 100.0
    unit_trade_val = float(ticker.get("UnitTradeValue", 0.0))
    price = float(ticker.get("LastPrice", 0.0))
    bid = float(ticker.get("MaxBid", 0.0))
    ask = float(ticker.get("MinAsk", 0.0))

    if price <= 0:
        return 0.0

    # 1. EMA trend strength (negative separation rewarded for shorts)
    trend_factor = max(0.1, 1.0 + ema_sep * 10.0)

    # 2. RSI — overbought conditions are better for shorts
    rsi_factor = max(0.1, 1.0 - abs(rsi_val - 45.0) / 30.0)

    # 3. 24h momentum — falling assets are better short targets
    change_factor = max(0.2, 1.0 + (-change_24h / 15.0))

    # 4. Volume / Liquidity
    volume_factor = (
        min(2.5, max(0.5, (unit_trade_val / 20_000.0) ** 0.25))
        if unit_trade_val > 0
        else 0.5
    )

    # 5. Volatility
    vol_factor = min(2.0, max(0.6, volatility / 1.2))

    # 6. Spread penalty
    spread_bps = (ask - bid) / price * 10_000 if price > 0 else 0.0
    spread_factor = max(0.3, 1.0 - (spread_bps / 150.0))

    score = (
        trend_factor
        * rsi_factor
        * change_factor
        * volume_factor
        * vol_factor
        * spread_factor
    )
    return round(max(0.0, score), 4)


def _load_positions(
    strategy: MomentumStrategy,
    balance: Mapping[str, object],
    tickers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, float]:
    """Load active positions from DB and adopt any pre-existing wallet holdings."""
    saved = json.loads(DB.get_state("bot_positions", "{}") or "{}")
    active: dict[str, float] = {}

    # 1. Restore previously tracked bot positions
    for pair, entry_price in saved.items():
        asset = pair.split("/", 1)[0]
        if free_balance(dict(balance), asset) > 0:
            active[pair] = float(entry_price)
            strategy.notify_bought(pair, float(entry_price))

    # 2. Adopt any pre-existing non-USD coins in the wallet
    wallet = balance.get("SpotWallet") or balance.get("Wallet") or {}
    if isinstance(wallet, Mapping) and tickers:
        for asset, amounts in wallet.items():
            if asset.upper() == "USD":
                continue
            pair = f"{asset.upper()}/USD"
            if pair in active:
                continue
            free_qty = (
                float(amounts.get("Free", 0.0)) if isinstance(amounts, Mapping) else 0.0
            )
            if free_qty > 0 and pair in tickers:
                current_price = float(tickers[pair].get("LastPrice", 0.0))
                if current_price > 0:
                    active[pair] = current_price
                    strategy.notify_bought(pair, current_price)
                    logger.info(
                        "Adopted pre-existing wallet holding %s (qty=%s) @ $%.4f",
                        pair,
                        free_qty,
                        current_price,
                    )

    return active


def _load_short_positions(
    strategy: MomentumStrategy,
) -> dict[str, float]:
    """Load active short positions from DB state."""
    saved = json.loads(DB.get_state("bot_short_positions", "{}") or "{}")
    active: dict[str, float] = {}
    for pair, entry_price in saved.items():
        active[pair] = float(entry_price)
        strategy.notify_shorted(pair, float(entry_price))
    return active


def _save_positions(positions: Mapping[str, float]) -> None:
    DB.set_state("bot_positions", json.dumps(positions, sort_keys=True))


def _save_short_positions(positions: Mapping[str, float]) -> None:
    DB.set_state("bot_short_positions", json.dumps(positions, sort_keys=True))


def _save_state(
    session_id: str,
    risk: RiskManager,
    equity: float,
    available_usd: float,
    positions: Mapping[str, float],
    short_positions: Mapping[str, float],
    tracked_symbols: list[str],
    candidates: list[dict[str, Any]],
    cycle_summary: Mapping[str, Any],
) -> None:
    """Persist comprehensive operational bot state to the database."""
    now_iso = datetime.now(UTC).isoformat()
    peak_eq = max(risk.peak_equity, equity)
    drawdown_pct = round((peak_eq - equity) / peak_eq * 100, 2) if peak_eq > 0 else 0.0

    bot_state = {
        "status": "HALTED" if risk.halted else "RUNNING",
        "session_id": session_id,
        "last_cycle_timestamp": now_iso,
        "equity_usd": round(equity, 4),
        "peak_equity_usd": round(peak_eq, 4),
        "drawdown_pct": drawdown_pct,
        "risk_halted": risk.halted,
        "available_usd": round(available_usd, 4),
        "tracked_symbols_count": len(tracked_symbols),
        "open_positions_count": len(positions),
        "open_short_positions_count": len(short_positions),
        "open_positions": dict(positions),
        "open_short_positions": dict(short_positions),
    }

    DB.set_multiple_states(
        {
            "bot_positions": json.dumps(positions, sort_keys=True),
            "bot_short_positions": json.dumps(short_positions, sort_keys=True),
            "bot_state": json.dumps(bot_state, sort_keys=True),
            "tracked_symbols": json.dumps(tracked_symbols),
            "top_candidates": json.dumps(candidates[:10]),
            "last_cycle_summary": json.dumps(dict(cycle_summary), sort_keys=True),
        }
    )


def _accepted(order: Mapping[str, object], pair: str, side: str) -> bool:
    if order.get("Success", True):
        return True
    logger.error("LIVE %s %s rejected: %s", side, pair, order.get("ErrMsg", order))
    return False


def _configure_logging() -> None:
    log_path = Path(config.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=formatter,
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)],
        force=True,
    )


def run() -> None:
    config.validate_live_execution()
    _configure_logging()
    assert_schema_current()
    client, strategy = RoostooClient(), MomentumStrategy(config)
    running, session_id = True, str(uuid.uuid4())

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        exchange_info = client.get_exchange_info()
        initial_balance = client.get_balance()
        initial_tickers = ticker_data(client)
        initial_tracked = _select_tracked_symbols(
            exchange_info, initial_tickers, config.min_symbols_tracked
        )

        risk = RiskManager(
            portfolio_value_usd(initial_balance, initial_tickers), config
        )
        positions = _load_positions(strategy, initial_balance, initial_tickers)
        short_positions = _load_short_positions(strategy)

        # Bulk restore history for all tracked symbols from DB in a single query
        history_bulk = DB.get_recent_prices_bulk(initial_tracked, config.min_history)
        for pair in initial_tracked:
            pair_prices = [float(row["price"]) for row in history_bulk.get(pair, [])]
            strategy.restore(pair, pair_prices)

        logger.info(
            "Bot initialized with %d tracked coins (min required: %d). "
            "Open longs: %d, Open shorts: %d",
            len(initial_tracked),
            config.min_symbols_tracked,
            len(positions),
            len(short_positions),
        )

        while running:
            try:
                tickers, balance = ticker_data(client), client.get_balance()
                tracked_symbols = _select_tracked_symbols(
                    exchange_info, tickers, config.min_symbols_tracked
                )
                selected = {
                    pair: tickers[pair] for pair in tracked_symbols if pair in tickers
                }
                DB.insert_prices(selected)
                equity = portfolio_value_usd(balance, tickers)
                risk.update_equity(equity)
                DB.insert_equity(equity)
                available_usd = free_balance(balance, "USD")

                cycle_sells: list[str] = []
                cycle_buys: list[str] = []
                cycle_shorts: list[str] = []
                cycle_covers: list[str] = []
                buy_candidates: list[dict[str, Any]] = []
                short_candidates: list[dict[str, Any]] = []

                # ── 1. Process SELL signals for long positions ────────────
                for pair in list(positions.keys()):
                    if pair not in selected:
                        continue
                    ticker = selected[pair]
                    price = float(ticker.get("LastPrice", 0.0))
                    action = strategy.update(pair, price)

                    if action == "SELL":
                        asset = pair.split("/", 1)[0]
                        quantity = free_balance(balance, asset)
                        precision, _ = _exchange_rules(exchange_info, pair)
                        factor = 10**precision
                        quantity = int(quantity * factor) / factor
                        if quantity > 0:
                            result = client.place_order(pair, "SELL", quantity)
                            if _accepted(result, pair, "SELL"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(
                                    result,
                                    mode="LIVE",
                                    session_id=session_id,
                                    fallback_pair=pair,
                                    fallback_side="SELL",
                                    fallback_price=fill,
                                    fallback_qty=quantity,
                                )
                                strategy.notify_sold(pair, fill < positions[pair])
                                positions.pop(pair, None)
                                _save_positions(positions)
                                available_usd += quantity * fill
                                cycle_sells.append(pair)
                                logger.info(
                                    "LIVE SELL %s qty=%s @ %.8f", pair, quantity, fill
                                )

                # ── 2. Process COVER signals for short positions ──────────
                for pair in list(short_positions.keys()):
                    if pair not in selected:
                        continue
                    ticker = selected[pair]
                    price = float(ticker.get("LastPrice", 0.0))
                    action = strategy.update(pair, price)

                    if action == "COVER":
                        # To cover a short: BUY back the asset
                        precision, _ = _exchange_rules(exchange_info, pair)
                        cover_quantity = risk.quantity(
                            available_usd * 0.5, price, precision
                        )
                        if cover_quantity > 0:
                            result = client.place_order(pair, "BUY", cover_quantity)
                            if _accepted(result, pair, "BUY"):
                                fill = _filled_price(result, price)
                                DB.insert_trade_from_order(
                                    result,
                                    mode="LIVE",
                                    session_id=session_id,
                                    fallback_pair=pair,
                                    fallback_side="BUY",
                                    fallback_price=fill,
                                    fallback_qty=cover_quantity,
                                )
                                was_loss = fill > short_positions[pair]
                                strategy.notify_covered(pair, was_loss)
                                short_positions.pop(pair, None)
                                _save_short_positions(short_positions)
                                cycle_covers.append(pair)
                                logger.info(
                                    "LIVE COVER %s qty=%s @ %.8f",
                                    pair,
                                    cover_quantity,
                                    fill,
                                )

                # ── 3. Evaluate BUY and SHORT candidates ──────────────────
                for pair, ticker in selected.items():
                    if pair in positions or pair in short_positions:
                        continue
                    price = float(ticker.get("LastPrice", 0.0))
                    action = strategy.update(pair, price)
                    indicators = strategy.indicators(pair)
                    vol = _volatility_pct(pair, list(strategy._state(pair).prices))

                    if action == "BUY":
                        score = _score_opportunity(pair, indicators, ticker, vol)
                        buy_candidates.append(
                            {
                                "pair": pair,
                                "score": score,
                                "price": price,
                                "indicators": indicators,
                                "volatility": vol,
                                "24h_change": float(ticker.get("Change", 0.0)),
                                "volume_usd": float(ticker.get("UnitTradeValue", 0.0)),
                                "side": "BUY",
                            }
                        )
                    elif action == "SHORT" and config.enable_shorts:
                        score = _score_short_opportunity(pair, indicators, ticker, vol)
                        short_candidates.append(
                            {
                                "pair": pair,
                                "score": score,
                                "price": price,
                                "indicators": indicators,
                                "volatility": vol,
                                "24h_change": float(ticker.get("Change", 0.0)),
                                "volume_usd": float(ticker.get("UnitTradeValue", 0.0)),
                                "side": "SHORT",
                            }
                        )

                # Sort candidates by score (highest first)
                buy_candidates.sort(key=lambda c: c["score"], reverse=True)
                short_candidates.sort(key=lambda c: c["score"], reverse=True)

                # Merge and interleave: take the best signals regardless of direction
                all_candidates = sorted(
                    buy_candidates + short_candidates,
                    key=lambda c: c["score"],
                    reverse=True,
                )

                # ── 4. Execute the best candidate entries ─────────────────
                total_open = len(positions) + len(short_positions)
                available_slots = max(0, config.max_open_positions - total_open)

                if available_slots > 0 and not risk.halted and all_candidates:
                    logger.info(
                        "Found %d BUY and %d SHORT candidates. Top picks: %s",
                        len(buy_candidates),
                        len(short_candidates),
                        ", ".join(
                            f"{c['pair']} ({c['side']} score={c['score']})"
                            for c in all_candidates[:available_slots]
                        ),
                    )
                    for cand in all_candidates[:available_slots]:
                        pair = cand["pair"]
                        price = cand["price"]
                        indicators = cand["indicators"]
                        vol = cand["volatility"]

                        precision, exchange_minimum = _exchange_rules(
                            exchange_info, pair
                        )
                        budget = risk.size_usd(
                            available_usd,
                            equity,
                            _signal_strength(indicators),
                            vol,
                        )
                        quantity = risk.quantity(budget, price, precision)

                        if quantity > 0 and quantity * price >= max(
                            config.min_order_usd, exchange_minimum
                        ):
                            if cand["side"] == "BUY":
                                result = client.place_order(pair, "BUY", quantity)
                                if _accepted(result, pair, "BUY"):
                                    fill = _filled_price(result, price)
                                    DB.insert_trade_from_order(
                                        result,
                                        mode="LIVE",
                                        session_id=session_id,
                                        fallback_pair=pair,
                                        fallback_side="BUY",
                                        fallback_price=fill,
                                        fallback_qty=quantity,
                                    )
                                    strategy.notify_bought(pair, fill)
                                    positions[pair] = fill
                                    _save_positions(positions)
                                    available_usd -= (
                                        quantity * fill * (1 + config.commission_rate)
                                    )
                                    cycle_buys.append(pair)
                                    logger.info(
                                        "LIVE BUY %s qty=%s @ %.8f (score=%.4f)",
                                        pair,
                                        quantity,
                                        fill,
                                        cand["score"],
                                    )

                            elif cand["side"] == "SHORT":
                                # SHORT: SELL the asset to open a short position
                                result = client.place_order(pair, "SELL", quantity)
                                if _accepted(result, pair, "SELL"):
                                    fill = _filled_price(result, price)
                                    DB.insert_trade_from_order(
                                        result,
                                        mode="LIVE",
                                        session_id=session_id,
                                        fallback_pair=pair,
                                        fallback_side="SELL",
                                        fallback_price=fill,
                                        fallback_qty=quantity,
                                    )
                                    strategy.notify_shorted(pair, fill)
                                    short_positions[pair] = fill
                                    _save_short_positions(short_positions)
                                    available_usd -= (
                                        quantity * fill * config.commission_rate
                                    )
                                    cycle_shorts.append(pair)
                                    logger.info(
                                        "LIVE SHORT %s qty=%s @ %.8f (score=%.4f)",
                                        pair,
                                        quantity,
                                        fill,
                                        cand["score"],
                                    )

                # ── 5. Save state to DB ───────────────────────────────────
                cycle_summary = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tracked_coins_count": len(selected),
                    "open_long_positions": len(positions),
                    "open_short_positions": len(short_positions),
                    "equity": round(equity, 4),
                    "buys_executed": cycle_buys,
                    "sells_executed": cycle_sells,
                    "shorts_executed": cycle_shorts,
                    "covers_executed": cycle_covers,
                    "buy_candidates_count": len(buy_candidates),
                    "short_candidates_count": len(short_candidates),
                }
                _save_state(
                    session_id=session_id,
                    risk=risk,
                    equity=equity,
                    available_usd=available_usd,
                    positions=positions,
                    short_positions=short_positions,
                    tracked_symbols=list(selected.keys()),
                    candidates=all_candidates,
                    cycle_summary=cycle_summary,
                )

                logger.info(
                    "Cycle complete: %d coins monitored, %d longs, %d shorts, "
                    "equity=$%.2f",
                    len(selected),
                    len(positions),
                    len(short_positions),
                    equity,
                )
                time.sleep(config.poll_interval_seconds)
            except Exception:
                logger.exception("cycle failed; retrying after poll interval")
                time.sleep(config.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    run()
