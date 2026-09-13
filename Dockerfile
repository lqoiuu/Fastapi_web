FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.13-slim AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 ticketing \
    && useradd --uid 10001 --gid ticketing --home-dir /app \
        --no-create-home --shell /usr/sbin/nologin ticketing

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=ticketing:ticketing alembic.ini ./alembic.ini
COPY --chown=ticketing:ticketing alembic ./alembic

RUN mkdir -p /app/var/attachments /app/var/exports \
    && chown -R ticketing:ticketing /app

USER ticketing

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"]

CMD ["uvicorn", "ticketing.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
