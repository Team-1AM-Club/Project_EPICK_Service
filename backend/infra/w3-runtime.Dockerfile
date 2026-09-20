FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /build

RUN python -m pip install --no-cache-dir "uv==0.8.15"
COPY pyproject.toml uv.lock ./
COPY specs/001-source-knowledge-validation/spec.md ./specs/001-source-knowledge-validation/spec.md
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

LABEL org.opencontainers.image.source="https://github.com/Team-1AM-Club/Project_EPICK_Service" \
      org.opencontainers.image.revision="3b23e0843a134fb341e6a256576ccf52fedbf4a8" \
      io.epick.w3.receipt-head="34660343f197c74cc03459a93e0160e46adbcd2b" \
      io.epick.w3.contract="w3.private.core-decision/0.1-candidate" \
      io.epick.w3.policy-revision="w3.retention/1.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PATH="/opt/w3/.venv/bin:${PATH}"

RUN groupadd --gid 10001 w3 \
    && useradd --uid 10001 --gid 10001 --no-create-home w3 \
    && mkdir -p /state \
    && chown 10001:10001 /state

COPY --from=builder /build/.venv /opt/w3/.venv

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python", "-m", "w3_knowledge.core_runtime_cli"]
CMD ["smoke", "--directory", "/tmp/smoke"]
