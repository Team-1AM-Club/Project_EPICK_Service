from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
DOCKERFILE = REPO_ROOT / "w3" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / "w3" / ".dockerignore"
ECR_POLICY = REPO_ROOT / "backend" / "infra" / "w3-runtime-ecr-policy.template.json"


def test_w3_image_is_locked_non_root_and_source_attributable() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "python:3.12.14-slim-bookworm@sha256:" in dockerfile
    assert "uv==0.8.15" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "ARG W3_SOURCE_SHA" in dockerfile
    assert 'org.opencontainers.image.revision="${W3_SOURCE_SHA}"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "w3_knowledge.core_runtime_cli"]' in dockerfile
    assert "AWS_ACCESS_KEY_ID" not in dockerfile
    assert "AWS_SECRET_ACCESS_KEY" not in dockerfile


def test_w3_build_context_is_allowlisted() -> None:
    rules = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()

    assert rules[0] == "*"
    assert "!pyproject.toml" in rules
    assert "!uv.lock" in rules
    assert "!src/**" in rules
    assert "**/*.env" not in {rule for rule in rules if rule.startswith("!")}


def test_publisher_and_worker_ecr_permissions_are_separate() -> None:
    template = json.loads(ECR_POLICY.read_text(encoding="utf-8"))
    publisher = json.dumps(template["publisher_policy"])
    worker = json.dumps(template["worker_pull_policy"])

    assert "ecr:PutImage" in publisher
    assert "ecr:PutImage" not in worker
    assert "ecr:BatchGetImage" in worker
    assert "${W3_ECR_REPOSITORY_ARN}" in publisher
    assert "${W3_ECR_REPOSITORY_ARN}" in worker
