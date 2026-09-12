import pytest

from w3_knowledge.models import Audience, PolicyDecision, Purpose
from w3_knowledge.projection import ProjectionDenied, project_bundle
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request


def test_return_path_rechecks_authorization() -> None:
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    with pytest.raises(ProjectionDenied):
        project_bundle(
            bundle=response.bundle,
            context=request().context,
            policy=FakePolicy(PolicyDecision.DENY),
        )


def test_live_mode_is_blocked_without_explicit_provider_configuration() -> None:
    live = request().model_copy(
        update={
            "context": request().context.model_copy(
                update={"mode": "LIVE", "purpose": "PROVIDER_POC"}
            )
        }
    )
    response = structure(
        live,
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    assert response.errors[0].code.value == "LIVE_CONFIGURATION_REQUIRED"


def test_production_w4_cannot_receive_synthetic_bundle() -> None:
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    production_context = request().context.model_copy(
        update={"audience": Audience.W4_KNOWLEDGE, "purpose": Purpose.PRODUCTION_STRUCTURE}
    )
    with pytest.raises(ProjectionDenied):
        project_bundle(bundle=response.bundle, context=production_context, policy=FakePolicy())
