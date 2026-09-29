FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG W4_SOURCE_SHA
LABEL org.opencontainers.image.source="https://github.com/Team-1AM-Club/Project_EPICK_Engine" \
      org.opencontainers.image.revision="${W4_SOURCE_SHA}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    AWS_EC2_METADATA_DISABLED=true
WORKDIR /app
COPY deploy/question-core/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
    && groupadd --gid 10001 w4 \
    && useradd --uid 10001 --gid 10001 --no-create-home w4 \
    && mkdir -p /var/lib/w4 \
    && chown 10001:10001 /var/lib/w4
COPY epick_w4/ /app/epick_w4/
COPY samples/question-core-w1-context-20260919/ /app/samples/question-core-w1-context-20260919/
USER 10001:10001
ENTRYPOINT ["python", "-m", "epick_w4.question_core_runtime"]
CMD ["relay"]
