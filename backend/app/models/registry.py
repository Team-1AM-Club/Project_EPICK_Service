"""Explicit ORM registry loading for narrow runtime entrypoints.

The main API and Alembic import many model modules through their routers and
migration environment.  Private worker adapters intentionally start with a
much smaller import graph, so SQLAlchemy may otherwise encounter an unresolved
string foreign key when flushing an object from that graph.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module

MODEL_MODULES = (
    "application_workspace",
    "deletion",
    "experience",
    "identity",
    "job_postings",
    "jobs",
    "knowledge",
    "lifecycle_operations",
    "organizations",
    "privacy_controls",
    "projection",
    "question_analysis",
    "recommendation_execution",
    "recommendations",
    "sources",
    "w2_commit_operations",
    "w4_question_core",
)


@lru_cache(maxsize=1)
def load_all_models() -> None:
    """Register every ORM-owned table exactly once in ``Base.metadata``."""

    for module_name in MODEL_MODULES:
        import_module(f"app.models.{module_name}")
