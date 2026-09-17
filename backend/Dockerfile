FROM python:3.12-slim

WORKDIR /app

# The worker-only Compose stack bind-mounts the RDS CA bundle at this path.
# Create its parent in the immutable image so the mount target is deterministic
# even with a read-only root filesystem.
RUN mkdir -p /run/epick

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
COPY contracts ./contracts

RUN python -m pip install --no-cache-dir .

# Keep schema changes and their release controls in the same immutable image as
# the API. The normal API command remains unchanged; deployment runs migration
# commands explicitly with the dedicated migration principal.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY infra ./infra

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
