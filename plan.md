# Quant Bot Plan

## Current implementation

- [x] Centralized all settings in `bot/config.py`.
- [x] Implemented direct exchange execution; there is no paper-trading path.
- [x] Ported and tuned the EMA/RSI momentum logic from the reference bot.
- [x] Added dynamic position sizing, cash reserve, position cap, stop loss, and drawdown circuit breaker.
- [x] Persisted market data, equity, completed orders, and bot-owned entry prices.

## Before enabling live execution

- [ ] Create/upgrade the database schema: `./.venv/bin/alembic upgrade head`.
- [ ] Set `API_KEY`, `API_SECRET`, and `LIVE_TRADING_ENABLED=true` in `.env`.
- [ ] Confirm `EXCHANGE_BASE_URL`, permitted symbols, minimum order sizes, and account balances.
- [ ] Review the values in `bot/config.py`, especially allocation, stop loss, and max drawdown.
- [ ] Run the test suite and review exchange connectivity before leaving the process unattended.

## Ongoing operation

- [ ] Start with `python -m bot.main` and retain logs/database backups.
- [ ] Review fills, equity curve, and drawdown at least daily.
- [ ] Re-tune only after evaluating enough completed trades, including fees and slippage.
