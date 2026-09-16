from test_c01 import SOURCE, allowed_version, fixture, index_request, parse, released, store_at
from test_restriction_guard import ports
from tests.support.factories import request, source_with_text
from tests.support.fakes import FakeExtraction
from w3_knowledge.models import Artifact, SourceInput, SourceRef, ProcessingStatus


def source(text=None):
    value = fixture("version-available")["payload"]
    ref = SourceRef(
        source_id=SOURCE, source_version_id=value["source_version_id"], source_kind="job_posting"
    )
    original = source_with_text(text or value["evidence_spans"][0]["text_excerpt"]).artifacts[0]
    artifact = Artifact(**{**original.model_dump(), "source_ref": ref})
    return SourceInput(source_ref=ref, artifacts=(artifact,))


def test_c01_guard_accepts_uuid_and_rejects_unindexed_text(tmp_path):
    from w3_knowledge.c01.guard import structure_guarded
    from w3_knowledge.c01.contracts import IndexKey

    with store_at(tmp_path / "guard.db") as store:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        keys = {SOURCE: IndexKey.model_validate(store.status(SOURCE)["index_key"])}
        assert structure_guarded(request(source()), ports(), store, keys).bundle.evidences
        assert not structure_guarded(
            request(source("injected unrelated text")), ports(), store, keys
        ).bundle.evidences
        store.consume(parse(released(2, 1)))
        assert not structure_guarded(request(source()), ports(), store, keys).bundle.evidences


def test_c01_guard_discards_result_when_restriction_arrives_during_extraction(tmp_path):
    from w3_knowledge.c01.guard import structure_guarded
    from w3_knowledge.c01.contracts import IndexKey

    with store_at(tmp_path / "race.db") as store:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        keys = {SOURCE: IndexKey.model_validate(store.status(SOURCE)["index_key"])}

        class Restrict(FakeExtraction):
            def extract_claims(self, *, source):
                event = released(2, 1)
                event["payload"]["restriction_status"] = "active"
                store.consume(parse(event))
                return ()

        result = structure_guarded(request(source()), ports(Restrict()), store, keys)
        assert result.status == ProcessingStatus.PAUSED
        assert not result.bundle.evidences
