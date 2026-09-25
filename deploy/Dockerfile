# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

RUN groupadd --system --gid 10001 w3 \
    && useradd --system --uid 10001 --gid w3 --home-dir /nonexistent --shell /usr/sbin/nologin w3 \
    && mkdir -p /app /var/lib/w3 \
    && chown -R w3:w3 /app /var/lib/w3

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY uv.lock pyproject.toml ./
COPY specs/001-source-knowledge-validation/spec.md ./specs/001-source-knowledge-validation/spec.md
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

USER w3
EXPOSE 8764
VOLUME ["/var/lib/w3"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8764/health', timeout=2))['status']=='ok'"]
ENTRYPOINT ["/app/.venv/bin/python", "-m", "w3_knowledge.c01.http"]
