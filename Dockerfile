# Multi-stage build — works with plain docker build (no BuildKit required).
#
# Stage 1 (builder): installs uv, exports pinned requirements from uv.lock,
#                    installs all production deps into /install prefix.
# Stage 2 (final):   minimal runtime image, non-root user, no build tools.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv via the official install script (curl is available in slim)
RUN apt-get update -qq && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/0.6.14/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && apt-get remove -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests — layer cache only busts when these change
COPY pyproject.toml uv.lock ./
# README.md is excluded by .dockerignore but referenced in pyproject.toml — stub it
RUN touch README.md

# Export exact pinned requirements from the lockfile
RUN uv export --no-dev --no-hashes --frozen -o /tmp/requirements.txt

# Copy application source (needed for -e . editable install)
COPY bot ./bot

# Install all deps + the package into an isolated prefix
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt \
    && pip install --no-cache-dir --prefix=/install --no-deps -e .

# ── Stage 2: final runtime ────────────────────────────────────────────────────
FROM python:3.13-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FILE=/app/logs/quant-bot.log

WORKDIR /app

# Non-root user
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --no-create-home app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source and Alembic config
COPY --chown=app:app bot ./bot
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations

# Log directory — bind-mount ./logs here in production
RUN mkdir -p /app/logs && chown app:app /app/logs

USER app

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
