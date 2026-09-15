from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_local",
)
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_test",
)
