"""W4 local prototype. Its input contract is provisional, not the public API schema."""

from .pipeline import recommend
from .evidence_extraction import extract_evidence
from .model_selection import select_evaluated_client

__all__ = ["recommend", "extract_evidence", "select_evaluated_client"]
