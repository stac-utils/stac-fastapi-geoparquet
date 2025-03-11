# https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layer

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --refresh

ADD . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

FROM python:3.12-slim
COPY --from=builder --chown=app:app /app/.venv /app/.venv

CMD [ "/app/.venv/bin/uvicorn", "stac_fastapi.geoparquet.main:app" ]
