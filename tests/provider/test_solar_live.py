"""실제 provider PoC는 명시 옵션·합성 입력·비밀 주입이 모두 있을 때만 실행한다."""

from __future__ import annotations

import os

import pytest

from w3_knowledge.adapters.solar import SolarExtractionAdapter
from w3_knowledge.config import RuntimeConfiguration, live_execution_error
from w3_knowledge.models import EvaluationMode, Purpose
from tests.support.factories import source_with_text


@pytest.mark.live
def test_live_solar_poc_uses_only_explicit_synthetic_configuration(
    pytestconfig: pytest.Config,
) -> None:
    if not pytestconfig.getoption("--run-live"):
        pytest.skip("--run-live가 필요합니다.")
    api_key = os.getenv("UPSTAGE_API_KEY")
    model_id = os.getenv("W3_SOLAR_MODEL_ID")
    base_url = os.getenv("W3_SOLAR_BASE_URL")
    if not api_key or not model_id or not base_url or os.getenv("W3_ALLOW_PROVIDER_POC") != "1":
        pytest.skip("명시적 PoC 허용과 provider 설정이 없습니다.")
    config = RuntimeConfiguration(
        model_id=model_id,
        provider_base_url=base_url,
        api_key=api_key,
        allow_provider_poc=True,
    )
    assert live_execution_error(EvaluationMode.LIVE, Purpose.PROVIDER_POC, config) is None
    candidates = SolarExtractionAdapter(config).extract_claims(source=source_with_text())
    assert isinstance(candidates, tuple)
