from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("w3 execution modes")
    group.addoption(
        "--run-live", action="store_true", default=False, help="명시적 LIVE provider 검증 허용"
    )
    group.addoption(
        "--pm-sample", action="store", default=None, help="로컬 PM observed 패키지 경로"
    )
    group.addoption(
        "--legacy-sample", action="store", default=None, help="로컬 legacy 수집 샘플 경로"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-live"):
        marker = pytest.mark.skip(reason="LIVE 검증은 --run-live 및 별도 실행 설정이 필요합니다.")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(marker)
