from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from epick_engine.source_collection.contracts import CompanyCandidate
from epick_engine.source_collection.service import (
    COMPANY_RESOLUTION_OPERATION,
    CompanyRecord,
    CompanyRegistry,
    CompanyRelationshipRecord,
    CompanyResolution,
    CompanyResolutionIdempotencyConflict,
    CompanyResolutionIdempotencyUnavailable,
    CompanyResolutionRequest,
    CompanyResolutionService,
    InvalidCompanyCursor,
    ResolutionStatus,
    SourceAttributionRecord,
    canonicalize_source_url,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_COMPANIES_FIXTURE = _PROJECT_ROOT / "tests/fixtures/synthetic_sources/companies.json"
_COMPANY_A_ID = UUID("00000000-0000-4000-8000-000000000101")
_COMPANY_B_ID = UUID("00000000-0000-4000-8000-000000000102")
_RESEARCH_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000103")


@dataclass
class _WriteSpy:
    company_creations: int = 0
    company_merges: int = 0
    relationship_creations: int = 0
    source_transfers: int = 0
    posting_copies: int = 0
    requirement_copies: int = 0

    def create_company(self, *_args: object, **_kwargs: object) -> None:
        self.company_creations += 1

    def merge_companies(self, *_args: object, **_kwargs: object) -> None:
        self.company_merges += 1

    def create_relationship(self, *_args: object, **_kwargs: object) -> None:
        self.relationship_creations += 1

    def transfer_source(self, *_args: object, **_kwargs: object) -> None:
        self.source_transfers += 1

    def copy_posting(self, *_args: object, **_kwargs: object) -> None:
        self.posting_copies += 1

    def copy_requirements(self, *_args: object, **_kwargs: object) -> None:
        self.requirement_copies += 1

    def assert_not_written(self) -> None:
        assert self.company_creations == 0
        assert self.company_merges == 0
        assert self.relationship_creations == 0
        assert self.source_transfers == 0
        assert self.posting_copies == 0
        assert self.requirement_copies == 0


@dataclass
class _InMemoryIdempotencyPort:
    records: dict[
        tuple[UUID, str, str],
        tuple[str, CompanyResolution],
    ]
    create_calls: int = 0

    def resolve(
        self,
        *,
        owner_user_id: UUID,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        create: Callable[[], CompanyResolution],
    ) -> CompanyResolution:
        key = (owner_user_id, operation, idempotency_key)
        existing = self.records.get(key)
        if existing is not None:
            existing_hash, result = existing
            if existing_hash != request_hash:
                raise CompanyResolutionIdempotencyConflict(
                    "idempotency key was reused with different input"
                )
            return result
        result = create()
        self.records[key] = (request_hash, result)
        self.create_calls += 1
        return result


def _records(value: object) -> list[dict[str, object]]:
    return [cast(dict[str, object], item) for item in cast(list[object], value)]


def _strings(value: object) -> tuple[str, ...]:
    return tuple(cast(str, item) for item in cast(list[object], value))


def _registry_from_fixture() -> CompanyRegistry:
    fixture = cast(dict[str, object], json.loads(_COMPANIES_FIXTURE.read_text(encoding="utf-8")))
    companies = tuple(
        CompanyRecord(
            company_id=UUID(cast(str, company["company_id"])),
            legal_name=cast(str, company["legal_name"]),
            aliases=_strings(company["aliases"]),
            official_domains=_strings(company["official_domains"]),
            legal_identifiers=cast(dict[str, object], company["legal_identifiers"]),
            identity_status=cast(str, company["identity_status"]),
            identity_evidence=_strings(company["identity_evidence"]),
        )
        for company in _records(fixture["companies"])
    )
    relationships = tuple(
        CompanyRelationshipRecord(
            relationship_id=UUID(cast(str, relationship["relationship_id"])),
            company_id=UUID(cast(str, relationship["company_id"])),
            related_company_id=UUID(cast(str, relationship["related_company_id"])),
            kind=cast(str, relationship["kind"]),
            evidence_ref=cast(str, relationship["evidence_ref"]),
            valid_from=relationship["valid_from"],
            valid_to=relationship["valid_to"],
        )
        for relationship in _records(fixture["relationships"])
    )
    source_attributions = tuple(
        SourceAttributionRecord(
            source_id=UUID(cast(str, attribution["source_id"])),
            source_ref=cast(str, attribution["source_ref"]),
            company_id=(
                UUID(cast(str, attribution["company_id"]))
                if attribution["company_id"] is not None
                else None
            ),
            url=cast(str, attribution["url"]),
            attribution_evidence_refs=_strings(attribution["attribution_evidence_refs"]),
            shared_careers_host=cast(str, attribution["shared_careers_host"]),
            automatic_transfer_permitted=cast(
                bool,
                attribution["automatic_transfer_permitted"],
            ),
            attribution_status=cast(str | None, attribution.get("attribution_status")),
        )
        for attribution in _records(fixture["source_attributions"])
    )
    return CompanyRegistry(
        companies=companies,
        relationships=relationships,
        source_attributions=source_attributions,
    )


def _service(registry: CompanyRegistry, writes: _WriteSpy) -> CompanyResolutionService:
    return CompanyResolutionService(registry=registry, write_sink=writes)


def _idempotent_service(
    registry: CompanyRegistry,
    writes: _WriteSpy,
    port: _InMemoryIdempotencyPort,
) -> CompanyResolutionService:
    return CompanyResolutionService(
        registry=registry,
        write_sink=writes,
        idempotency_port=port,
    )


def test_explicit_identity_evidence_selects_only_matching_legal_entity_and_preserves_evidence() -> (
    None
):
    registry = _registry_from_fixture()
    writes = _WriteSpy()
    supplied_evidence = "https://synthetic-meridian-b.test/legal/entity"

    resolution = _service(registry, writes).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(supplied_evidence,),
        )
    )

    assert resolution.status is ResolutionStatus.RESOLVED
    assert resolution.company_id == _COMPANY_B_ID
    assert resolution.matched_identity_evidence_refs == (supplied_evidence,)
    writes.assert_not_written()


def test_name_only_match_requires_selection_without_creating_a_company() -> None:
    registry = _registry_from_fixture()
    writes = _WriteSpy()

    resolution = _service(registry, writes).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
        )
    )

    assert resolution.status is ResolutionStatus.SELECTION_REQUIRED
    assert resolution.company_id is None
    assert {candidate.company_id for candidate in resolution.candidates} == {
        _COMPANY_A_ID,
        _COMPANY_B_ID,
    }
    assert all(isinstance(candidate, CompanyCandidate) for candidate in resolution.candidates)
    writes.assert_not_written()


def test_shared_careers_host_does_not_merge_companies_or_transfer_a_source() -> None:
    registry = _registry_from_fixture()
    writes = _WriteSpy()

    resolution = _service(registry, writes).resolve(
        CompanyResolutionRequest(
            legal_name=None,
            source_url="https://synthetic-meridian-careers.test/jobs/static-posting",
            identity_evidence_refs=("https://synthetic-meridian-careers.test/jobs/static-posting",),
        )
    )

    assert resolution.status is ResolutionStatus.SELECTION_REQUIRED
    assert resolution.company_id is None
    assert {candidate.company_id for candidate in resolution.candidates} == {
        _COMPANY_A_ID,
        _RESEARCH_COMPANY_ID,
    }
    writes.assert_not_written()


def test_unverified_product_unit_does_not_create_a_company() -> None:
    registry = _registry_from_fixture()
    writes = _WriteSpy()

    resolution = _service(registry, writes).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Product Unit",
            source_url="https://synthetic-meridian-careers.test/jobs/unverified-unit",
            identity_evidence_refs=(),
        )
    )

    assert resolution.status is ResolutionStatus.UNVERIFIED
    assert resolution.company_id is None
    assert resolution.candidates == ()
    writes.assert_not_written()


def test_malformed_evidence_url_does_not_escape_as_a_dependency_failure() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name=None,
            source_url=None,
            identity_evidence_refs=("https://a..b/legal",),
        )
    )

    assert result.status is ResolutionStatus.UNVERIFIED
    assert result.company_id is None
    assert result.candidates == ()


def test_publicly_evidenced_subsidiary_relationship_is_queryable_without_copying_sibling_data() -> (
    None
):
    registry = _registry_from_fixture()
    writes = _WriteSpy()

    relationships = _service(registry, writes).relationships_for(_RESEARCH_COMPANY_ID)

    assert relationships == registry.relationships
    assert relationships[0].company_id == _RESEARCH_COMPANY_ID
    assert relationships[0].related_company_id == _COMPANY_A_ID
    assert relationships[0].kind == "subsidiary_of"
    assert relationships[0].evidence_ref.startswith("https://")
    writes.assert_not_written()


def test_relationship_without_public_evidence_is_not_returned() -> None:
    registry = _registry_from_fixture()
    registry_without_public_relationship_evidence = replace(
        registry,
        relationships=(replace(registry.relationships[0], evidence_ref=""),),
    )
    writes = _WriteSpy()

    relationships = _service(
        registry_without_public_relationship_evidence, writes
    ).relationships_for(_RESEARCH_COMPANY_ID)

    assert relationships == ()
    writes.assert_not_written()


def test_resolution_uses_only_the_in_memory_registry_without_external_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_network_is_used(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("company resolution must not perform external lookup or fetch")

    monkeypatch.setattr(socket, "getaddrinfo", fail_if_network_is_used)

    resolution = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
        )
    )

    assert resolution.status is ResolutionStatus.SELECTION_REQUIRED


def test_canonicalization_normalizes_host_and_removes_fragment_without_losing_path_or_query() -> (
    None
):
    canonical_url = canonicalize_source_url(
        "HTTPS://SYNTHETIC-MERIDIAN-A.TEST/jobs/platform-engineer-a?department=platform&ref=42#details"
    )

    assert canonical_url == (
        "https://synthetic-meridian-a.test/jobs/platform-engineer-a?department=platform&ref=42"
    )


def test_canonicalization_keeps_distinct_query_values_as_distinct_sources() -> None:
    first_source = canonicalize_source_url(
        "https://synthetic-meridian-a.test/jobs/platform-engineer-a?opening=101"
    )
    second_source = canonicalize_source_url(
        "https://synthetic-meridian-a.test/jobs/platform-engineer-a?opening=102"
    )

    assert first_source != second_source


def test_canonicalization_preserves_ipv6_host_brackets() -> None:
    canonical_url = canonicalize_source_url(
        "https://[2001:db8::1]:443/jobs/platform-engineer?opening=101#apply"
    )

    assert canonical_url == "https://[2001:db8::1]/jobs/platform-engineer?opening=101"


def test_canonicalization_normalizes_an_empty_root_path() -> None:
    assert canonicalize_source_url("https://synthetic-meridian-a.test") == (
        "https://synthetic-meridian-a.test/"
    )
    assert canonicalize_source_url("https://synthetic-meridian-a.test/") == (
        "https://synthetic-meridian-a.test/"
    )


def test_canonicalization_rejects_url_credentials() -> None:
    with pytest.raises(ValueError, match="credentials are not allowed"):
        canonicalize_source_url(
            "https://synthetic-user:synthetic-password@synthetic-meridian-a.test/jobs"
        )


def test_canonicalization_rejects_an_invalid_dns_label() -> None:
    with pytest.raises(ValueError, match="domain is not valid"):
        canonicalize_source_url(f"https://{'a' * 64}.test/jobs")


def test_resolution_request_snapshots_mutable_sequence_inputs() -> None:
    supplied_evidence = ["https://synthetic-meridian-a.test/legal/entity"]
    supplied_identifiers = ["SMA-2026"]
    request = CompanyResolutionRequest(
        legal_name="Synthetic Meridian Labs Ltd.",
        source_url=None,
        identity_evidence_refs=cast(tuple[str, ...], supplied_evidence),
        official_identifiers=cast(tuple[str, ...], supplied_identifiers),
    )

    supplied_evidence.clear()
    supplied_identifiers.clear()

    assert request.identity_evidence_refs == ("https://synthetic-meridian-a.test/legal/entity",)
    assert request.official_identifiers == ("SMA-2026",)


def test_registry_search_returns_same_name_candidates_with_identity_evidence() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).search_companies(
        owner_user_id=_COMPANY_A_ID,
        query="Synthetic Meridian Labs",
        official_domain=None,
        cursor=None,
        limit=10,
    )

    assert result["selection_required"] is True
    assert result["candidates"] == [
        {
            "company_id": str(_COMPANY_A_ID),
            "legal_name": "Synthetic Meridian Labs Ltd.",
            "identity_evidence_refs": ["https://synthetic-meridian-a.test/legal/entity"],
        },
        {
            "company_id": str(_COMPANY_B_ID),
            "legal_name": "Synthetic Meridian Labs Ltd.",
            "identity_evidence_refs": ["https://synthetic-meridian-b.test/legal/entity"],
        },
    ]


def test_official_domain_filters_search_without_resolving_or_merging() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).search_companies(
        owner_user_id=_COMPANY_A_ID,
        query="Synthetic Meridian Labs",
        official_domain="SYNTHETIC-MERIDIAN-B.TEST.",
        cursor=None,
        limit=10,
    )

    assert result["selection_required"] is False
    assert [
        candidate["company_id"] for candidate in cast(list[dict[str, str]], result["candidates"])
    ] == [str(_COMPANY_B_ID)]


def test_registry_search_paginates_in_stable_registry_order() -> None:
    service = _service(_registry_from_fixture(), _WriteSpy())
    first = service.search_companies(
        owner_user_id=_COMPANY_A_ID,
        query="Synthetic Meridian Labs",
        official_domain=None,
        cursor=None,
        limit=1,
    )
    second = service.search_companies(
        owner_user_id=_COMPANY_A_ID,
        query="Synthetic Meridian Labs",
        official_domain=None,
        cursor=first["next_cursor"],
        limit=1,
    )

    assert [
        candidate["company_id"] for candidate in cast(list[dict[str, str]], first["candidates"])
    ] == [str(_COMPANY_A_ID)]
    assert first["next_cursor"] == 1
    assert [
        candidate["company_id"] for candidate in cast(list[dict[str, str]], second["candidates"])
    ] == [str(_COMPANY_B_ID)]
    assert second["next_cursor"] is None


def test_registry_search_rejects_invalid_decoded_cursor_position() -> None:
    with pytest.raises(InvalidCompanyCursor):
        _service(_registry_from_fixture(), _WriteSpy()).search_companies(
            owner_user_id=_COMPANY_A_ID,
            query="Synthetic Meridian Labs",
            official_domain=None,
            cursor=True,
            limit=1,
        )


def test_company_detail_includes_only_evidenced_relationships() -> None:
    detail = _service(_registry_from_fixture(), _WriteSpy()).get_company(
        owner_user_id=_COMPANY_A_ID,
        company_id=_RESEARCH_COMPANY_ID,
    )

    assert cast(dict[str, object], detail["company"])["company_id"] == str(_RESEARCH_COMPANY_ID)
    assert detail["relationships"] == [
        {
            "relationship_id": "00000000-0000-4000-8000-000000000201",
            "company_id": str(_RESEARCH_COMPANY_ID),
            "related_company_id": str(_COMPANY_A_ID),
            "kind": "subsidiary_of",
            "evidence_ref": ("https://synthetic-meridian-research.test/legal/group-relationship"),
            "valid_from": None,
            "valid_to": None,
        }
    ]
    assert "source_attributions" not in detail


def test_confirmed_legal_identifier_resolves_one_same_name_company() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
            official_identifiers=("SML-B-002",),
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.company_id == _COMPANY_B_ID


def test_selected_company_must_be_a_known_candidate() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
            selected_company_id=_RESEARCH_COMPANY_ID,
        )
    )

    assert result.status is ResolutionStatus.SELECTION_REQUIRED
    assert result.company_id is None
    assert {candidate.company_id for candidate in result.candidates} == {
        _COMPANY_A_ID,
        _COMPANY_B_ID,
    }


def test_selected_known_candidate_uses_its_verified_registry_evidence() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
            selected_company_id=_COMPANY_B_ID,
        )
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.company_id == _COMPANY_B_ID
    assert result.matched_identity_evidence_refs == (
        "https://synthetic-meridian-b.test/legal/entity",
    )


def test_selected_company_does_not_override_an_unrelated_legal_name() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Unrelated Example Co.",
            source_url=None,
            identity_evidence_refs=(),
            selected_company_id=_COMPANY_A_ID,
        )
    )

    assert result.status is ResolutionStatus.UNVERIFIED
    assert result.company_id is None
    assert result.candidates == ()


def test_invalid_selected_company_is_not_ignored_when_other_evidence_matches() -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=("https://synthetic-meridian-b.test/legal/entity",),
            selected_company_id=_RESEARCH_COMPANY_ID,
        )
    )

    assert result.status is ResolutionStatus.SELECTION_REQUIRED
    assert result.company_id is None
    assert [candidate.company_id for candidate in result.candidates] == [_COMPANY_B_ID]


@pytest.mark.parametrize(
    "non_identifier",
    ["synthetic-registry", "synthetic-jurisdiction-b"],
)
def test_registry_metadata_is_not_treated_as_a_confirmed_legal_identifier(
    non_identifier: str,
) -> None:
    result = _service(_registry_from_fixture(), _WriteSpy()).resolve(
        CompanyResolutionRequest(
            legal_name="Synthetic Meridian Labs Ltd.",
            source_url=None,
            identity_evidence_refs=(),
            official_identifiers=(non_identifier,),
        )
    )

    assert result.status is ResolutionStatus.SELECTION_REQUIRED
    assert result.company_id is None


def test_api_resolution_preserves_shared_domain_candidates_without_merging() -> None:
    port = _InMemoryIdempotencyPort(records={})
    service = _idempotent_service(_registry_from_fixture(), _WriteSpy(), port)

    result = service.resolve_company(
        owner_user_id=_COMPANY_A_ID,
        name=None,
        official_identifiers=None,
        selected_company_id=None,
        identity_evidence_refs=["https://synthetic-meridian-careers.test/jobs/static-posting"],
        idempotency_key="shared-domain",
    )

    assert result["resolution"] == ResolutionStatus.SELECTION_REQUIRED
    assert result["company_id"] is None
    assert [
        candidate["company_id"] for candidate in cast(list[dict[str, str]], result["candidates"])
    ] == [str(_COMPANY_A_ID), str(_RESEARCH_COMPANY_ID)]


def test_idempotent_resolution_replays_first_result_for_same_owner_and_input() -> None:
    port = _InMemoryIdempotencyPort(records={})
    service = _idempotent_service(_registry_from_fixture(), _WriteSpy(), port)
    request = CompanyResolutionRequest(
        legal_name="Synthetic Meridian Labs Ltd.",
        source_url=None,
        identity_evidence_refs=(),
    )

    first = service.resolve_idempotently(
        owner_user_id=_COMPANY_A_ID,
        operation=COMPANY_RESOLUTION_OPERATION,
        idempotency_key="same-click",
        request=request,
    )
    replay = service.resolve_idempotently(
        owner_user_id=_COMPANY_A_ID,
        operation=COMPANY_RESOLUTION_OPERATION,
        idempotency_key="same-click",
        request=request,
    )

    assert replay is first
    assert port.create_calls == 1


def test_idempotent_resolution_rejects_changed_input_for_same_owner_key() -> None:
    port = _InMemoryIdempotencyPort(records={})
    service = _idempotent_service(_registry_from_fixture(), _WriteSpy(), port)
    first = CompanyResolutionRequest(
        legal_name="Synthetic Meridian Labs Ltd.",
        source_url=None,
        identity_evidence_refs=(),
    )
    changed = replace(first, legal_name="Synthetic Meridian Research Ltd.")

    service.resolve_idempotently(
        owner_user_id=_COMPANY_A_ID,
        operation=COMPANY_RESOLUTION_OPERATION,
        idempotency_key="changed-input",
        request=first,
    )

    with pytest.raises(CompanyResolutionIdempotencyConflict):
        service.resolve_idempotently(
            owner_user_id=_COMPANY_A_ID,
            operation=COMPANY_RESOLUTION_OPERATION,
            idempotency_key="changed-input",
            request=changed,
        )


def test_same_idempotency_key_is_independent_between_owners() -> None:
    port = _InMemoryIdempotencyPort(records={})
    service = _idempotent_service(_registry_from_fixture(), _WriteSpy(), port)
    request = CompanyResolutionRequest(
        legal_name="Synthetic Meridian Labs Ltd.",
        source_url=None,
        identity_evidence_refs=(),
    )

    service.resolve_idempotently(
        owner_user_id=_COMPANY_A_ID,
        operation=COMPANY_RESOLUTION_OPERATION,
        idempotency_key="owner-scoped",
        request=request,
    )
    service.resolve_idempotently(
        owner_user_id=_COMPANY_B_ID,
        operation=COMPANY_RESOLUTION_OPERATION,
        idempotency_key="owner-scoped",
        request=request,
    )

    assert port.create_calls == 2


def test_idempotent_resolution_fails_closed_without_atomic_port() -> None:
    service = _service(_registry_from_fixture(), _WriteSpy())

    with pytest.raises(CompanyResolutionIdempotencyUnavailable):
        service.resolve_idempotently(
            owner_user_id=_COMPANY_A_ID,
            operation=COMPANY_RESOLUTION_OPERATION,
            idempotency_key="missing-port",
            request=CompanyResolutionRequest(
                legal_name="Synthetic Meridian Labs Ltd.",
                source_url=None,
                identity_evidence_refs=(),
            ),
        )
