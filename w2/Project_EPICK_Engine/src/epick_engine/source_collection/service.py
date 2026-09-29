"""Known-registry company resolution and static source-collection orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import AnyUrl

from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    AcquisitionStatus,
    AlternativeSourceReason,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompanyCandidate,
    CompletionKind,
    CoreFailureDecisionAction,
    CoreFailureDecisionContext,
    DateStatus,
    DateValue,
    Evidence,
    ExtractionStatus,
    Failure,
    FindAlternativeSourceAction,
    FindAlternativeSourceContext,
    FreshnessStatus,
    Permission,
    Policy,
    PostingSection,
    RequiredAction,
    RetentionScope,
    SourceEnvelope,
    SourceEvent,
    SourceEventType,
    SourceMetadata,
    SourceObservationSnapshot,
    SourceReference,
    SourceType,
    UserRetryAction,
    UserRetryContext,
)
from epick_engine.source_collection.contracts import (
    Representation as SourceRepresentation,
)
from epick_engine.source_collection.parsing import (
    HASH_PROFILE_VERSION,
    EvidenceDraft,
    ExtractedSectionDraft,
    StaticParseResult,
    StaticPostingParseResult,
    compute_content_hash,
    extract_static_candidate,
    parse_approved_static_posting,
)
from epick_engine.source_collection.persistence import (
    PreparedCollectionCommit,
    PreparedEvidence,
    PreparedExtractionRevision,
    PreparedParserExecution,
    PreparedSourceObservation,
    PreparedSourceVersion,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    PolicyBlocked,
    PolicyOperation,
    PolicySnapshot,
    authorize_operation,
)
from epick_engine.source_collection.policy import (
    Representation as StaticRepresentation,
)

OUTPUT_HASH_PROFILE_VERSION = "epick-parser-output-sha256-v1"
JOB_POSTING_OUTPUT_HASH_PROFILE_VERSION = "epick-job-posting-parser-output-sha256-v2"
_MAX_PREPARED_EVIDENCE = 10_000
_MAX_PREPARED_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_PREPARED_SECTION_EVIDENCE_REFS = 20_000
_MAX_PARSER_LIMITATIONS = 128
_MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60

COMPANY_RESOLUTION_INPUT_VERSION = 1
COMPANY_RESOLUTION_OPERATION = "POST /api/v1/companies/resolve"


class ResolutionStatus(StrEnum):
    """Public outcomes for a company-resolution request."""

    RESOLVED = "resolved"
    SELECTION_REQUIRED = "selection_required"
    UNVERIFIED = "unverified"


class CompanyNotFound(LookupError):
    """Raised when a requested known-registry company does not exist."""


class InvalidCompanyCursor(ValueError):
    """Raised when a decoded company-search position is not usable."""


class CompanyResolutionIdempotencyConflict(RuntimeError):
    """Raised when one owner reuses a resolution key with different input."""


class CompanyResolutionIdempotencyUnavailable(RuntimeError):
    """Raised when an idempotent resolution is attempted without an atomic port."""


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    company_id: UUID
    legal_name: str
    aliases: tuple[str, ...]
    official_domains: tuple[str, ...]
    legal_identifiers: Mapping[str, object]
    identity_status: str
    identity_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompanyRelationshipRecord:
    relationship_id: UUID
    company_id: UUID
    related_company_id: UUID
    kind: str
    evidence_ref: str
    valid_from: object | None
    valid_to: object | None


@dataclass(frozen=True, slots=True)
class SourceAttributionRecord:
    source_id: UUID
    source_ref: str
    company_id: UUID | None
    url: str
    attribution_evidence_refs: tuple[str, ...]
    shared_careers_host: str
    automatic_transfer_permitted: bool
    attribution_status: str | None = None


@dataclass(frozen=True, slots=True)
class CompanyRegistry:
    companies: tuple[CompanyRecord, ...]
    relationships: tuple[CompanyRelationshipRecord, ...]
    source_attributions: tuple[SourceAttributionRecord, ...]


@dataclass(frozen=True, slots=True)
class CompanyResolutionRequest:
    legal_name: str | None
    source_url: str | None
    identity_evidence_refs: tuple[str, ...]
    official_identifiers: tuple[str, ...] = ()
    selected_company_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "identity_evidence_refs",
            tuple(self.identity_evidence_refs),
        )
        object.__setattr__(
            self,
            "official_identifiers",
            tuple(self.official_identifiers),
        )


@dataclass(frozen=True, slots=True)
class CompanyResolution:
    status: ResolutionStatus
    company_id: UUID | None
    candidates: tuple[CompanyCandidate, ...]
    matched_identity_evidence_refs: tuple[str, ...] = ()


class CompanyResolutionIdempotencyPort(Protocol):
    """Atomic owner-scoped replay boundary supplied by the application owner."""

    def resolve(
        self,
        *,
        owner_user_id: UUID,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        create: Callable[[], CompanyResolution],
    ) -> CompanyResolution: ...


class CompanyResolutionService:
    """Resolve only companies already present in the injected trusted registry."""

    def __init__(
        self,
        *,
        registry: CompanyRegistry,
        write_sink: object,
        idempotency_port: CompanyResolutionIdempotencyPort | None = None,
    ) -> None:
        self._registry = registry
        self._write_sink = write_sink
        self._idempotency_port = idempotency_port

    def resolve(self, request: CompanyResolutionRequest) -> CompanyResolution:
        """Resolve confirmed evidence, otherwise preserve candidate ambiguity."""

        candidates = self._candidate_records(request)
        candidate_ids = {candidate.company_id for candidate in candidates}

        if request.selected_company_id is not None:
            selected = self._company_by_id(request.selected_company_id)
            selection_matches_request = self._selection_matches_request(
                selected,
                request,
                candidate_ids,
            )
            if (
                selected is not None
                and selected.identity_status == "verified"
                and selected.identity_evidence
                and selection_matches_request
            ):
                matched = self._matching_evidence(selected, request.identity_evidence_refs)
                return self._resolved(selected, matched or selected.identity_evidence)
            if candidates:
                return CompanyResolution(
                    status=ResolutionStatus.SELECTION_REQUIRED,
                    company_id=None,
                    candidates=tuple(self._candidate(company) for company in candidates),
                )
            return CompanyResolution(
                status=ResolutionStatus.UNVERIFIED,
                company_id=None,
                candidates=(),
            )

        evidence_matches = self._confirmed_evidence_matches(
            request.identity_evidence_refs,
            candidate_ids,
        )
        if len(evidence_matches) == 1:
            company = evidence_matches[0]
            return self._resolved(
                company,
                self._matching_evidence(company, request.identity_evidence_refs),
            )

        identifier_matches = self._confirmed_identifier_matches(
            request.official_identifiers,
            candidate_ids,
        )
        if len(identifier_matches) == 1:
            return self._resolved(identifier_matches[0], ())

        ambiguous = candidates
        if not ambiguous:
            ambiguous = self._deduplicate_companies((*evidence_matches, *identifier_matches))
        if ambiguous:
            return CompanyResolution(
                status=ResolutionStatus.SELECTION_REQUIRED,
                company_id=None,
                candidates=tuple(self._candidate(company) for company in ambiguous),
            )
        return CompanyResolution(
            status=ResolutionStatus.UNVERIFIED,
            company_id=None,
            candidates=(),
        )

    def resolve_idempotently(
        self,
        *,
        owner_user_id: UUID,
        operation: str,
        idempotency_key: str,
        request: CompanyResolutionRequest,
    ) -> CompanyResolution:
        """Resolve through an injected atomic replay port; never use local cache state."""

        if self._idempotency_port is None:
            raise CompanyResolutionIdempotencyUnavailable(
                "company resolution idempotency port is required"
            )
        if not operation.strip() or not idempotency_key.strip():
            raise ValueError("operation and idempotency_key are required")
        request_hash = _resolution_request_hash(request)
        return self._idempotency_port.resolve(
            owner_user_id=owner_user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            create=lambda: self.resolve(request),
        )

    def resolve_company(
        self,
        *,
        owner_user_id: UUID,
        name: str | None,
        official_identifiers: list[str] | None,
        selected_company_id: UUID | None,
        identity_evidence_refs: list[str] | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        """API-facing adapter for owner-scoped idempotent company resolution."""

        result = self.resolve_idempotently(
            owner_user_id=owner_user_id,
            operation=COMPANY_RESOLUTION_OPERATION,
            idempotency_key=idempotency_key,
            request=CompanyResolutionRequest(
                legal_name=name,
                source_url=None,
                identity_evidence_refs=tuple(identity_evidence_refs or ()),
                official_identifiers=tuple(official_identifiers or ()),
                selected_company_id=selected_company_id,
            ),
        )
        return {
            "resolution": result.status.value,
            "company_id": str(result.company_id) if result.company_id is not None else None,
            "candidates": [candidate.model_dump(mode="json") for candidate in result.candidates],
        }

    def search_companies(
        self,
        *,
        owner_user_id: UUID,
        query: str,
        official_domain: str | None,
        cursor: object | None,
        limit: int,
    ) -> dict[str, object]:
        """Search only the injected registry; cursor validation belongs to the API."""

        del owner_user_id
        normalized_query = query.strip().casefold()
        if not normalized_query:
            raise ValueError("query is required")
        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be positive")
        normalized_domain = (
            normalize_company_domain(official_domain) if official_domain is not None else None
        )
        matches = [
            company
            for company in self._registry.companies
            if self._matches_query(company, normalized_query)
            and (
                normalized_domain is None
                or normalized_domain
                in {normalize_company_domain(domain) for domain in company.official_domains}
            )
        ]
        offset = _company_cursor_offset(cursor)
        page = matches[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "candidates": [self._candidate(company).model_dump(mode="json") for company in page],
            "selection_required": len(matches) > 1,
            "next_cursor": next_offset if next_offset < len(matches) else None,
        }

    def get_company(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
    ) -> dict[str, object]:
        """Return public known-registry identity and evidenced relationships only."""

        del owner_user_id
        company = self._company_by_id(company_id)
        if company is None:
            raise CompanyNotFound("company was not found")
        return {
            "company": {
                "company_id": str(company.company_id),
                "legal_name": company.legal_name,
                "aliases": list(company.aliases),
                "official_domains": list(company.official_domains),
                "legal_identifiers": dict(company.legal_identifiers),
                "identity_status": company.identity_status,
                "identity_evidence": list(company.identity_evidence),
            },
            "relationships": [
                {
                    "relationship_id": str(relationship.relationship_id),
                    "company_id": str(relationship.company_id),
                    "related_company_id": str(relationship.related_company_id),
                    "kind": relationship.kind,
                    "evidence_ref": relationship.evidence_ref,
                    "valid_from": relationship.valid_from,
                    "valid_to": relationship.valid_to,
                }
                for relationship in self.relationships_for(company_id)
            ],
        }

    def relationships_for(
        self,
        company_id: UUID,
    ) -> tuple[CompanyRelationshipRecord, ...]:
        return tuple(
            relationship
            for relationship in self._registry.relationships
            if relationship.evidence_ref.strip()
            and company_id in {relationship.company_id, relationship.related_company_id}
        )

    def _candidate_records(
        self,
        request: CompanyResolutionRequest,
    ) -> tuple[CompanyRecord, ...]:
        by_name = (
            tuple(
                company
                for company in self._registry.companies
                if self._matches_query(company, request.legal_name.strip().casefold())
            )
            if request.legal_name and request.legal_name.strip()
            else ()
        )
        by_source = self._companies_for_source(request.source_url)
        if not by_source and request.source_url is None:
            by_source = self._deduplicate_companies(
                tuple(
                    company
                    for reference in request.identity_evidence_refs
                    for company in self._companies_for_source(reference)
                )
            )
        if request.legal_name and request.legal_name.strip():
            if not by_name:
                return ()
            if not by_source:
                return by_name
            source_ids = {company.company_id for company in by_source}
            overlap = tuple(company for company in by_name if company.company_id in source_ids)
            return overlap or by_name
        return by_source

    def _companies_for_source(self, source_url: str | None) -> tuple[CompanyRecord, ...]:
        if not source_url:
            return ()
        try:
            hostname = urlsplit(source_url).hostname
            if hostname is None:
                return ()
            normalized = normalize_company_domain(hostname)
        except ValueError:
            return ()
        return tuple(
            company
            for company in self._registry.companies
            if normalized
            in {normalize_company_domain(domain) for domain in company.official_domains}
        )

    def _confirmed_evidence_matches(
        self,
        evidence_refs: tuple[str, ...],
        candidate_ids: set[UUID],
    ) -> tuple[CompanyRecord, ...]:
        supplied = {reference for reference in evidence_refs if reference}
        if not supplied:
            return ()
        return tuple(
            company
            for company in self._registry.companies
            if company.identity_status == "verified"
            and (not candidate_ids or company.company_id in candidate_ids)
            and supplied.intersection(company.identity_evidence)
        )

    def _confirmed_identifier_matches(
        self,
        identifiers: tuple[str, ...],
        candidate_ids: set[UUID],
    ) -> tuple[CompanyRecord, ...]:
        supplied = {
            identifier.strip().casefold() for identifier in identifiers if identifier.strip()
        }
        if not supplied:
            return ()
        return tuple(
            company
            for company in self._registry.companies
            if company.identity_status == "verified"
            and (not candidate_ids or company.company_id in candidate_ids)
            and supplied.intersection(_identifier_values(company.legal_identifiers))
        )

    def _company_by_id(self, company_id: UUID) -> CompanyRecord | None:
        return next(
            (company for company in self._registry.companies if company.company_id == company_id),
            None,
        )

    def _selection_matches_request(
        self,
        selected: CompanyRecord | None,
        request: CompanyResolutionRequest,
        candidate_ids: set[UUID],
    ) -> bool:
        if selected is None:
            return False
        if candidate_ids:
            return selected.company_id in candidate_ids
        if request.legal_name and request.legal_name.strip():
            return False
        if request.source_url:
            return False
        if self._matching_evidence(selected, request.identity_evidence_refs):
            return True
        supplied_identifiers = {
            identifier.strip().casefold()
            for identifier in request.official_identifiers
            if identifier.strip()
        }
        if supplied_identifiers:
            return bool(
                supplied_identifiers.intersection(_identifier_values(selected.legal_identifiers))
            )
        return not request.identity_evidence_refs

    @staticmethod
    def _matches_query(company: CompanyRecord, normalized_query: str) -> bool:
        return any(
            normalized_query in name.casefold() for name in (company.legal_name, *company.aliases)
        )

    @staticmethod
    def _candidate(company: CompanyRecord) -> CompanyCandidate:
        return CompanyCandidate(
            company_id=company.company_id,
            legal_name=company.legal_name,
            identity_evidence_refs=list(company.identity_evidence),
        )

    def _resolved(
        self,
        company: CompanyRecord,
        evidence_refs: tuple[str, ...],
    ) -> CompanyResolution:
        return CompanyResolution(
            status=ResolutionStatus.RESOLVED,
            company_id=company.company_id,
            candidates=(),
            matched_identity_evidence_refs=evidence_refs,
        )

    @staticmethod
    def _matching_evidence(
        company: CompanyRecord,
        supplied: tuple[str, ...],
    ) -> tuple[str, ...]:
        known = set(company.identity_evidence)
        return tuple(reference for reference in supplied if reference in known)

    @staticmethod
    def _deduplicate_companies(
        companies: tuple[CompanyRecord, ...],
    ) -> tuple[CompanyRecord, ...]:
        seen: set[UUID] = set()
        result: list[CompanyRecord] = []
        for company in companies:
            if company.company_id not in seen:
                seen.add(company.company_id)
                result.append(company)
        return tuple(result)


def canonicalize_source_url(url: str) -> str:
    """Normalize an approved HTTPS URL without discarding meaningful path or query."""

    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid source URL") from exc
    if parts.scheme.casefold() != "https" or hostname is None:
        raise ValueError("source URL must be absolute HTTPS")
    if parts.username is not None or parts.password is not None:
        raise ValueError("source URL credentials are not allowed")
    normalized_hostname = normalize_company_domain(hostname)
    effective_port = 443 if port is None else port
    if effective_port <= 0:
        raise ValueError("source URL port must be positive")
    netloc_host = f"[{normalized_hostname}]" if ":" in normalized_hostname else normalized_hostname
    netloc = netloc_host if effective_port == 443 else f"{netloc_host}:{effective_port}"
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


def normalize_company_domain(domain: str) -> str:
    value = domain.strip().rstrip(".")
    if not value:
        raise ValueError("domain is required")
    try:
        return ip_address(value).compressed.casefold()
    except ValueError:
        pass
    try:
        normalized = value.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("domain is not valid IDNA") from exc
    labels = normalized.split(".")
    if len(normalized) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not label.replace("-", "").isalnum()
        or not label.isascii()
        for label in labels
    ):
        raise ValueError("domain is not a valid hostname")
    return normalized


def _identifier_values(identifiers: Mapping[str, object]) -> set[str]:
    value = identifiers.get("value")
    if not isinstance(value, str) or not value.strip():
        return set()
    return {value.strip().casefold()}


def _company_cursor_offset(cursor: object | None) -> int:
    if cursor is None:
        return 0
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise InvalidCompanyCursor("company cursor position must be a non-negative integer")
    return cursor


def _resolution_request_hash(request: CompanyResolutionRequest) -> str:
    payload: dict[str, Any] = {
        "input_version": COMPANY_RESOLUTION_INPUT_VERSION,
        "legal_name": request.legal_name,
        "source_url": request.source_url,
        "identity_evidence_refs": list(request.identity_evidence_refs),
        "official_identifiers": list(request.official_identifiers),
        "selected_company_id": (
            str(request.selected_company_id) if request.selected_company_id is not None else None
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class StaticCollectionInput:
    source_id: UUID
    company_id: UUID
    source_url: str
    title: str | None
    source_type: SourceType
    policy: Policy
    policy_revision: int
    robots_permission: Permission
    limits: ExecutionLimits
    result_version: int
    aggregate_revision: int
    language: str | None
    redirect_robots_permissions: tuple[tuple[str, Permission], ...] = ()


class CollectionInputProvider(Protocol):
    def load(self, command: CollectionCommand) -> StaticCollectionInput:
        """Load the immutable, worker-authorized input for one command."""
        ...


class _StaticCollector(Protocol):
    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        """Fetch one policy-authorized static response."""
        ...


class _StaticExecutionContext(Protocol):
    @property
    def command(self) -> CollectionCommand:
        """Return the effective command for the current stage permit."""
        ...

    def enter_stage(
        self,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> object:
        """Authorize a collection stage before its work starts."""


class StaticCollectionExecution:
    """Prepare one static collection commit; persistence belongs to the worker."""

    def __init__(
        self,
        attempt_id: UUID,
        input_provider: CollectionInputProvider,
        collector: _StaticCollector,
        parser: Callable[[StaticResponseCandidate], StaticParseResult] = extract_static_candidate,
        *,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._attempt_id = attempt_id
        self._input_provider = input_provider
        self._collector = collector
        self._parser = parser
        self._clock = clock
        self._uuid_factory = uuid_factory

    def run_once(self, context: _StaticExecutionContext) -> PreparedCollectionCommit:
        command = context.command
        input_value = self._input_provider.load(command)
        _validate_static_collection_input(command, input_value)
        now = self._now()

        if command.resume_stage not in {CollectionStage.POLICY, CollectionStage.FETCH}:
            raise ValueError("static collection only supports policy or fetch resume stages")
        _validate_effective_command(command, input_value)
        if (
            command.resume_stage is CollectionStage.FETCH
            and command.policy_revision != input_value.policy_revision
        ):
            raise ValueError("fetch resume policy revision does not match collection input")
        if command.resume_stage is CollectionStage.POLICY:
            context.enter_stage(
                CollectionStage.POLICY,
                policy_revision=input_value.policy_revision,
            )
            command = context.command
            _validate_effective_command(command, input_value)

        policy_snapshot = _policy_snapshot(input_value)
        try:
            authorize_operation(policy_snapshot, PolicyOperation.FETCH)
            authorize_operation(policy_snapshot, PolicyOperation.STORE_EXCERPT)
        except PolicyBlocked:
            return self._failure_commit(
                command=command,
                input_value=input_value,
                now=now,
                stage=CollectionStage.POLICY,
                code=StaticFetchFailureCode.SOURCE_POLICY_BLOCKED.value,
                impact="정책상 수집 또는 발췌 저장이 허용되지 않았습니다.",
                message_ko="이 소스는 현재 정책상 수집할 수 없습니다.",
                acquisition_status=AcquisitionStatus.ACCESS_DENIED,
                checked_url=input_value.source_url,
                http_status=None,
                representation=None,
                alternative_reason=AlternativeSourceReason.SOURCE_POLICY_BLOCKED,
            )

        if command.resume_stage is CollectionStage.POLICY:
            context.enter_stage(CollectionStage.FETCH, policy_revision=input_value.policy_revision)
            command = context.command
            _validate_effective_command(command, input_value)
        fetch_result = self._collector.fetch(
            StaticFetchRequest(
                command_id=command.command_id,
                source_url=input_value.source_url,
                policy=policy_snapshot,
                robots_permission=input_value.robots_permission,
                limits=input_value.limits,
                redirect_robots_permissions=input_value.redirect_robots_permissions,
            )
        )
        if fetch_result.command_id != command.command_id:
            raise ValueError("collector result command does not match collection command")
        candidate = fetch_result.candidate
        if candidate is None:
            failure_code = fetch_result.failure_code
            if failure_code is None:
                raise ValueError("collector result must contain a failure code")
            return self._failure_commit(
                command=command,
                input_value=input_value,
                now=now,
                stage=CollectionStage.FETCH,
                code=failure_code.value,
                impact="소스 응답을 안전하게 수집하지 못했습니다.",
                message_ko="소스를 수집하지 못했습니다.",
                acquisition_status=_fetch_acquisition_status(failure_code),
                checked_url=input_value.source_url,
                http_status=None,
                representation=None,
                alternative_reason=_alternative_reason(failure_code),
                retry=failure_code is StaticFetchFailureCode.RATE_LIMITED,
                retry_not_before=(
                    _parse_retry_not_before(fetch_result.retry_after, now)
                    if failure_code is StaticFetchFailureCode.RATE_LIMITED
                    else None
                ),
            )

        context.enter_stage(CollectionStage.PARSE, policy_revision=input_value.policy_revision)
        command = context.command
        _validate_effective_command(command, input_value)
        parsed = self._parser(candidate)
        if not isinstance(parsed, StaticParseResult):
            raise ValueError("static parser returned an invalid result")

        content_hash = parsed.content_hash
        supported_representation = (
            candidate.representation in {StaticRepresentation.HTML, StaticRepresentation.JSON}
            and parsed.representation is candidate.representation
        )
        trusted_catalog = (
            parsed
            if self._parser is extract_static_candidate
            else extract_static_candidate(candidate)
        )
        valid_parse_output = _validate_static_parse_output(
            candidate,
            parsed,
            trusted_catalog,
        )
        if (
            parsed.extraction_status not in {ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL}
            or not parsed.evidence
            or content_hash is None
            or not _is_sha256(content_hash)
            or not parsed.hash_profile_version
            or not parsed.parser_version
            or not supported_representation
            or not valid_parse_output
        ):
            parse_failure_code = (
                StaticFetchFailureCode.UNSUPPORTED_FORMAT.value
                if not supported_representation
                else StaticFetchFailureCode.EXTRACTION_FAILED.value
            )
            return self._failure_commit(
                command=command,
                input_value=input_value,
                now=now,
                stage=CollectionStage.PARSE,
                code=parse_failure_code,
                impact="검증 가능한 발췌를 만들지 못했습니다.",
                message_ko="소스 내용을 신뢰할 수 있는 발췌로 변환하지 못했습니다.",
                acquisition_status=AcquisitionStatus.EXTRACTION_FAILED,
                checked_url=candidate.final_target.url,
                http_status=candidate.http_status,
                representation=_source_representation(candidate.representation),
                alternative_reason=(
                    AlternativeSourceReason.UNSUPPORTED_FORMAT
                    if not supported_representation
                    else None
                ),
                parser_execution=self._failed_parser_execution(
                    command=command,
                    candidate=candidate,
                    parsed=parsed,
                    now=now,
                ),
                missing_sections=_safe_parser_limitations(parsed.limitations),
            )

        assert content_hash is not None
        return self._success_commit(
            command=command,
            input_value=input_value,
            candidate=candidate,
            parsed=parsed,
            content_hash=content_hash,
            now=now,
        )

    def close(self) -> None:
        close = getattr(self._collector, "close", None)
        if callable(close):
            cast(Callable[[], None], close)()

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now

    def _next_uuid(self) -> UUID:
        value = self._uuid_factory()
        if not isinstance(value, UUID):
            raise ValueError("uuid_factory must return UUID values")
        return value

    def _failed_parser_execution(
        self,
        *,
        command: CollectionCommand,
        candidate: StaticResponseCandidate,
        parsed: StaticParseResult,
        now: datetime,
    ) -> PreparedParserExecution | None:
        try:
            expected_content_hash = compute_content_hash(
                candidate.representation,
                candidate.document.text,
            )
        except (TypeError, UnicodeEncodeError, ValueError):
            return None
        if (
            parsed.content_hash != expected_content_hash
            or parsed.hash_profile_version != HASH_PROFILE_VERSION
            or parsed.representation is not candidate.representation
            or not _is_bounded_token(parsed.parser_version)
        ):
            return None
        return PreparedParserExecution(
            parser_execution_id=self._next_uuid(),
            source_id=command.source_id,
            source_version_id=None,
            content_hash=parsed.content_hash,
            parser_version=parsed.parser_version,
            output_hash=None,
            extraction_revision_id=None,
            status="failed",
            executed_at=now,
        )

    def _failure_commit(
        self,
        *,
        command: CollectionCommand,
        input_value: StaticCollectionInput,
        now: datetime,
        stage: CollectionStage,
        code: str,
        impact: str,
        message_ko: str,
        acquisition_status: AcquisitionStatus,
        checked_url: str,
        http_status: int | None,
        representation: SourceRepresentation | None,
        alternative_reason: AlternativeSourceReason | None,
        retry: bool = False,
        retry_not_before: datetime | None = None,
        parser_execution: PreparedParserExecution | None = None,
        missing_sections: tuple[str, ...] = (),
    ) -> PreparedCollectionCommit:
        resume_stage = CollectionStage.FETCH if retry else None
        checkpoint_ref = f"fetch:{self._attempt_id}" if retry else None
        result = CollectionResult(
            schema_version="w2.collection.v1",
            command_id=command.command_id,
            job_id=command.job_id,
            input_version=command.input_version,
            result_version=input_value.result_version,
            successful_source_refs=[],
            failures=[
                Failure(
                    source_id=command.source_id,
                    stage=stage,
                    code=code,
                    missing_sections=list(missing_sections),
                    impact=impact,
                    core_decision_revision=command.core_source_decision.decision_revision,
                )
            ],
            completion_kind=CompletionKind.NONE,
            resume_stage=resume_stage,
            checkpoint_ref=checkpoint_ref,
            retry_not_before=retry_not_before,
            message_ko=message_ko,
            required_actions=self._failure_actions(
                command=command,
                alternative_reason=alternative_reason,
                resume_stage=resume_stage,
                retry_not_before=retry_not_before,
            ),
            source_id=command.source_id,
            policy_revision=input_value.policy_revision,
        )
        snapshot = SourceObservationSnapshot(
            observation_id=self._next_uuid(),
            source_id=command.source_id,
            source_version_id=None,
            policy_decision_id=input_value.policy.policy_decision_id,
            observed_at=now,
            access_class=input_value.policy.access_class,
            acquisition_status=acquisition_status,
            http_status=http_status,
            checked_url=cast(AnyUrl, checked_url),
            error_code=code,
            representation=representation,
        )
        event = SourceEvent(
            event_id=self._next_uuid(),
            event_type=SourceEventType.OBSERVATION_CHANGED,
            schema_version="w2.source.v1",
            aggregate_id=command.source_id,
            aggregate_revision=input_value.aggregate_revision,
            occurred_at=now,
            payload=snapshot,
        )
        return PreparedCollectionCommit(
            attempt_id=self._attempt_id,
            result=result,
            finalized_at=now,
            parser_execution=parser_execution,
            observation=PreparedSourceObservation(snapshot=snapshot),
            events=(event,),
        )

    def _failure_actions(
        self,
        *,
        command: CollectionCommand,
        alternative_reason: AlternativeSourceReason | None,
        resume_stage: CollectionStage | None,
        retry_not_before: datetime | None,
    ) -> list[RequiredAction]:
        actions: list[RequiredAction] = []
        if command.core_source_decision.is_core:
            actions.append(
                CoreFailureDecisionAction(
                    code="core_failure_decision",
                    label_ko="핵심 소스의 처리 방식을 선택해 주세요.",
                    context=CoreFailureDecisionContext(
                        source_id=command.source_id,
                        core_decision_revision=command.core_source_decision.decision_revision,
                        choices=("continue_limited", "stop", "retry"),
                    ),
                )
            )
        if resume_stage is not None:
            actions.append(
                UserRetryAction(
                    code="user_retry",
                    label_ko="잠시 후 다시 시도해 주세요.",
                    context=UserRetryContext(
                        source_id=command.source_id,
                        resume_stage=resume_stage,
                        retry_not_before=retry_not_before,
                    ),
                )
            )
        if alternative_reason is not None:
            actions.append(
                FindAlternativeSourceAction(
                    code="find_alternative_source",
                    label_ko="대체 가능한 공식 소스를 찾아 주세요.",
                    context=FindAlternativeSourceContext(
                        source_id=command.source_id,
                        reason_code=alternative_reason,
                    ),
                )
            )
        return actions

    def _success_commit(
        self,
        *,
        command: CollectionCommand,
        input_value: StaticCollectionInput,
        candidate: StaticResponseCandidate,
        parsed: StaticParseResult,
        content_hash: str,
        now: datetime,
    ) -> PreparedCollectionCommit:
        source_version_id = self._next_uuid()
        source_representation = _source_representation(candidate.representation)
        evidence_pairs = tuple((draft, self._next_uuid()) for draft in parsed.evidence)
        prepared_evidence = tuple(
            PreparedEvidence(
                evidence_id=evidence_id,
                source_version_id=source_version_id,
                evidence_key=draft.evidence_key,
                section_title=draft.section_title,
                text_excerpt=draft.text_excerpt,
                locator=draft.locator,
                chunk_order=draft.chunk_order,
                origin_kind=draft.origin_kind,
            )
            for draft, evidence_id in evidence_pairs
        )
        evidence_spans = [
            Evidence(
                evidence_id=evidence_id,
                source_version_id=source_version_id,
                section_title=draft.section_title,
                text_excerpt=draft.text_excerpt,
                locator=draft.locator,
                chunk_order=draft.chunk_order,
            )
            for draft, evidence_id in evidence_pairs
        ]
        valid_from = _unknown_date()
        valid_to = _unknown_date()
        posting_parse = (
            parse_approved_static_posting(parsed)
            if input_value.source_type is SourceType.JOB_POSTING
            else None
        )
        published_at = (
            posting_parse.published_at if posting_parse is not None else parsed.published_at
        )
        date_values = {
            "published_at": published_at,
            "valid_from": valid_from,
            "valid_to": valid_to,
        }
        if posting_parse is not None:
            date_values["deadline"] = posting_parse.deadline
        source_version = PreparedSourceVersion(
            source_version_id=source_version_id,
            source_id=command.source_id,
            company_id=command.company_id,
            title=input_value.title,
            source_type=input_value.source_type,
            canonical_url=input_value.source_url,
            content_hash=content_hash,
            hash_profile_version=parsed.hash_profile_version,
            representation=source_representation,
            first_parser_version=parsed.parser_version,
            collected_at=now,
            published_at=published_at,
            valid_from=valid_from,
            valid_to=valid_to,
            language=input_value.language,
            policy_decision_id=input_value.policy.policy_decision_id,
        )
        output_hash = (
            compute_job_posting_parser_output_hash(parsed, posting_parse)
            if posting_parse is not None
            else compute_parser_output_hash(parsed)
        )
        extraction_revision_id = self._next_uuid()
        posting_sections: tuple[PostingSection, ...] = ()
        if posting_parse is not None:
            evidence_ids_by_key = {
                evidence.evidence_key: evidence.evidence_id for evidence in prepared_evidence
            }
            posting_sections = tuple(
                PostingSection(
                    section_key=section.section_key,
                    kind=section.kind,
                    heading_raw=section.heading_raw,
                    text_raw=section.text_raw,
                    evidence_ids=[
                        evidence_ids_by_key[evidence_key] for evidence_key in section.evidence_keys
                    ],
                    order=section.order,
                    relation_text=section.relation_text,
                )
                for section in posting_parse.sections
            )
        extraction_revision = PreparedExtractionRevision(
            extraction_revision_id=extraction_revision_id,
            source_version_id=source_version_id,
            parser_version=parsed.parser_version,
            output_hash=output_hash,
            created_at=now,
            extraction_status=parsed.extraction_status,
            posting_sections=posting_sections,
            date_values=date_values,
            limitations=parsed.limitations,
            evidence_ids=tuple(item.evidence_id for item in prepared_evidence),
        )
        parser_execution = PreparedParserExecution(
            parser_execution_id=self._next_uuid(),
            source_id=command.source_id,
            source_version_id=source_version_id,
            content_hash=content_hash,
            parser_version=parsed.parser_version,
            output_hash=output_hash,
            extraction_revision_id=extraction_revision_id,
            status="succeeded",
            executed_at=now,
        )
        acquisition_status: Literal[
            AcquisitionStatus.AVAILABLE,
            AcquisitionStatus.PARTIALLY_EXTRACTED,
        ]
        extraction_status: Literal[
            ExtractionStatus.COMPLETE,
            ExtractionStatus.PARTIAL,
        ]
        if parsed.extraction_status is ExtractionStatus.COMPLETE:
            acquisition_status = AcquisitionStatus.AVAILABLE
            extraction_status = ExtractionStatus.COMPLETE
        else:
            acquisition_status = AcquisitionStatus.PARTIALLY_EXTRACTED
            extraction_status = ExtractionStatus.PARTIAL
        failures: list[Failure] = []
        required_actions: list[RequiredAction] = []
        if parsed.extraction_status is ExtractionStatus.PARTIAL:
            failures.append(
                Failure(
                    source_id=command.source_id,
                    stage=CollectionStage.PARSE,
                    code="PARTIAL_EXTRACTION",
                    missing_sections=list(parsed.limitations),
                    impact="일부 발췌만 검증되었습니다.",
                    core_decision_revision=command.core_source_decision.decision_revision,
                )
            )
            required_actions = self._failure_actions(
                command=command,
                alternative_reason=None,
                resume_stage=None,
                retry_not_before=None,
            )
        result = CollectionResult(
            schema_version="w2.collection.v1",
            command_id=command.command_id,
            job_id=command.job_id,
            input_version=command.input_version,
            result_version=input_value.result_version,
            successful_source_refs=[
                SourceReference(
                    source_id=command.source_id,
                    source_version_id=source_version_id,
                    extraction_revision_id=extraction_revision_id,
                )
            ],
            failures=failures,
            completion_kind=(
                CompletionKind.COMPLETE
                if parsed.extraction_status is ExtractionStatus.COMPLETE
                else CompletionKind.PARTIAL
            ),
            resume_stage=None,
            checkpoint_ref=None,
            retry_not_before=None,
            message_ko=(
                "소스 수집을 완료했습니다."
                if parsed.extraction_status is ExtractionStatus.COMPLETE
                else "일부 발췌만 검증되어 제한적으로 수집했습니다."
            ),
            required_actions=required_actions,
            source_id=command.source_id,
            policy_revision=input_value.policy_revision,
        )
        snapshot = SourceObservationSnapshot(
            observation_id=self._next_uuid(),
            source_id=command.source_id,
            source_version_id=source_version_id,
            policy_decision_id=input_value.policy.policy_decision_id,
            observed_at=now,
            access_class=input_value.policy.access_class,
            acquisition_status=acquisition_status,
            http_status=candidate.http_status,
            checked_url=cast(AnyUrl, candidate.final_target.url),
            error_code=None,
            representation=source_representation,
        )
        envelope = SourceEnvelope(
            schema_version="w2.source.v1",
            source_id=command.source_id,
            source_version_id=source_version_id,
            extraction_revision_id=extraction_revision_id,
            company_id=command.company_id,
            source_type=input_value.source_type,
            url_or_path=cast(AnyUrl, candidate.final_target.url),
            title=input_value.title,
            policy=input_value.policy,
            acquisition_status=acquisition_status,
            extraction_status=extraction_status,
            accuracy_status=AccuracyStatus.UNVERIFIED,
            freshness_status=FreshnessStatus.CURRENT,
            published_at=published_at,
            collected_at=now,
            checked_at=now,
            valid_from=valid_from,
            valid_to=valid_to,
            content_hash=content_hash,
            hash_profile_version=parsed.hash_profile_version,
            parser_version=parsed.parser_version,
            language=input_value.language,
            evidence_spans=evidence_spans,
            posting_sections=list(posting_sections),
            retention_scope=RetentionScope.EXCERPTS_ONLY,
            normalized_body_ref=None,
            limitations=list(parsed.limitations),
            metadata=SourceMetadata(
                representation=source_representation,
                content_type=_source_content_type(candidate.representation),
                normalization_version=parsed.hash_profile_version,
            ),
        )
        event = SourceEvent(
            event_id=self._next_uuid(),
            event_type=SourceEventType.VERSION_AVAILABLE,
            schema_version="w2.source.v1",
            aggregate_id=command.source_id,
            aggregate_revision=input_value.aggregate_revision,
            occurred_at=now,
            payload=envelope,
        )
        return PreparedCollectionCommit(
            attempt_id=self._attempt_id,
            result=result,
            finalized_at=now,
            source_version=source_version,
            evidence=prepared_evidence,
            extraction_revision=extraction_revision,
            parser_execution=parser_execution,
            observation=PreparedSourceObservation(snapshot=snapshot),
            events=(event,),
        )


class JobPostingNotFound(LookupError):
    """Raised for both unknown and non-owned job postings."""


class JobPostingSelectionInvalid(ValueError):
    """Raised when a selected version or revision is outside the posting scope."""


class JobPostingContentUnavailable(LookupError):
    """Raised when a posting has no selected version with an available extraction."""


@dataclass(frozen=True, slots=True)
class JobPostingRecord:
    """Canonical posting identity returned by the repository boundary."""

    job_posting_id: UUID
    company_id: UUID
    source_id: UUID
    current_source_version_id: UUID | None


@dataclass(frozen=True, slots=True)
class JobPostingContentRecord:
    """One persisted posting revision view; raw document bodies are intentionally absent."""

    source_version_id: UUID
    source_id: UUID
    company_id: UUID
    title: str | None
    extraction_revision_id: UUID
    created_at: datetime
    extraction_status: ExtractionStatus
    posting_sections: tuple[PostingSection, ...]
    limitations: tuple[str, ...]
    evidence: tuple[JobPostingEvidenceSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class JobPostingEvidenceSnapshot:
    """Allow-listed evidence excerpt for a selected source-version view."""

    evidence_id: UUID
    source_version_id: UUID
    text_excerpt: str


@dataclass(frozen=True, slots=True)
class JobPostingImportAccepted:
    """Canonical identity returned by W1's atomic import and posting-resolution adapter."""

    job_posting_id: UUID
    source_id: UUID
    company_id: UUID
    job_id: UUID


@dataclass(frozen=True, slots=True)
class JobPostingImportResult:
    """Public import acceptance without W1 fences or owner internals."""

    job_posting_id: UUID
    source_id: UUID
    job_id: UUID


@dataclass(frozen=True, slots=True)
class JobPostingFailureReference:
    """Safe W1-owned failure state for a posting result or retry response."""

    failure: Failure
    checkpoint_ref: str | None
    required_actions: tuple[RequiredAction, ...]


@dataclass(frozen=True, slots=True)
class JobPostingW3Handoff:
    """Allow-listed W3 handoff metadata; private W3 details never cross this boundary."""

    owner: Literal["W3"]
    status: str
    code: str
    handoff_ref: str


@dataclass(frozen=True, slots=True)
class JobPostingRetryResult:
    """Public W1 retry acceptance; acceptance is not a claim of immediate dispatch."""

    job_posting_id: UUID
    job_id: UUID
    resume_stage: CollectionStage | None
    failure_reference: JobPostingFailureReference | None = None
    w3_handoff: JobPostingW3Handoff | None = None


@dataclass(frozen=True, slots=True)
class JobPostingView:
    """Selected immutable posting content and safe current failure information."""

    job_posting_id: UUID
    company_id: UUID
    source_id: UUID
    source_version_id: UUID | None
    extraction_revision_id: UUID | None
    title: str | None
    extraction_status: ExtractionStatus | None
    sections: tuple[PostingSection, ...]
    limitations: tuple[str, ...]
    failure_reference: JobPostingFailureReference | None
    evidence: tuple[JobPostingEvidenceSnapshot, ...] = ()


class JobPostingRepository(Protocol):
    """Read-only ownership/content adapter for a canonical posting identity."""

    def find_owned_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingRecord | None:
        """Return an owned posting only; unknown and other-owner postings return None."""

    def list_job_posting_content(
        self,
        *,
        source_id: UUID,
        company_id: UUID,
    ) -> tuple[JobPostingContentRecord, ...]:
        """Read persisted content and safe evidence excerpts only; never fetch or parse."""


class JobPostingJobPort(Protocol):
    """W1 job/ownership boundary; it owns durable dispatch and retry decisions."""

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> JobPostingImportAccepted:
        """Atomically accept W1 import and T031 canonical posting resolution.

        The adapter transaction must invoke ``resolve_job_posting`` and replay the
        same owner/key/payload with the same accepted identity.
        """

    def read_failure_reference(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingFailureReference | None:
        """Return safe failure/checkpoint/action state already owned by W1."""


class JobPostingService:
    """Connect canonical postings to W1 acceptance and persisted selected content."""

    def __init__(
        self,
        *,
        repository: JobPostingRepository,
        job_port: JobPostingJobPort,
    ) -> None:
        self._repository = repository
        self._job_port = job_port

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> JobPostingImportResult:
        """Project the canonical identity accepted atomically by the W1 adapter."""

        accepted = self._job_port.import_job_posting(
            owner_user_id=owner_user_id,
            company_id=company_id,
            url=url,
            analysis_request_id=analysis_request_id,
            idempotency_key=idempotency_key,
        )
        if accepted.company_id != company_id:
            raise JobPostingSelectionInvalid("job posting company scope is invalid")
        return JobPostingImportResult(
            job_posting_id=accepted.job_posting_id,
            source_id=accepted.source_id,
            job_id=accepted.job_id,
        )

    def get_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        source_version_id: UUID | None = None,
        extraction_revision_id: UUID | None = None,
    ) -> JobPostingView:
        """Read one owned posting at a current or explicitly selected immutable revision."""

        posting = self._owned_posting(owner_user_id, job_posting_id)
        selected = self._select_content(
            posting=posting,
            source_version_id=source_version_id,
            extraction_revision_id=extraction_revision_id,
        )
        if selected is None:
            failure_reference = self._job_port.read_failure_reference(
                owner_user_id=owner_user_id,
                job_posting_id=job_posting_id,
            )
            if failure_reference is None:
                raise JobPostingContentUnavailable(
                    "job posting has no available extraction revision or failure reference"
                )
            return JobPostingView(
                job_posting_id=posting.job_posting_id,
                company_id=posting.company_id,
                source_id=posting.source_id,
                source_version_id=source_version_id or posting.current_source_version_id,
                extraction_revision_id=None,
                title=None,
                extraction_status=None,
                sections=(),
                limitations=(),
                failure_reference=failure_reference,
                evidence=(),
            )
        evidence = self._validated_evidence(selected)
        failure_reference = self._job_port.read_failure_reference(
            owner_user_id=owner_user_id,
            job_posting_id=job_posting_id,
        )
        return JobPostingView(
            job_posting_id=posting.job_posting_id,
            company_id=posting.company_id,
            source_id=posting.source_id,
            source_version_id=selected.source_version_id,
            extraction_revision_id=selected.extraction_revision_id,
            title=selected.title,
            extraction_status=selected.extraction_status,
            sections=selected.posting_sections,
            limitations=selected.limitations,
            failure_reference=failure_reference,
            evidence=evidence,
        )

    def _owned_posting(self, owner_user_id: UUID, job_posting_id: UUID) -> JobPostingRecord:
        posting = self._repository.find_owned_job_posting(
            owner_user_id=owner_user_id,
            job_posting_id=job_posting_id,
        )
        if posting is None:
            raise JobPostingNotFound("job posting was not found")
        return posting

    def _select_content(
        self,
        *,
        posting: JobPostingRecord,
        source_version_id: UUID | None,
        extraction_revision_id: UUID | None,
    ) -> JobPostingContentRecord | None:
        selected_version_id = source_version_id or posting.current_source_version_id
        if selected_version_id is None:
            if extraction_revision_id is not None:
                raise JobPostingSelectionInvalid(
                    "selected extraction revision has no source version scope"
                )
            return None
        content = self._repository.list_job_posting_content(
            source_id=posting.source_id,
            company_id=posting.company_id,
        )
        matching_version_id = tuple(
            item for item in content if item.source_version_id == selected_version_id
        )
        selected_version = tuple(
            item
            for item in matching_version_id
            if item.source_id == posting.source_id and item.company_id == posting.company_id
        )
        if not selected_version:
            if (
                matching_version_id
                or source_version_id is not None
                or extraction_revision_id is not None
            ):
                raise JobPostingSelectionInvalid(
                    "selected source version is not valid for this job posting"
                )
            return None
        if extraction_revision_id is not None:
            selected_revision = next(
                (
                    item
                    for item in selected_version
                    if item.extraction_revision_id == extraction_revision_id
                ),
                None,
            )
            if selected_revision is None:
                raise JobPostingSelectionInvalid(
                    "selected extraction revision is not valid for this source version"
                )
            return selected_revision
        available = tuple(
            item
            for item in selected_version
            if item.extraction_status in {ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL}
        )
        if not available:
            return None
        return max(
            available,
            key=lambda item: (item.created_at, str(item.extraction_revision_id)),
        )

    @staticmethod
    def _validated_evidence(
        content: JobPostingContentRecord,
    ) -> tuple[JobPostingEvidenceSnapshot, ...]:
        evidence_by_id: dict[UUID, JobPostingEvidenceSnapshot] = {}
        for evidence in content.evidence:
            if evidence.source_version_id != content.source_version_id:
                raise JobPostingSelectionInvalid(
                    "evidence snapshot is not valid for this source version"
                )
            if evidence.evidence_id in evidence_by_id:
                raise JobPostingSelectionInvalid("evidence snapshot contains duplicate evidence")
            evidence_by_id[evidence.evidence_id] = evidence
        for section in content.posting_sections:
            if any(evidence_id not in evidence_by_id for evidence_id in section.evidence_ids):
                raise JobPostingSelectionInvalid("posting section references unavailable evidence")
        return content.evidence


class SourceVersionEvidenceNotFound(LookupError):
    """Raised for an unknown, non-owned, or mismatched Source resource."""


class SourceVersionEvidenceInvalidInput(ValueError):
    """Raised when a decoded Source version or Evidence cursor is invalid."""


class SourceVersionEvidenceDependencyUnavailable(RuntimeError):
    """Raised when an injected read or W1 adapter violates this boundary."""


class SourceRefreshIdempotencyConflict(RuntimeError):
    """Raised by W1 when a refresh key is reused with a different payload."""


SOURCE_REFRESH_OPERATION = "POST /api/v1/sources/{source_id}/refresh"
_RETAINED_BODY_REFERENCE_PREFIX = "retained-body:"
_RETAINED_BODY_REFERENCE_TOKEN_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class OwnedSourceRecord:
    """The minimum owner-scoped Source identity needed by this service."""

    source_id: UUID
    company_id: UUID


@dataclass(frozen=True, slots=True)
class OwnedSourceVersionRecord:
    """An owner-scoped immutable SourceVersion identity."""

    source_id: UUID
    source_version_id: UUID


@dataclass(frozen=True, slots=True)
class SourceVersionReadRecord:
    """A version snapshot supplied by the persistence read adapter.

    ``published_at`` is intentionally carried unchanged even though the current
    version-list API does not expose it.  In particular, an unknown publication
    date must never be replaced with ``collected_at``.
    """

    source_id: UUID
    source_version_id: UUID
    title: str | None
    source_type: str
    canonical_url: str
    content_hash: str
    hash_profile_version: str
    representation: str
    collected_at: datetime
    published_at: DateValue


@dataclass(frozen=True, slots=True)
class EvidenceReadRecord:
    """A safe Evidence projection; body text and paths are never represented."""

    evidence_id: UUID
    source_version_id: UUID
    section_title: str | None
    text_excerpt: str
    locator: Mapping[str, object]
    chunk_order: int
    origin_kind: str


@dataclass(frozen=True, slots=True)
class SourceOriginEvidenceRelation:
    """An explicit Origin-to-Evidence relationship supplied by the read adapter."""

    origin_relation_id: UUID
    relationship_kind: str
    verification_status: str
    origin_source_id: UUID | None
    origin_url: str | None
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class SourceCurrentReadState:
    """Current overlays, deliberately separate from immutable version snapshots."""

    current_restriction: Mapping[str, object]
    freshness_status: FreshnessStatus


@dataclass(frozen=True, slots=True)
class RetainedBodyReference:
    """An opaque, currently permitted retained-body handle, never its contents."""

    body_ref: str

    def __post_init__(self) -> None:
        token = (
            self.body_ref.removeprefix(_RETAINED_BODY_REFERENCE_PREFIX)
            if isinstance(self.body_ref, str)
            else ""
        )
        if (
            not isinstance(self.body_ref, str)
            or not self.body_ref.startswith(_RETAINED_BODY_REFERENCE_PREFIX)
            or not 1 <= len(token) <= _RETAINED_BODY_REFERENCE_TOKEN_MAX_LENGTH
            or not token.isascii()
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for character in token
            )
        ):
            raise ValueError("retained body reference is invalid")


@dataclass(frozen=True, slots=True)
class SourceRefreshAccepted:
    """The public-safe identity accepted atomically by the W1 refresh boundary."""

    source_id: UUID
    job_id: UUID


class SourceOwnershipPort(Protocol):
    """Resolve Source identities in owner scope without disclosing misses."""

    def find_owned_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
    ) -> OwnedSourceRecord | None:
        """Return an owned Source only; unknown and other-owner Sources return None."""

    def find_owned_source_version(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
    ) -> OwnedSourceVersionRecord | None:
        """Return an owned SourceVersion only; unknown and other-owner versions return None."""

    def find_owned_source_version_revision(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
    ) -> OwnedSourceVersionRecord | None:
        """Return an owned version/revision pair only; every other case returns None."""


class SourcePolicyAuthorizationPort(Protocol):
    """Authorize the current policy decision; this service owns no policy cache."""

    def authorize_current(
        self,
        *,
        source_id: UUID,
        operation: PolicyOperation,
    ) -> None:
        """Raise PolicyBlocked when the current Source policy rejects the operation."""


class SourceVersionEvidenceReadPort(Protocol):
    """Read immutable data and current overlays without fetching or re-extracting."""

    def current_state(self, *, source_id: UUID) -> SourceCurrentReadState:
        """Return the current restriction and freshness calculation for one Source."""

    def list_source_versions(
        self,
        *,
        source_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[SourceVersionReadRecord, ...]:
        """Return versions in persistence keyset order."""

    def list_revision_evidence(
        self,
        *,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        after: tuple[int, UUID] | None,
        limit: int,
    ) -> tuple[EvidenceReadRecord, ...] | None:
        """Return None only when the revision does not belong to the version."""

    def list_origin_relations_for_evidence(
        self,
        *,
        source_id: UUID,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[SourceOriginEvidenceRelation, ...]:
        """Return persisted explicit Origin links; never infer them from matching data."""


class RetainedBodyReferencePort(Protocol):
    """A policy-checked internal re-extraction decision boundary."""

    def get_current_body_reference(
        self,
        *,
        source_id: UUID,
        source_version_id: UUID,
    ) -> RetainedBodyReference | None:
        """Return only an opaque reference, never a body text value or storage path."""


class SourceRefreshJobPort(Protocol):
    """W1 owns atomic new-job acceptance, deduplication, slots, and dispatch."""

    def accept_policy_refresh(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
        operation: str,
    ) -> SourceRefreshAccepted:
        """Accept a new policy-stage refresh; queued acceptance is still successful."""


class SourceVersionEvidenceService:
    """Serve Source versions/Evidence and delegate explicit refresh acceptance to W1."""

    def __init__(
        self,
        *,
        ownership_port: SourceOwnershipPort,
        policy_port: SourcePolicyAuthorizationPort,
        read_port: SourceVersionEvidenceReadPort,
        refresh_job_port: SourceRefreshJobPort,
        retained_body_port: RetainedBodyReferencePort | None = None,
    ) -> None:
        self._ownership_port = ownership_port
        self._policy_port = policy_port
        self._read_port = read_port
        self._refresh_job_port = refresh_job_port
        self._retained_body_port = retained_body_port

    def list_source_versions(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> Mapping[str, object]:
        """List owned immutable versions with a validated persistence keyset cursor."""

        source = self._owned_source(owner_user_id, source_id)
        before = _source_version_cursor(cursor)
        page_limit = _source_page_limit(limit)
        rows = self._read_port.list_source_versions(
            source_id=source.source_id,
            before=before,
            limit=page_limit + 1,
        )
        _validate_version_page(rows, source.source_id, page_limit)
        items = rows[:page_limit]
        current = self._read_port.current_state(source_id=source.source_id)
        return {
            "items": tuple(_source_version_payload(item) for item in items),
            "next_cursor": (
                (items[-1].collected_at, items[-1].source_version_id)
                if len(rows) > page_limit and items
                else None
            ),
            "current_restriction": dict(current.current_restriction),
            "freshness_status": current.freshness_status.value,
        }

    def list_evidence(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> Mapping[str, object]:
        """List a valid owned version/revision's Evidence under the current policy."""

        version = self._owned_source_version_revision(
            owner_user_id,
            source_version_id,
            extraction_revision_id,
        )
        after = _evidence_cursor(cursor)
        page_limit = _source_page_limit(limit)
        self._policy_port.authorize_current(
            source_id=version.source_id,
            operation=PolicyOperation.STORE_EXCERPT,
        )
        self._policy_port.authorize_current(
            source_id=version.source_id,
            operation=PolicyOperation.REDISTRIBUTE,
        )
        rows = self._read_port.list_revision_evidence(
            source_version_id=version.source_version_id,
            extraction_revision_id=extraction_revision_id,
            after=after,
            limit=page_limit + 1,
        )
        if rows is None:
            raise SourceVersionEvidenceDependencyUnavailable(
                "owned source version/revision pair was unavailable from the read adapter"
            )
        _validate_evidence_page(rows, version.source_version_id, page_limit)
        items = rows[:page_limit]
        evidence_ids = tuple(item.evidence_id for item in items)
        origins = self._read_port.list_origin_relations_for_evidence(
            source_id=version.source_id,
            evidence_ids=evidence_ids,
        )
        _validate_origin_relations(origins, evidence_ids)
        body_ref = self._current_body_reference(version)
        current = self._read_port.current_state(source_id=version.source_id)
        return {
            "items": tuple(_evidence_payload(item) for item in items),
            "next_cursor": (
                (items[-1].chunk_order, items[-1].evidence_id)
                if len(rows) > page_limit and items
                else None
            ),
            "retention_scope": (
                RetentionScope.NORMALIZED_BODY.value
                if body_ref is not None
                else RetentionScope.EXCERPTS_ONLY.value
            ),
            "body_ref": body_ref,
            "current_restriction": dict(current.current_restriction),
            "freshness_status": current.freshness_status.value,
            "origin_relations": tuple(_origin_relation_payload(item) for item in origins),
        }

    def refresh_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """Re-check owner/current policy, then delegate one new policy-stage Job to W1."""

        source = self._owned_source(owner_user_id, source_id)
        if analysis_request_id is not None and not isinstance(analysis_request_id, UUID):
            raise SourceVersionEvidenceInvalidInput("analysis_request_id must be a UUID or None")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise SourceVersionEvidenceInvalidInput("idempotency_key must not be blank")
        self._policy_port.authorize_current(
            source_id=source.source_id,
            operation=PolicyOperation.FETCH,
        )
        accepted = self._refresh_job_port.accept_policy_refresh(
            owner_user_id=owner_user_id,
            source_id=source.source_id,
            analysis_request_id=analysis_request_id,
            idempotency_key=idempotency_key,
            operation=f"{SOURCE_REFRESH_OPERATION}#{source.source_id}",
        )
        if accepted.source_id != source.source_id or not isinstance(accepted.job_id, UUID):
            raise SourceVersionEvidenceDependencyUnavailable(
                "W1 refresh acceptance does not match the requested source"
            )
        return {
            "source_id": accepted.source_id,
            "job_id": accepted.job_id,
            "status_url": f"/api/v1/jobs/{accepted.job_id}",
        }

    def _owned_source(self, owner_user_id: UUID, source_id: UUID) -> OwnedSourceRecord:
        source = self._ownership_port.find_owned_source(
            owner_user_id=owner_user_id,
            source_id=source_id,
        )
        if source is None or source.source_id != source_id:
            raise SourceVersionEvidenceNotFound("source was not found")
        return source

    def _owned_source_version(
        self,
        owner_user_id: UUID,
        source_version_id: UUID,
    ) -> OwnedSourceVersionRecord:
        version = self._ownership_port.find_owned_source_version(
            owner_user_id=owner_user_id,
            source_version_id=source_version_id,
        )
        if version is None or version.source_version_id != source_version_id:
            raise SourceVersionEvidenceNotFound("source version was not found")
        return version

    def _owned_source_version_revision(
        self,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
    ) -> OwnedSourceVersionRecord:
        version = self._ownership_port.find_owned_source_version_revision(
            owner_user_id=owner_user_id,
            source_version_id=source_version_id,
            extraction_revision_id=extraction_revision_id,
        )
        if version is None or version.source_version_id != source_version_id:
            raise SourceVersionEvidenceNotFound("source version evidence was not found")
        return version

    def _current_body_reference(self, version: OwnedSourceVersionRecord) -> str | None:
        if self._retained_body_port is None:
            return None
        try:
            self._policy_port.authorize_current(
                source_id=version.source_id,
                operation=PolicyOperation.STORE_BODY,
            )
        except PolicyBlocked:
            return None
        retained = self._retained_body_port.get_current_body_reference(
            source_id=version.source_id,
            source_version_id=version.source_version_id,
        )
        if retained is None:
            return None
        try:
            return RetainedBodyReference(retained.body_ref).body_ref
        except (AttributeError, TypeError, ValueError) as exc:
            raise SourceVersionEvidenceDependencyUnavailable(
                "retained body reference is invalid"
            ) from exc


def _source_page_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceVersionEvidenceInvalidInput("limit must be a positive integer")
    return value


def _source_version_cursor(value: object | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    parts = _cursor_parts(value)
    collected_at = _cursor_datetime(parts[0])
    return collected_at, _cursor_uuid(parts[1])


def _evidence_cursor(value: object | None) -> tuple[int, UUID] | None:
    if value is None:
        return None
    parts = _cursor_parts(value)
    chunk_order = parts[0]
    if isinstance(chunk_order, bool) or not isinstance(chunk_order, int) or chunk_order < 0:
        raise SourceVersionEvidenceInvalidInput("evidence cursor chunk_order is invalid")
    return chunk_order, _cursor_uuid(parts[1])


def _cursor_parts(value: object) -> tuple[object, object]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise SourceVersionEvidenceInvalidInput("cursor must contain exactly two keyset values")
    return value[0], value[1]


def _cursor_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceVersionEvidenceInvalidInput("version cursor time is invalid") from exc
    else:
        raise SourceVersionEvidenceInvalidInput("version cursor time is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceVersionEvidenceInvalidInput("version cursor time must be timezone-aware")
    return parsed.astimezone(UTC)


def _cursor_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise SourceVersionEvidenceInvalidInput("cursor id is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise SourceVersionEvidenceInvalidInput("cursor id is invalid") from exc
    if str(parsed) != value:
        raise SourceVersionEvidenceInvalidInput("cursor id is not canonical")
    return parsed


def _validate_version_page(
    rows: tuple[SourceVersionReadRecord, ...],
    source_id: UUID,
    page_limit: int,
) -> None:
    if len(rows) > page_limit + 1:
        raise SourceVersionEvidenceDependencyUnavailable("version read exceeded the requested page")
    previous: tuple[datetime, UUID] | None = None
    for row in rows:
        if row.source_id != source_id:
            raise SourceVersionEvidenceDependencyUnavailable("version belongs to another source")
        if row.collected_at.tzinfo is None or row.collected_at.utcoffset() is None:
            raise SourceVersionEvidenceDependencyUnavailable(
                "version collected_at is not timezone-aware"
            )
        position = (row.collected_at, row.source_version_id)
        if previous is not None and position >= previous:
            raise SourceVersionEvidenceDependencyUnavailable(
                "version read is not in stable keyset order"
            )
        previous = position


def _validate_evidence_page(
    rows: tuple[EvidenceReadRecord, ...],
    source_version_id: UUID,
    page_limit: int,
) -> None:
    if len(rows) > page_limit + 1:
        raise SourceVersionEvidenceDependencyUnavailable(
            "evidence read exceeded the requested page"
        )
    previous: tuple[int, UUID] | None = None
    for row in rows:
        if row.source_version_id != source_version_id or row.chunk_order < 0:
            raise SourceVersionEvidenceDependencyUnavailable(
                "evidence is not valid for this source version"
            )
        position = (row.chunk_order, row.evidence_id)
        if previous is not None and position <= previous:
            raise SourceVersionEvidenceDependencyUnavailable(
                "evidence read is not in stable keyset order"
            )
        previous = position


def _validate_origin_relations(
    relations: tuple[SourceOriginEvidenceRelation, ...],
    evidence_ids: tuple[UUID, ...],
) -> None:
    visible_evidence_ids = set(evidence_ids)
    for relation in relations:
        if not relation.evidence_ids or not set(relation.evidence_ids).issubset(
            visible_evidence_ids
        ):
            raise SourceVersionEvidenceDependencyUnavailable(
                "origin relation is not linked to the visible Evidence page"
            )


def _source_version_payload(row: SourceVersionReadRecord) -> dict[str, object]:
    return {
        "source_version_id": row.source_version_id,
        "title": row.title,
        "source_type": row.source_type,
        "canonical_url": row.canonical_url,
        "content_hash": row.content_hash,
        "hash_profile_version": row.hash_profile_version,
        "representation": row.representation,
        "collected_at": row.collected_at,
    }


def _evidence_payload(row: EvidenceReadRecord) -> dict[str, object]:
    return {
        "evidence_id": row.evidence_id,
        "source_version_id": row.source_version_id,
        "section_title": row.section_title,
        "text_excerpt": row.text_excerpt,
        "locator": dict(row.locator),
        "chunk_order": row.chunk_order,
        "origin_kind": row.origin_kind,
    }


def _origin_relation_payload(row: SourceOriginEvidenceRelation) -> dict[str, object]:
    return {
        "origin_relation_id": row.origin_relation_id,
        "relationship_kind": row.relationship_kind,
        "verification_status": row.verification_status,
        "origin_source_id": row.origin_source_id,
        "origin_url": row.origin_url,
        "evidence_ids": row.evidence_ids,
    }


def compute_parser_output_hash(result: StaticParseResult) -> str:
    return hashlib.sha256(_canonical_parser_output(result)).hexdigest()


def compute_job_posting_parser_output_hash(
    result: StaticParseResult,
    posting: StaticPostingParseResult,
) -> str:
    """Hash the immutable static output plus every persisted job-posting semantic field."""

    return hashlib.sha256(_canonical_job_posting_parser_output(result, posting)).hexdigest()


def _canonical_parser_output(result: StaticParseResult) -> bytes:
    return _canonical_json(_parser_output_payload(result))


def _canonical_job_posting_parser_output(
    result: StaticParseResult,
    posting: StaticPostingParseResult,
) -> bytes:
    return _canonical_json(
        {
            "output_hash_profile_version": JOB_POSTING_OUTPUT_HASH_PROFILE_VERSION,
            "static_parse": _parser_output_payload(result),
            "posting": {
                "sections": [
                    {
                        "section_key": section.section_key,
                        "kind": section.kind.value,
                        "heading_raw": section.heading_raw,
                        "text_raw": section.text_raw,
                        "evidence_keys": list(section.evidence_keys),
                        "order": section.order,
                        "relation_text": section.relation_text,
                    }
                    for section in posting.sections
                ],
                "published_at": posting.published_at.model_dump(mode="json"),
                "deadline": posting.deadline.model_dump(mode="json"),
            },
        }
    )


def _parser_output_payload(result: StaticParseResult) -> dict[str, object]:
    return {
        "extraction_status": result.extraction_status.value,
        "evidence": [
            {
                "evidence_key": evidence.evidence_key,
                "section_title": evidence.section_title,
                "text_excerpt": evidence.text_excerpt,
                "locator": evidence.locator.model_dump(mode="json"),
                "chunk_order": evidence.chunk_order,
                "origin_kind": evidence.origin_kind,
            }
            for evidence in result.evidence
        ],
        "sections": [
            {
                "section_key": section.section_key,
                "heading_raw": section.heading_raw,
                "text_raw": section.text_raw,
                "evidence_keys": list(section.evidence_keys),
                "order": section.order,
            }
            for section in result.sections
        ],
        "published_at": result.published_at.model_dump(mode="json"),
        "limitations": list(result.limitations),
    }


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_static_parse_output(
    candidate: StaticResponseCandidate,
    result: StaticParseResult,
    trusted_catalog: StaticParseResult,
) -> bool:
    try:
        if (
            candidate.representation not in {StaticRepresentation.HTML, StaticRepresentation.JSON}
            or result.representation is not candidate.representation
            or trusted_catalog.representation is not candidate.representation
            or result.hash_profile_version != HASH_PROFILE_VERSION
            or not _is_bounded_token(result.parser_version)
            or result.content_hash
            != compute_content_hash(candidate.representation, candidate.document.text)
            or not isinstance(result.evidence, tuple)
            or not isinstance(result.sections, tuple)
            or not isinstance(result.limitations, tuple)
            or not isinstance(result.published_at, DateValue)
            or not result.evidence
            or len(result.evidence) > _MAX_PREPARED_EVIDENCE
            or len(result.sections) > len(result.evidence)
            or len(result.limitations) > _MAX_PARSER_LIMITATIONS
            or not trusted_catalog.evidence
            or not _parse_output_within_budget(result)
            or any(not isinstance(section, ExtractedSectionDraft) for section in result.sections)
        ):
            return False

        trusted_evidence = {
            evidence.evidence_key: (index, evidence)
            for index, evidence in enumerate(trusted_catalog.evidence)
        }
        evidence_by_key: dict[str, EvidenceDraft] = {}
        previous_catalog_index = -1
        for evidence in result.evidence:
            catalog_match = trusted_evidence.get(evidence.evidence_key)
            if (
                not isinstance(evidence, EvidenceDraft)
                or catalog_match is None
                or evidence != catalog_match[1]
                or not _is_bounded_token(evidence.evidence_key, max_length=256)
                or evidence.evidence_key in evidence_by_key
                or catalog_match[0] <= previous_catalog_index
                or evidence.origin_kind
                != (
                    "static_html"
                    if candidate.representation is StaticRepresentation.HTML
                    else "json_value"
                )
                or not isinstance(evidence.text_excerpt, str)
                or not evidence.text_excerpt
                or (
                    evidence.section_title is not None
                    and not isinstance(evidence.section_title, str)
                )
            ):
                return False
            evidence_by_key[evidence.evidence_key] = evidence
            previous_catalog_index = catalog_match[0]

        if (
            result.extraction_status is ExtractionStatus.COMPLETE
            and result.evidence != trusted_catalog.evidence
        ):
            return False

        expected_sections: list[ExtractedSectionDraft] = []
        expected_evidence_keys: list[str] = []
        for trusted_section in trusted_catalog.sections:
            retained_keys = tuple(
                key for key in trusted_section.evidence_keys if key in evidence_by_key
            )
            if not retained_keys:
                continue
            text_raw = "\n".join(evidence_by_key[key].text_excerpt for key in retained_keys)
            heading_raw = trusted_section.heading_raw
            if candidate.representation is StaticRepresentation.HTML and heading_raw is not None:
                heading_key = next(
                    (
                        key
                        for key in trusted_section.evidence_keys
                        if trusted_evidence[key][1].text_excerpt == heading_raw
                    ),
                    None,
                )
                if heading_key not in evidence_by_key:
                    heading_raw = None
            expected_sections.append(
                ExtractedSectionDraft(
                    section_key=_static_section_key(
                        retained_keys,
                        text_raw,
                        trusted_section.order,
                    ),
                    heading_raw=heading_raw,
                    text_raw=text_raw,
                    evidence_keys=retained_keys,
                    order=trusted_section.order,
                )
            )
            expected_evidence_keys.extend(retained_keys)
        if result.sections != tuple(expected_sections) or tuple(expected_evidence_keys) != tuple(
            evidence.evidence_key for evidence in result.evidence
        ):
            return False

        validated_date = DateValue.model_validate(result.published_at.model_dump())
        if (
            validated_date != result.published_at
            or result.published_at != trusted_catalog.published_at
            or _safe_parser_limitations(result.limitations) != result.limitations
        ):
            return False
        return len(_canonical_parser_output(result)) <= _MAX_PREPARED_OUTPUT_BYTES
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return False


def _static_section_key(
    evidence_keys: tuple[str, ...],
    text_raw: str,
    order: int,
) -> str:
    payload = "\0".join((*evidence_keys, text_raw, str(order))).encode("utf-8")
    return f"section:{hashlib.sha256(payload).hexdigest()}"


def _safe_parser_limitations(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > _MAX_PARSER_LIMITATIONS:
        return ()
    return tuple(
        value
        for value in values
        if isinstance(value, str) and _is_bounded_token(value, max_length=128)
    )


def _is_bounded_token(value: object, *, max_length: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and all(
            character.isascii() and (character.isalnum() or character in {"_", "-", ".", ":"})
            for character in value
        )
    )


def _parse_output_within_budget(result: StaticParseResult) -> bool:
    remaining_bytes = _MAX_PREPARED_OUTPUT_BYTES
    section_evidence_refs = 0

    def consume(value: object) -> bool:
        nonlocal remaining_bytes
        if value is None:
            return True
        if not isinstance(value, str):
            return False
        remaining_bytes -= len(value.encode("utf-8"))
        return remaining_bytes >= 0

    try:
        if not consume(result.parser_version) or not consume(result.hash_profile_version):
            return False
        for evidence in result.evidence:
            if not all(
                consume(value)
                for value in (
                    evidence.evidence_key,
                    evidence.section_title,
                    evidence.text_excerpt,
                    evidence.locator.value,
                    evidence.locator.normalization_version,
                    evidence.origin_kind,
                )
            ):
                return False
        for section in result.sections:
            if (
                not isinstance(section.evidence_keys, tuple)
                or len(section.evidence_keys) > _MAX_PREPARED_EVIDENCE
            ):
                return False
            section_evidence_refs += len(section.evidence_keys)
            if section_evidence_refs > _MAX_PREPARED_SECTION_EVIDENCE_REFS:
                return False
            if not all(
                consume(value)
                for value in (
                    section.section_key,
                    section.heading_raw,
                    section.text_raw,
                )
            ):
                return False
            if not all(consume(value) for value in section.evidence_keys):
                return False
        if not all(consume(value) for value in result.limitations):
            return False
        return consume(result.published_at.raw_text)
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return False


def _parse_retry_not_before(value: object, now: datetime) -> datetime | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    raw_value = value.strip()
    if not raw_value:
        return None
    if raw_value.isascii() and raw_value.isdecimal():
        seconds = min(int(raw_value), _MAX_RETRY_AFTER_SECONDS)
        return now + timedelta(seconds=seconds)
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    now_utc = now.astimezone(UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc <= now_utc:
        return now
    return min(parsed_utc, now_utc + timedelta(seconds=_MAX_RETRY_AFTER_SECONDS))


def _policy_snapshot(value: StaticCollectionInput) -> PolicySnapshot:
    return PolicySnapshot(
        official_status=value.policy.official_status,
        access_class=value.policy.access_class,
        collection_permission=value.policy.collection_permission,
        excerpt_storage_permission=value.policy.excerpt_storage_permission,
        body_storage_permission=value.policy.body_storage_permission,
        redistribution_permission=value.policy.redistribution_permission,
        revision=value.policy_revision,
    )


def _validate_static_collection_input(
    command: CollectionCommand,
    value: StaticCollectionInput,
) -> None:
    if not isinstance(value, StaticCollectionInput):
        raise ValueError("collection input provider returned an invalid value")
    if value.source_id != command.source_id or value.company_id != command.company_id:
        raise ValueError("collection input does not match command source or company scope")
    if not isinstance(value.policy, Policy):
        raise ValueError("collection input policy must be a Policy")
    if not isinstance(value.source_url, str) or not value.source_url.strip():
        raise ValueError("collection input source_url is required")
    if not isinstance(value.source_type, SourceType):
        raise ValueError("collection input source_type is invalid")
    if not isinstance(value.robots_permission, Permission):
        raise ValueError("collection input robots_permission is invalid")
    _require_positive_revision(value.policy_revision, "policy_revision")
    _require_positive_revision(value.result_version, "result_version")
    _require_positive_revision(value.aggregate_revision, "aggregate_revision")


def _validate_effective_command(
    command: CollectionCommand,
    value: StaticCollectionInput,
) -> None:
    if command.source_id != value.source_id or command.company_id != value.company_id:
        raise ValueError("stage permit changed collection source or company scope")
    if command.policy_revision not in {None, value.policy_revision}:
        raise ValueError("stage permit policy revision does not match collection input")


def _require_positive_revision(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")


def _unknown_date() -> DateValue:
    return DateValue(
        status=DateStatus.UNKNOWN,
        raw_text=None,
        value=None,
        precision=None,
        timezone=None,
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _source_representation(value: StaticRepresentation) -> SourceRepresentation:
    if value is StaticRepresentation.HTML:
        return SourceRepresentation.STATIC_HTML
    return SourceRepresentation.OFFICIAL_JSON


def _source_content_type(
    value: StaticRepresentation,
) -> Literal["text/html", "application/json"]:
    if value is StaticRepresentation.HTML:
        return "text/html"
    return "application/json"


def _fetch_acquisition_status(code: StaticFetchFailureCode) -> AcquisitionStatus:
    return {
        StaticFetchFailureCode.SOURCE_POLICY_BLOCKED: AcquisitionStatus.ACCESS_DENIED,
        StaticFetchFailureCode.ACCESS_DENIED: AcquisitionStatus.ACCESS_DENIED,
        StaticFetchFailureCode.NOT_FOUND: AcquisitionStatus.NOT_FOUND,
        StaticFetchFailureCode.RATE_LIMITED: AcquisitionStatus.RATE_LIMITED,
    }.get(code, AcquisitionStatus.EXTRACTION_FAILED)


def _alternative_reason(code: StaticFetchFailureCode) -> AlternativeSourceReason | None:
    return {
        StaticFetchFailureCode.SOURCE_POLICY_BLOCKED: AlternativeSourceReason.SOURCE_POLICY_BLOCKED,
        StaticFetchFailureCode.UNSUPPORTED_FORMAT: AlternativeSourceReason.UNSUPPORTED_FORMAT,
        StaticFetchFailureCode.NOT_FOUND: AlternativeSourceReason.NOT_FOUND,
        StaticFetchFailureCode.ACCESS_DENIED: AlternativeSourceReason.ACCESS_DENIED,
    }.get(code)
