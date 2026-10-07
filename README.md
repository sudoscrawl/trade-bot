# Quant Bot

Direct-execution spot momentum bot for a Roostoo-compatible exchange. It can place live orders; there is no paper-trading mode.

## Run locally

Requirements: Python 3.14+, [uv](https://docs.astral.sh/uv/), access to a PostgreSQL database, and exchange credentials.

1. Create your local configuration.

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set `DB_URL`, `API_KEY`, `API_SECRET`, and `LIVE_TRADING_ENABLED=true`. The default `LIVE_TRADING_ENABLED=false` is intentional: the bot refuses to start until live execution is explicitly enabled.

3. Install the locked dependencies and start the bot.

   ```bash
   make sync
   make run
   ```

`make run` applies outstanding Alembic migrations before starting `python -m bot.main`. It uses `uv run`, so it does not depend on a manually created `.venv`. Press `Ctrl-C` to stop the local process.

Useful commands:

```bash
make test
make migrate
make migration message='describe_change'
make help
```

All settings are centralized in [bot/config.py](bot/config.py). The bot uses an EMA/RSI momentum entry, 2.25% stop loss, 3.5% take profit, dynamic sizing, a 4-position limit, cash reserve, and a 15% peak-to-trough drawdown circuit breaker. See [plan.md](plan.md) for the live-launch checklist.

## Docker + PostgreSQL

The multi-stage [Dockerfile](Dockerfile) builds production dependencies in a builder image and copies only the installed application into a non-root runtime image. [docker-compose.yml](docker-compose.yml) uses the hosted Neon PostgreSQL URL from `.env`, runs `alembic upgrade head`, and then starts the bot.

```bash
cp .env.example .env
# Set Neon DB_URL, API_KEY, API_SECRET, and LIVE_TRADING_ENABLED=true.
make docker-up
```

Neon persists the PostgreSQL data. The bot log persists on the host at `./logs/quant-bot.log`; `logs/` is intentionally ignored by Git. Use `make docker-logs` for the container stream and `make docker-down` to stop the container. The Make targets use Docker Compose when its v2 plugin is installed, and automatically fall back to plain Docker when it is not.
