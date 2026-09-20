FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ARG W4_SOURCE_SHA
LABEL org.opencontainers.image.source="https://github.com/Team-1AM-Club/Project_EPICK_Engine" \
      org.opencontainers.image.revision="${W4_SOURCE_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system --gid 10001 epick \
    && adduser --system --uid 10001 --ingroup epick --home /nonexistent epick

WORKDIR /app
COPY deploy/question-core/requirements.lock /tmp/requirements.lock
COPY epick_w4 /app/epick_w4
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock

USER 10001:10001
CMD ["python", "-m", "epick_w4.w1_runtime"]
