"""Fail-closed approved input loading for the W2 collection runtime."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Literal, NoReturn
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epick_engine.source_collection.collector import _hard_deadline_seconds
from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionCommand,
    CollectionStage,
    OfficialStatus,
    Permission,
    Policy,
    SourceType,
)
from epick_engine.source_collection.persistence import (
    Company,
    OutboxEvent,
    Source,
    SourcePolicyDecision,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    ExecutionPolicyUnconfigured,
    parse_execution_limits,
)
from epick_engine.source_collection.service import StaticCollectionInput
from epick_engine.source_collection.w1_lookup_client import W1LookupClient
from epick_engine.source_collection.w1_transport import LookupRequest

type SessionFactory = Callable[[], Session]
type PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]
type NonEmptyStrictStr = Annotated[str, Field(strict=True, min_length=1)]

_SOURCE_INTEGER_FIELDS = ("policy_revision", "result_version")
_LIMIT_INTEGER_FIELDS = (
    "site_concurrency",
    "global_concurrency",
    "source_ttl_seconds",
    "max_response_bytes",
    "max_decompressed_bytes",
    "max_redirects",
    "general_retry_limit",
    "retention_days",
)


class SourceRuntimeInputError(ValueError):
    """Approved runtime input is unavailable or inconsistent with durable state."""


class _StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeExecutionLimits(_StrictConfigModel):
    """Strict wire representation parsed only through the shared policy validator."""

    site_concurrency: StrictInt
    global_concurrency: StrictInt
    source_ttl_seconds: StrictInt
    max_response_bytes: StrictInt
    max_decompressed_bytes: StrictInt
    connect_timeout_seconds: StrictFloat
    read_timeout_seconds: StrictFloat
    max_redirects: StrictInt
    general_retry_limit: StrictInt
    retention_days: StrictInt

    @model_validator(mode="after")
    def validate_shared_limits(self) -> RuntimeExecutionLimits:
        try:
            parse_execution_limits(self.model_dump(mode="python"))
        except ExecutionPolicyUnconfigured as exc:
            raise ValueError(str(exc)) from None
        return self

    def to_execution_limits(self) -> ExecutionLimits:
        return parse_execution_limits(self.model_dump(mode="python"))


class RuntimeSourceConfig(_StrictConfigModel):
    policy_revision: PositiveStrictInt
    robots_permission: Permission
    result_version: PositiveStrictInt
    language: NonEmptyStrictStr | None
    redirect_robots_permissions: tuple[tuple[NonEmptyStrictStr, Permission], ...]
    limits: RuntimeExecutionLimits

    @field_validator("robots_permission", mode="before")
    @classmethod
    def parse_robots_permission(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return Permission(value)
            except ValueError:
                raise ValueError("robots_permission must be an existing permission") from None
        return value

    @field_validator("redirect_robots_permissions", mode="before")
    @classmethod
    def normalize_redirect_permissions(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        normalized: list[tuple[object, object]] = []
        for item in value:
            if not isinstance(item, list | tuple) or len(item) != 2:
                return value
            url, raw_permission = item
            if isinstance(raw_permission, str):
                try:
                    permission: object = Permission(raw_permission)
                except ValueError:
                    raise ValueError(
                        "redirect robots permission must be an existing permission"
                    ) from None
            else:
                permission = raw_permission
            normalized.append((url, permission))
        return tuple(normalized)

    @property
    def execution_limits(self) -> ExecutionLimits:
        return self.limits.to_execution_limits()


class RuntimeOnboardingPolicy(_StrictConfigModel):
    """An explicit W2 approval; never derived from a W1-submitted URL."""

    official_status: Literal["verified"]
    access_class: Literal["public", "restricted"]
    collection_permission: Literal["allowed"]
    excerpt_storage_permission: Permission
    body_storage_permission: Permission
    redistribution_permission: Permission
    evidence_refs: Annotated[tuple[NonEmptyStrictStr, ...], Field(min_length=1)]
    checked_at: AwareDatetime
    policy_version: NonEmptyStrictStr

    @field_validator(
        "excerpt_storage_permission",
        "body_storage_permission",
        "redistribution_permission",
        mode="before",
    )
    @classmethod
    def parse_permission(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return Permission(value)
            except ValueError:
                return value
        return value

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def parse_evidence(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("checked_at", mode="before")
    @classmethod
    def parse_checked_at(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value


class RuntimeOnboardingProfile(_StrictConfigModel):
    company_id: UUID
    company_legal_name: NonEmptyStrictStr
    company_official_domain: NonEmptyStrictStr
    hostname: NonEmptyStrictStr
    path_prefix: NonEmptyStrictStr
    w1_source_type: NonEmptyStrictStr
    w2_source_type: SourceType
    company_identity_evidence: Annotated[tuple[NonEmptyStrictStr, ...], Field(min_length=1)]
    policy: RuntimeOnboardingPolicy
    source_config: RuntimeSourceConfig

    @field_validator("w2_source_type", mode="before")
    @classmethod
    def parse_source_type(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return SourceType(value)
            except ValueError:
                return value
        return value

    @field_validator("company_id", mode="before")
    @classmethod
    def parse_company_id(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                return value
        return value

    @field_validator("company_identity_evidence", mode="before")
    @classmethod
    def parse_identity_evidence(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_approval(self) -> RuntimeOnboardingProfile:
        if (
            self.hostname != self.hostname.lower()
            or "." not in self.hostname
            or any(character in self.hostname for character in "/:@?# ")
            or self.company_official_domain != self.company_official_domain.lower()
            or "." not in self.company_official_domain
            or any(character in self.company_official_domain for character in "/:@?# ")
            or not (
                self.hostname == self.company_official_domain
                or self.hostname.endswith(f".{self.company_official_domain}")
            )
            or not self.path_prefix.startswith("/")
            or not self.path_prefix.endswith("/")
            or ".." in self.path_prefix
            or self.source_config.policy_revision != 1
        ):
            raise ValueError("onboarding approval is invalid")
        return self

    def permits(self, *, company_id: UUID, url: str, w1_source_type: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                company_id == self.company_id
                and w1_source_type == self.w1_source_type
                and parsed.scheme == "https"
                and parsed.hostname == self.hostname
                and parsed.port in (None, 443)
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and parsed.path.startswith(self.path_prefix)
            )
        except ValueError:
            return False


class RuntimeSourceConfigFile(_StrictConfigModel):
    """Non-secret, immutable runtime configuration keyed by normalized Source UUID."""

    schema_version: Literal["w2.source-runtime-config.v1"]
    claim_lease_seconds: PositiveStrictInt
    sources: Mapping[UUID, RuntimeSourceConfig]
    approved_sites: tuple[RuntimeOnboardingProfile, ...] = ()

    @field_validator("approved_sites", mode="before")
    @classmethod
    def parse_approved_sites(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_source_keys(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        normalized: dict[UUID, object] = {}
        for raw_source_id, source_config in value.items():
            if isinstance(raw_source_id, UUID):
                source_id = raw_source_id
            elif isinstance(raw_source_id, str):
                try:
                    source_id = UUID(raw_source_id)
                except ValueError:
                    raise ValueError("runtime config Source key must be a UUID") from None
            else:
                raise ValueError("runtime config Source key must be a UUID")
            if source_id in normalized:
                raise ValueError("duplicate Source after UUID normalization")
            normalized[source_id] = source_config
        return normalized

    @field_serializer("sources")
    def serialize_sources(
        self,
        value: Mapping[UUID, RuntimeSourceConfig],
    ) -> dict[UUID, RuntimeSourceConfig]:
        return dict(value)

    @model_validator(mode="after")
    def validate_claim_lease(self) -> RuntimeSourceConfigFile:
        if not self.sources and not self.approved_sites:
            raise ValueError("at least one pinned Source or approved site is required")
        approved_configs = (*self.sources.values(), *(p.source_config for p in self.approved_sites))
        for source_config in approved_configs:
            limits = source_config.execution_limits
            minimum_lease = (
                _hard_deadline_seconds(limits)
                + limits.connect_timeout_seconds
                + limits.read_timeout_seconds
            )
            if not math.isfinite(minimum_lease) or self.claim_lease_seconds <= minimum_lease:
                raise ValueError(
                    "claim lease must exceed collector deadline and connect/read timeouts"
                )
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        keys = [
            (p.company_id, p.hostname, p.path_prefix, p.w1_source_type)
            for p in self.approved_sites
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate onboarding approval")
        return self

    def onboarding_profile(
        self, *, company_id: UUID, url: str, w1_source_type: str
    ) -> RuntimeOnboardingProfile | None:
        matches = [
            profile
            for profile in self.approved_sites
            if profile.permits(company_id=company_id, url=url, w1_source_type=w1_source_type)
        ]
        # Overlapping path approvals are ambiguous; never pick one by order.
        return matches[0] if len(matches) == 1 else None

    def approved_config_for_input(
        self,
        *,
        source_id: UUID,
        company_id: UUID,
        url: str,
        source_type: SourceType,
    ) -> RuntimeSourceConfig | None:
        pinned = self.sources.get(source_id)
        if pinned is not None:
            return pinned
        matches = [
            profile.source_config
            for profile in self.approved_sites
            if profile.w2_source_type == source_type
            and profile.permits(
                company_id=company_id, url=url, w1_source_type=profile.w1_source_type
            )
        ]
        return matches[0] if len(matches) == 1 else None

    @property
    def heartbeat_interval_seconds(self) -> float:
        """Return the fixed renewal cadence used across child fetch and parent parsing."""

        return self.claim_lease_seconds / 3.0


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise SourceRuntimeInputError("runtime config has a duplicate JSON object key")
        parsed[key] = value
    return parsed


def _reject_nonfinite_json_number(_value: str) -> NoReturn:
    raise SourceRuntimeInputError("runtime config contains a non-finite JSON number")


def _normalize_schema_integer_field(container: dict[str, object], field: str) -> None:
    value = container.get(field)
    if type(value) is float and math.isfinite(value) and value.is_integer():
        container[field] = int(value)


def _normalize_schema_integer_numbers(parsed: object) -> object:
    if not isinstance(parsed, dict):
        return parsed

    _normalize_schema_integer_field(parsed, "claim_lease_seconds")
    sources = parsed.get("sources")
    approved_sites = parsed.get("approved_sites")
    candidates: list[object] = []
    if isinstance(sources, dict):
        candidates.extend(sources.values())
    if isinstance(approved_sites, list):
        candidates.extend(
            site.get("source_config") for site in approved_sites if isinstance(site, dict)
        )
    for source in candidates:
        if not isinstance(source, dict):
            continue
        for field in _SOURCE_INTEGER_FIELDS:
            _normalize_schema_integer_field(source, field)

        limits = source.get("limits")
        if not isinstance(limits, dict):
            continue
        for field in _LIMIT_INTEGER_FIELDS:
            _normalize_schema_integer_field(limits, field)

    return parsed


def parse_runtime_source_config_json(
    raw_json: str | bytes | bytearray,
) -> RuntimeSourceConfigFile:
    """Parse the approved wire config without allowing JSON key shadowing."""

    try:
        parsed = json.loads(
            raw_json,
            object_pairs_hook=_reject_duplicate_json_object_keys,
            parse_constant=_reject_nonfinite_json_number,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceRuntimeInputError("runtime config is not valid JSON") from exc
    return RuntimeSourceConfigFile.model_validate(_normalize_schema_integer_numbers(parsed))


class SqlAlchemyCollectionInputProvider:
    """Load one approved Source input in a short read transaction."""

    def __init__(
        self,
        session_factory: SessionFactory,
        config: RuntimeSourceConfigFile,
        onboarding_lookup_client: W1LookupClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._onboarding_lookup_client = onboarding_lookup_client

    @property
    def claim_lease_seconds(self) -> int:
        return self._config.claim_lease_seconds

    @property
    def heartbeat_interval_seconds(self) -> float:
        return self._config.heartbeat_interval_seconds

    def load(self, command: CollectionCommand) -> StaticCollectionInput:
        if (
            command.resume_stage is not CollectionStage.POLICY
            or command.policy_revision is not None
        ):
            raise SourceRuntimeInputError("runtime input requires an initial policy command")

        approved = self._config.sources.get(command.source_id)
        if approved is None:
            approved = self._onboard_approved_source(command)

        with self._session_factory() as session, session.begin():
            source = session.get(Source, command.source_id)
            if source is None:
                raise SourceRuntimeInputError("registered Source is missing")
            if source.company_id != command.company_id:
                raise SourceRuntimeInputError("Source/Company binding does not match")

            company = session.get(Company, command.company_id)
            if company is None or company.identity_status != "verified":
                raise SourceRuntimeInputError("Source company is not verified")

            policy = session.scalar(
                select(SourcePolicyDecision)
                .where(SourcePolicyDecision.source_id == command.source_id)
                .order_by(SourcePolicyDecision.revision.desc())
                .limit(1)
            )
            if policy is None:
                raise SourceRuntimeInputError("current Source policy is missing")
            if policy.revision != approved.policy_revision:
                raise SourceRuntimeInputError(
                    "approved policy revision does not match current policy"
                )

            aggregate_revision = session.scalar(
                select(func.coalesce(func.max(OutboxEvent.aggregate_revision), 0) + 1).where(
                    OutboxEvent.aggregate_id == command.source_id
                )
            )
            if (
                isinstance(aggregate_revision, bool)
                or not isinstance(aggregate_revision, int)
                or aggregate_revision <= 0
            ):
                raise SourceRuntimeInputError("next aggregate revision is unavailable")

            try:
                source_type = SourceType(source.source_type)
                mapped_policy = Policy(
                    policy_decision_id=policy.policy_decision_id,
                    official_status=OfficialStatus(policy.official_status),
                    access_class=AccessClass(policy.access_class),
                    collection_permission=Permission(policy.collection_permission),
                    excerpt_storage_permission=Permission(policy.excerpt_storage_permission),
                    body_storage_permission=Permission(policy.body_storage_permission),
                    redistribution_permission=Permission(policy.redistribution_permission),
                    checked_at=policy.checked_at,
                    policy_version=policy.policy_version,
                )
            except ValueError:
                raise SourceRuntimeInputError("registered Source policy data is invalid") from None

            return StaticCollectionInput(
                source_id=source.source_id,
                company_id=source.company_id,
                source_url=source.canonical_url,
                title=source.title,
                source_type=source_type,
                policy=mapped_policy,
                policy_revision=policy.revision,
                robots_permission=approved.robots_permission,
                limits=approved.execution_limits,
                result_version=approved.result_version,
                aggregate_revision=aggregate_revision,
                language=approved.language,
                redirect_robots_permissions=approved.redirect_robots_permissions,
            )

    def _onboard_approved_source(self, command: CollectionCommand) -> RuntimeSourceConfig:
        client = self._onboarding_lookup_client
        if client is None:
            raise SourceRuntimeInputError("approved source config is missing")
        # W1 only returns this metadata for the exact live command, owner epoch
        # and execution fence. A W1 URL is still not a W2 policy decision.
        try:
            response = client.lookup_source_onboarding(
                LookupRequest(
                    schema_version="w1.private.command-lookup.v1",
                    command_id=command.command_id,
                    execution_fence=int(command.execution_fence),
                    owner_deletion_epoch=command.owner_deletion_epoch,
                )
            )
        except Exception:
            raise SourceRuntimeInputError("W1 source identity is unavailable") from None
        metadata = response.source
        if (
            response.status != "AVAILABLE"
            or metadata is None
            or metadata.source_id != command.source_id
            or metadata.company_id != command.company_id
        ):
            raise SourceRuntimeInputError("W1 source identity does not match the command")
        profile = self._config.onboarding_profile(
            company_id=metadata.company_id,
            url=metadata.canonical_url,
            w1_source_type=metadata.source_type,
        )
        if (
            profile is None
            or metadata.company_legal_name != profile.company_legal_name
            or metadata.company_official_domain != profile.company_official_domain
            or metadata.company_identification_status != "VERIFIED"
        ):
            raise SourceRuntimeInputError("W2 site approval is missing or mismatched")
        with self._session_factory() as session, session.begin():
            session.execute(
                insert(Company)
                .values(
                    company_id=profile.company_id,
                    legal_name=profile.company_legal_name,
                    aliases=[],
                    official_domains=[profile.company_official_domain, profile.hostname],
                    legal_identifiers={},
                    identity_status="verified",
                    identity_evidence=list(profile.company_identity_evidence),
                )
                .on_conflict_do_nothing(index_elements=[Company.company_id])
            )
            company = session.get(Company, profile.company_id)
            if (
                company is None
                or company.identity_status != "verified"
                or company.legal_name != profile.company_legal_name
                or profile.hostname not in company.official_domains
            ):
                raise SourceRuntimeInputError("registered Company conflicts with W2 approval")
            session.execute(
                insert(Source)
                .values(
                    source_id=metadata.source_id,
                    company_id=metadata.company_id,
                    source_type=profile.w2_source_type.value,
                    canonical_url=metadata.canonical_url,
                    title=metadata.title,
                    pointer_update_mode="FINALIZE_GATE",
                )
                .on_conflict_do_nothing(index_elements=[Source.source_id])
            )
            source = session.get(Source, metadata.source_id)
            if (
                source is None
                or source.company_id != metadata.company_id
                or source.canonical_url != metadata.canonical_url
                or source.source_type != profile.w2_source_type.value
                or source.pointer_update_mode != "FINALIZE_GATE"
            ):
                raise SourceRuntimeInputError("registered Source conflicts with W1 identity")
            policy = profile.policy
            decision_id = uuid5(
                NAMESPACE_URL,
                f"epick-w2-onboarding:{metadata.source_id}:{policy.policy_version}",
            )
            session.execute(
                insert(SourcePolicyDecision)
                .values(
                    policy_decision_id=decision_id,
                    source_id=metadata.source_id,
                    revision=1,
                    official_status=policy.official_status,
                    access_class=policy.access_class,
                    collection_permission=policy.collection_permission,
                    excerpt_storage_permission=policy.excerpt_storage_permission.value,
                    body_storage_permission=policy.body_storage_permission.value,
                    redistribution_permission=policy.redistribution_permission.value,
                    evidence_refs=list(policy.evidence_refs),
                    checked_at=policy.checked_at,
                    policy_version=policy.policy_version,
                )
                .on_conflict_do_nothing(
                    index_elements=[SourcePolicyDecision.source_id, SourcePolicyDecision.revision]
                )
            )
            current = session.scalar(
                select(SourcePolicyDecision)
                .where(SourcePolicyDecision.source_id == metadata.source_id)
                .order_by(SourcePolicyDecision.revision.desc())
                .limit(1)
            )
            if (
                current is None
                or current.policy_decision_id != decision_id
                or current.revision != 1
                or current.policy_version != policy.policy_version
                or current.collection_permission != "allowed"
                or current.excerpt_storage_permission != policy.excerpt_storage_permission.value
                or current.body_storage_permission != policy.body_storage_permission.value
                or current.redistribution_permission != policy.redistribution_permission.value
            ):
                raise SourceRuntimeInputError("current W2 Source policy conflicts with approval")
        return profile.source_config
