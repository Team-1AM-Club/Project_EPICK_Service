from test_restriction_contract import event, parse
from test_restriction_runtime import index_request
from tests.support.factories import request
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from w3_knowledge.models import PolicyDecision, ProcessingStatus, Artifact, SourceRef, SourceInput
from w3_knowledge.restriction.contracts import Event, IndexKey
from w3_knowledge.restriction.guard import structure_guarded
from w3_knowledge.restriction.store import Store
from w3_knowledge.service import W3Ports


def setup(store):
    value = event()
    value["aggregate_id"] = value["payload"]["source_id"] = "synthetic-source"
    value["payload"]["index_key"]["source_version_id"] = "synthetic-source-v1"
    store.consume(parse(Event, value))
    store.index(index_request(value))
    keys = {"synthetic-source": parse(IndexKey, value["payload"]["index_key"])}
    return value, keys


def ports(extraction=None, policy=PolicyDecision.ALLOW):
    return W3Ports(
        FakePolicy(policy),
        FakeRetainedText(),
        extraction or FakeExtraction(),
        FakeSemanticValidation(),
        FakeAdditionalVerification(),
    )


def test_guard_respects_existing_policy_and_exact_reference(tmp_path):
    with Store(tmp_path / "guard.db", clock=lambda: "2026-09-13T01:00:00Z") as store:
        _, keys = setup(store)
        assert structure_guarded(request(), ports(), store, keys).bundle.evidences
        assert not structure_guarded(
            request(), ports(policy=PolicyDecision.DENY), store, keys
        ).bundle.evidences
        assert not structure_guarded(request(), ports(), store, {}).bundle.evidences
        altered = keys["synthetic-source"].model_dump()
        altered["extraction_revision_id"] = "other-extraction"
        assert not structure_guarded(
            request(), ports(), store, {"synthetic-source": parse(IndexKey, altered)}
        ).bundle.evidences


def test_restriction_arriving_during_extraction_removes_entire_response(tmp_path):
    with Store(tmp_path / "guard.db", clock=lambda: "2026-09-13T01:00:00Z") as store:
        value, keys = setup(store)

        class RestrictDuringExtraction(FakeExtraction):
            def extract_claims(self, *, source):
                value["event_id"] = "restricted-during-call"
                value["aggregate_revision"] = 2
                value["payload"]["status"] = "RESTRICTED"
                store.consume(parse(Event, value))
                return ()

        result = structure_guarded(request(), ports(RestrictDuringExtraction()), store, keys)
        assert result.status == ProcessingStatus.PAUSED
        assert not result.bundle.evidences
        assert not result.bundle.claims
        assert result.bundle.limitations[0].code == "RESTRICTION_CONTEXT_CHANGED"


def test_enclosing_allowed_source_cannot_smuggle_a_restricted_artifact(tmp_path):
    with Store(tmp_path / "guard.db", clock=lambda: "2026-09-13T01:00:00Z") as store:
        _, keys = setup(store)
        restricted = event(status="RESTRICTED", event_id="restricted-event")
        restricted["aggregate_id"] = restricted["payload"]["source_id"] = "restricted-source"
        store.consume(parse(Event, restricted))
        allowed = request().sources[0]
        foreign_ref = SourceRef(
            source_id="restricted-source",
            source_version_id="restricted-version",
            source_kind="synthetic",
        )
        artifact = Artifact(**{**allowed.artifacts[0].model_dump(), "source_ref": foreign_ref})
        mixed = SourceInput(source_ref=allowed.source_ref, artifacts=(artifact,))
        result = structure_guarded(request(mixed), ports(), store, keys)
        assert not result.bundle.evidences
