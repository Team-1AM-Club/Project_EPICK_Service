from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).parents[2]


def test_w3_authority_has_an_isolated_process_entrypoint() -> None:
    script = (BACKEND_ROOT / "scripts/run_w3_authority_adapter.py").read_text(encoding="utf-8")

    assert "create_configured_w3_authority_private_app" in script
    assert 'port=8043' in script
    assert "access_log=False" in script


def test_w3_authority_compose_profile_is_loopback_only_and_hardened() -> None:
    compose = (BACKEND_ROOT / "infra/w1-runtime.compose.yml").read_text(encoding="utf-8")
    service = compose.split("  w1-w3-authority:", 1)[1]

    assert 'profiles: ["w3-authority"]' in service
    assert "W1_W3_AUTHORITY_ENV_FILE" in service
    assert 'command: ["python", "scripts/run_w3_authority_adapter.py"]' in service
    assert '"127.0.0.1:8043:8043"' in service
    assert '"0.0.0.0:8043:8043"' not in service
    assert "read_only: true" in service
    assert "cap_drop:" in service and "- ALL" in service
    assert "no-new-privileges:true" in service


def test_w3_authority_database_role_is_column_minimal_and_preflighted() -> None:
    roles = (BACKEND_ROOT / "infra/postgres/runtime_roles.sql").read_text(encoding="utf-8")
    privileges = (BACKEND_ROOT / "infra/postgres/runtime_privileges.sql").read_text(
        encoding="utf-8"
    )
    preflight = (BACKEND_ROOT / "scripts/postgres_runtime_privilege_preflight.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE ROLE epick_w3_authority NOLOGIN NOSUPERUSER NOBYPASSRLS" in roles
    for grant in (
        "GRANT SELECT (id, account_status, deletion_epoch)\nON TABLE users",
        (
            "GRANT SELECT (id, owner_user_id, status, owner_deletion_epoch, "
            "analysis_input_version)\nON TABLE jobs"
        ),
        (
            "GRANT SELECT (job_id, owner_user_id, source_id, "
            "analysis_input_version)\nON TABLE job_source_links"
        ),
        "GRANT SELECT (id, company_id)\nON TABLE sources",
    ):
        assert grant in privileges
    assert "epick_w3_authority" in preflight
    for forbidden_column in ("email", "display_name", "safe_failure_message", "canonical_url"):
        assert f'"{forbidden_column}", "SELECT", False' in preflight
