from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]
DOCKERFILE = REPO_ROOT / "backend" / "infra" / "w3-runtime.Dockerfile"
ECR_POLICY = REPO_ROOT / "backend" / "infra" / "w3-runtime-ecr-policy.template.json"
IMPLEMENTATION_SHA = "0c4f01f9537a3129c976fae5e63111a7982c5da6"
RUNTIME_SHA = "402f7a63bf8f8d601cc6ada1ce685280f47320ce"
RECEIPT_HEAD_SHA = RUNTIME_SHA


def test_w3_image_is_locked_non_root_and_source_attributable() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "python:3.12.14-slim-bookworm@sha256:" in dockerfile
    assert "uv==0.8.15" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert f'org.opencontainers.image.revision="{RUNTIME_SHA}"' in dockerfile
    assert f'io.epick.w3.upstream-implementation="{IMPLEMENTATION_SHA}"' in dockerfile
    assert f'io.epick.w3.receipt-head="{RECEIPT_HEAD_SHA}"' in dockerfile
    assert 'io.epick.w3.policy-revision="w3.retention/1.1"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "w3_knowledge.core_runtime_cli"]' in dockerfile
    assert "AWS_ACCESS_KEY_ID" not in dockerfile
    assert "AWS_SECRET_ACCESS_KEY" not in dockerfile


def test_w3_image_recipe_only_copies_allowlisted_archive_inputs() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    copy_lines = [line.strip() for line in dockerfile.splitlines() if line.startswith("COPY ")]

    assert "COPY pyproject.toml uv.lock ./" in copy_lines
    expected_spec_copy = (
        "COPY specs/001-source-knowledge-validation/spec.md "
        "./specs/001-source-knowledge-validation/spec.md"
    )
    assert expected_spec_copy in copy_lines
    assert "COPY src ./src" in copy_lines
    assert all(line not in {"COPY . .", "COPY . /app"} for line in copy_lines)
    assert ".git" not in dockerfile
    assert ".venv" not in "\n".join(line for line in copy_lines if "--from=builder" not in line)


def test_publisher_and_worker_ecr_permissions_are_separate() -> None:
    template = json.loads(ECR_POLICY.read_text(encoding="utf-8"))
    publisher = json.dumps(template["publisher_policy"])
    worker = json.dumps(template["worker_pull_policy"])

    assert "ecr:PutImage" in publisher
    assert "ecr:PutImage" not in worker
    assert "ecr:BatchGetImage" in worker
    assert "${W3_ECR_REPOSITORY_ARN}" in publisher
    assert "${W3_ECR_REPOSITORY_ARN}" in worker
