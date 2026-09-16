from pathlib import Path

import pytest
from pydantic import ValidationError

from w3_knowledge.restriction.contracts import Event, IndexRequest, ReplayBatch, Snapshot

ROOT = Path(__file__).resolve().parents[2] / "contracts/restriction/v0.1-draft"


@pytest.mark.parametrize(
    "name,model",
    [
        ("initial", Event),
        ("restrict", Event),
        ("release", Event),
        ("replay", ReplayBatch),
        ("snapshot", Snapshot),
        ("index-request", IndexRequest),
    ],
)
def test_published_examples_are_consumable(name, model):
    model.model_validate_json((ROOT / "examples" / f"{name}.json").read_bytes())


@pytest.mark.parametrize(
    "name", ["private-field", "future-version", "revision-string", "source-mismatch"]
)
def test_published_invalid_examples_are_rejected(name):
    with pytest.raises(ValidationError):
        Event.model_validate_json((ROOT / "invalid" / f"{name}.json").read_bytes())
