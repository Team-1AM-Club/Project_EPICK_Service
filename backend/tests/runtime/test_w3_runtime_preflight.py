from dataclasses import replace

import pytest

from app.runtime.w3_runtime_preflight import (
    W3RuntimeComposeInspection,
    W3RuntimeImageInspection,
    W3RuntimePersistenceInspection,
    W3RuntimePreflightConfig,
    W3RuntimePreflightError,
    verify_w3_actual_runtime,
)
from scripts.preflight_w3_actual_runtime import (
    RETENTION_SECONDS,
    _count_only,
    _inspect_command,
    _migration_blockers,
    _policy_revision,
)

IMPLEMENTATION_SHA = "66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b"
RECEIPT_HEAD_SHA = "bad8671b2ea8d02bdce2157120b94d2b7edf09d8"
POLICY_REVISION = "w3.retention/1.1"
DIGEST = "sha256:" + "a" * 64


def _config() -> W3RuntimePreflightConfig:
    return W3RuntimePreflightConfig(
        expected_implementation_sha=IMPLEMENTATION_SHA,
        expected_receipt_head_sha=RECEIPT_HEAD_SHA,
        expected_policy_revision=POLICY_REVISION,
        expected_image_digest=DIGEST,
        expected_runtime_uid=10001,
        expected_state_volume="epick-w3-core-runtime-state",
        database_path="/state/core.db",
    )


def _image() -> W3RuntimeImageInspection:
    return W3RuntimeImageInspection(
        implementation_sha=IMPLEMENTATION_SHA,
        receipt_head_sha=RECEIPT_HEAD_SHA,
        policy_revision=POLICY_REVISION,
        image_digest=DIGEST,
        runtime_user="10001:10001",
    )


def _compose() -> W3RuntimeComposeInspection:
    return W3RuntimeComposeInspection(
        runtime_user="10001:10001",
        read_only=True,
        state_volume="epick-w3-core-runtime-state",
        state_target="/state",
        cap_drop=("ALL",),
        tmpfs=("/tmp:rw,noexec,nosuid,nodev,size=16m",),
    )


def _persistence() -> W3RuntimePersistenceInspection:
    return W3RuntimePersistenceInspection(
        database_path="/state/core.db",
        initialized=True,
        smoke_status="LOCAL_VERIFIED_NOT_DEPLOYED",
        smoke_aws_calls=0,
        policy_revision=POLICY_REVISION,
        migration_blockers={"owner_tombstone_without_deleted_at": 0},
        before_counts={"deliveries": 0, "counters": 0, "tombstones": 0},
        after_counts={"deliveries": 0, "counters": 0, "tombstones": 0},
    )


def test_valid_runtime_returns_only_safe_readiness_fields() -> None:
    result = verify_w3_actual_runtime(
        config=_config(),
        image=_image(),
        compose=_compose(),
        persistence=_persistence(),
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "image": "implementation_receipt_and_digest_verified",
        "runtime": "non_root_read_only",
        "state": "single_local_volume_persisted",
        "smoke": "local_no_network_verified",
        "policy": "w3_retention_1_1_verified",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("implementation_sha", "0" * 40, "implementation revision"),
        ("receipt_head_sha", "0" * 40, "receipt HEAD"),
        ("policy_revision", "w3.retention/0.0", "policy revision"),
        ("image_digest", "sha256:" + "b" * 64, "image digest"),
        ("runtime_user", "0:0", "runtime user"),
    ],
)
def test_image_identity_and_uid_mismatch_fail_closed(
    field: str, value: str, message: str
) -> None:
    with pytest.raises(W3RuntimePreflightError, match=message):
        verify_w3_actual_runtime(
            config=_config(),
            image=replace(_image(), **{field: value}),
            compose=_compose(),
            persistence=_persistence(),
        )


def test_writable_root_or_wrong_state_mount_is_rejected() -> None:
    compose = W3RuntimeComposeInspection(
        runtime_user="10001:10001",
        read_only=False,
        state_volume="host-bind",
        state_target="/data",
        cap_drop=(),
        tmpfs=(),
    )

    with pytest.raises(W3RuntimePreflightError, match="read-only"):
        verify_w3_actual_runtime(
            config=_config(), image=_image(), compose=compose, persistence=_persistence()
        )


def test_restart_count_mismatch_is_rejected() -> None:
    persistence = W3RuntimePersistenceInspection(
        database_path="/state/core.db",
        initialized=True,
        smoke_status="LOCAL_VERIFIED_NOT_DEPLOYED",
        smoke_aws_calls=0,
        policy_revision=POLICY_REVISION,
        migration_blockers={"owner_tombstone_without_deleted_at": 0},
        before_counts={"deliveries": 1},
        after_counts={"deliveries": 0},
    )

    with pytest.raises(W3RuntimePreflightError, match="restart"):
        verify_w3_actual_runtime(
            config=_config(), image=_image(), compose=_compose(), persistence=persistence
        )


def test_policy_migration_blocker_is_rejected() -> None:
    persistence = replace(
        _persistence(), migration_blockers={"owner_tombstone_without_deleted_at": 1}
    )

    with pytest.raises(W3RuntimePreflightError, match="migration blocker"):
        verify_w3_actual_runtime(
            config=_config(), image=_image(), compose=_compose(), persistence=persistence
        )


def test_operator_collection_uses_policy_input_and_safe_inspect_fields() -> None:
    report = {
        "policy_revision": POLICY_REVISION,
        "migration_blockers": {"owner_tombstone_without_deleted_at": 0},
        "deliveries": [{"state": "PENDING"}, {"state": "HELD"}],
    }

    assert RETENTION_SECONDS == "1209600"
    assert _inspect_command(["runtime"])[-1] == RETENTION_SECONDS
    assert _count_only(report) == {
        "deliveries": 2,
        "state_pending": 1,
        "state_held": 1,
    }
    assert _policy_revision(report, report) == POLICY_REVISION
    assert _migration_blockers(report, report) == {
        "owner_tombstone_without_deleted_at": 0
    }
