from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.integration.db.test_runtime_workers import (
    test_w2_fetch_checkpoint_retry_preserves_revision_pin_and_current_fence as _assert_dispatch_lookup_equivalence,
)

pytest_plugins = ("tests.integration.db.conftest",)


@pytest.fixture(autouse=True)
def clean_dispatch_lookup_rows(migrated_engine: Engine) -> Iterator[None]:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


@pytest.mark.postgres
@pytest.mark.parametrize("direct", [False, True], ids=["core", "direct"])
def test_dispatch_and_lookup_share_revision_fence_epoch_and_analysis_input(
    migrated_engine: Engine,
    db_session: Session,
    direct: bool,
) -> None:
    """Exercise the canonical dispatch/resume/lookup flow for both W2 command kinds.

    The shared scenario asserts a positive stored policy revision on the resumed
    dispatch, exact lookup payload equality, current execution fence and deletion
    epoch, and successful W2 parsing of the analysis input version.
    """

    _assert_dispatch_lookup_equivalence(
        migrated_engine,
        db_session,
        direct,
    )
