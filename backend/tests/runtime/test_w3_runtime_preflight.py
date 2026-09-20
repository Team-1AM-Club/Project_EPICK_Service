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

SOURCE_SHA = "c7e6788168c048941bdabe7ed8cb01007edeecec"
DIGEST = "sha256:" + "a" * 64


def _config() -> W3RuntimePreflightConfig:
    return W3RuntimePreflightConfig(
        expected_source_sha=SOURCE_SHA,
        expected_image_digest=DIGEST,
        expected_runtime_uid=10001,
        expected_state_volume="epick-w3-core-runtime-state",
        database_path="/state/core.db",
    )


def _image() -> W3RuntimeImageInspection:
    return W3RuntimeImageInspection(
        source_sha=SOURCE_SHA,
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
        before_counts={"deliveries": 0},
        after_counts={"deliveries": 0},
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
        "image": "source_and_digest_verified",
        "runtime": "non_root_read_only",
        "state": "single_local_volume_persisted",
        "smoke": "local_no_network_verified",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_sha", "0" * 40, "source revision"),
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
        before_counts={"deliveries": 1},
        after_counts={"deliveries": 0},
    )

    with pytest.raises(W3RuntimePreflightError, match="restart"):
        verify_w3_actual_runtime(
            config=_config(), image=_image(), compose=_compose(), persistence=persistence
        )
