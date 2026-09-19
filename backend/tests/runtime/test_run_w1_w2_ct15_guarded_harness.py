from __future__ import annotations

import pytest

from scripts.run_w1_w2_ct15_guarded_harness import Ct15HarnessError, _require_configuration


def _set_valid_ct15_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("W1_CT15_EXECUTE_SYNTHETIC", "YES")
    monkeypatch.setenv("W1_CT15_RUN_ID", "ct15-guarded-harness")
    monkeypatch.setenv(
        "W1_CT15_SEED_DATABASE_URL",
        "postgresql+psycopg://fixture:secret@db.example/epick_ct15_fixture",
    )


def test_guarded_harness_requires_explicit_synthetic_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_ct15_environment(monkeypatch)
    monkeypatch.setenv("W1_CT15_EXECUTE_SYNTHETIC", "NO")

    with pytest.raises(Ct15HarnessError, match="W1_CT15_EXECUTE_SYNTHETIC"):
        _require_configuration()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("W1_CT15_RUN_ID", "not-ct15", "ct15-"),
        (
            "W1_CT15_SEED_DATABASE_URL",
            "postgresql+psycopg://fixture:secret@db.example/epick_staging",
            "PostgreSQL CT15 database",
        ),
        (
            "W1_CT15_SEED_DATABASE_URL",
            "mysql://fixture:secret@db.example/epick_ct15_fixture",
            "PostgreSQL CT15 database",
        ),
    ],
)
def test_guarded_harness_rejects_non_isolated_input(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    _set_valid_ct15_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(Ct15HarnessError, match=message):
        _require_configuration()


def test_guarded_harness_accepts_a_synthetic_ct15_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_ct15_environment(monkeypatch)

    run_id, database_url = _require_configuration()

    assert run_id == "ct15-guarded-harness"
    assert database_url.endswith("/epick_ct15_fixture")
