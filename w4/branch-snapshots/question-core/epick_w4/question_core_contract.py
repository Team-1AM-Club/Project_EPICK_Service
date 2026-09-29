"""Flat Question Core codec; a reviewed schema is an explicit dependency.

Reviewed W1 wire adoption is pinned separately from candidate review.
Wire adoption does not approve runtime configuration, policy, or real user data.
"""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


class QuestionCoreError(ValueError):
    """Only fixed, non-private error codes cross the producer boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise QuestionCoreError(code)


def canonical_json(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise QuestionCoreError("CORE_JSON_INVALID") from None


def digest(value):
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "CORE_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _nonfinite(_):
    raise QuestionCoreError("CORE_JSON_INVALID")


def parse_json(body, *, maximum=16 * 1024):
    try:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        require(isinstance(raw, bytes), "CORE_JSON_INVALID")
        require(len(raw) <= maximum, "CORE_BODY_TOO_LARGE")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        if isinstance(error, QuestionCoreError):
            raise
        raise QuestionCoreError("CORE_JSON_INVALID") from None


_VARIABLE_FIELDS = frozenset(
    {
        "message_id",
        "decision_id",
        "occurred_at",
        "job_id",
        "question_version_id",
        "source_id",
        "analysis_input_version",
        "decision_version",
        "is_core",
        "decision_code",
        "reason_code",
    }
)
_CONSTANTS = {
    "producer": "w4",
    "decision_scope": "QUESTION_MATCHING",
    "decision_owner": "W4",
    "company_id": None,
}
_CONTRACT_CONSTANTS = frozenset({"schema_version", "message_type", "visibility_scope"})
# W1 adopted this exact schema while retaining the candidate version string.
# Original candidate bytes and arbitrary status relabeling remain ineligible.
# Provenance: samples/question-core-w1-runtime-20260919/W1-CONTRACT-README.md.
_ADOPTED_CANDIDATE_PINS = frozenset(
    {
        (
            "519b9127227ad3ca6483a61a5143d355c5eea5eb",
            "1d004ea5ea4bbd346926c6be878de759f25b43011b7117ff8700c4d48e8b71af",
        ),
        (
            "deda25c62a762e3f7f6ea5273c93a7e6a18c6412",
            "1d004ea5ea4bbd346926c6be878de759f25b43011b7117ff8700c4d48e8b71af",
        ),
    }
)


def _local_refs_only(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef"}:
                require(
                    isinstance(item, str) and item.startswith("#"), "CORE_SCHEMA_EXTERNAL_REFERENCE"
                )
            _local_refs_only(item)
    elif isinstance(value, list):
        for item in value:
            _local_refs_only(item)


@dataclass(frozen=True)
class ContractSource:
    """Operator-reviewed provenance, not transport authentication."""

    schema_sha256: str
    status: str = "PENDING_W1_ORIGINAL"
    w1_full_sha: str | None = None


class QuestionCoreContract:
    def __init__(self, schema_path, *, source: ContractSource, max_body_bytes=16 * 1024):
        try:
            raw = Path(schema_path).read_bytes()
        except OSError:
            raise QuestionCoreError("CORE_SCHEMA_UNAVAILABLE") from None
        require(
            hashlib.sha256(raw).hexdigest() == source.schema_sha256, "CORE_SCHEMA_HASH_MISMATCH"
        )
        require(
            source.status in {"TEST_ONLY", "PENDING_W1_ORIGINAL", "W1_CANDIDATE", "W1_ADOPTED"},
            "CORE_SCHEMA_PROVENANCE_INVALID",
        )
        require(
            type(max_body_bytes) is int and 0 < max_body_bytes <= 16 * 1024,
            "CORE_SCHEMA_PROVENANCE_INVALID",
        )
        schema = parse_json(raw, maximum=256 * 1024)
        require(isinstance(schema, dict), "CORE_SCHEMA_UNSUPPORTED")
        _local_refs_only(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            raise QuestionCoreError("CORE_SCHEMA_UNSUPPORTED") from None
        properties = schema.get("properties", {})
        require(isinstance(properties, dict), "CORE_SCHEMA_UNSUPPORTED")
        fields = set(properties)
        required = schema.get("required", [])
        require(
            isinstance(required, list) and all(isinstance(k, str) for k in required),
            "CORE_SCHEMA_UNSUPPORTED",
        )
        require(
            schema.get("type") == "object"
            and schema.get("additionalProperties") is False
            and fields == set(required)
            and _VARIABLE_FIELDS | set(_CONSTANTS) | {"schema_version"}
            <= fields
            <= _VARIABLE_FIELDS | set(_CONSTANTS) | _CONTRACT_CONSTANTS,
            "CORE_SCHEMA_UNSUPPORTED",
        )
        constants = {}
        for name in fields - _VARIABLE_FIELDS:
            prop = properties[name]
            require(isinstance(prop, dict), "CORE_SCHEMA_UNSUPPORTED")
            if name == "company_id" and prop.get("type") == "null" and "const" not in prop:
                # W1's candidate expresses the sole null value with type, not const.
                constants[name] = None
            else:
                require("const" in prop, "CORE_SCHEMA_UNSUPPORTED")
                constants[name] = prop["const"]
        require(
            all(constants.get(k) == v for k, v in _CONSTANTS.items()), "CORE_SCHEMA_UNSUPPORTED"
        )
        require(
            isinstance(constants["schema_version"], str) and constants["schema_version"],
            "CORE_SCHEMA_UNSUPPORTED",
        )
        require(
            "visibility_scope" not in constants or constants["visibility_scope"] == "PRIVATE",
            "CORE_SCHEMA_UNSUPPORTED",
        )
        self.source = source
        self.max_body_bytes = max_body_bytes
        self._constants = constants
        self._validator = Draft202012Validator(deepcopy(schema), format_checker=FormatChecker())

    def require_adopted(self):
        version = self._constants["schema_version"].lower()
        pinned_adoption = (
            isinstance(self.source.w1_full_sha, str)
            and (
                self.source.w1_full_sha,
                self.source.schema_sha256,
            )
            in _ADOPTED_CANDIDATE_PINS
        )
        require(
            self.source.status == "W1_ADOPTED"
            and isinstance(self.source.w1_full_sha, str)
            and re.fullmatch(r"[0-9a-f]{40}", self.source.w1_full_sha)
            and "test-only" not in version
            and ("candidate" not in version or pinned_adoption),
            "CORE_TRANSPORT_NOT_ADOPTED",
        )

    def build(self, fields):
        require(set(fields) == _VARIABLE_FIELDS, "CORE_EVENT_FIELDS_INVALID")
        return self.validate({**self._constants, **deepcopy(fields)})

    def validate(self, value):
        if isinstance(value, (str, bytes)):
            value = parse_json(value, maximum=self.max_body_bytes)
        require(isinstance(value, dict), "CORE_EVENT_INVALID")
        raw = canonical_json(value)
        require(len(raw) <= self.max_body_bytes, "CORE_BODY_TOO_LARGE")
        require(self._validator.is_valid(value), "CORE_EVENT_INVALID")
        require(
            type(value["decision_version"]) is int
            and value["decision_version"] > 0
            and type(value["is_core"]) is bool
            and value["decision_code"] in {"CORE_REQUIRED", "NON_CORE_OPTIONAL"}
            and value["is_core"] == (value["decision_code"] == "CORE_REQUIRED"),
            "CORE_EVENT_INVALID",
        )
        require(value["message_id"] != value["decision_id"], "CORE_ID_ROLES_NOT_SEPARATE")
        return deepcopy(value)
