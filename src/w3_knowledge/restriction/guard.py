"""Explicit integration with the existing knowledge module and its policy ports."""

from dataclasses import replace

from ..models import (
    KnowledgeBundle,
    Limitation,
    PolicyDecision,
    ProcessingStatus,
    StructureResponse,
    RequirementPresenceState,
)
from ..service import structure


class RestrictionTextGuard:
    def __init__(self, retained_text, store, keys):
        self.retained_text, self.store, self.keys = retained_text, store, keys

    def can_use(self, *, context, source):
        if any(
            artifact.source_ref.source_id != source.source_ref.source_id
            or artifact.source_ref.source_version_id != source.source_ref.source_version_id
            for artifact in source.artifacts
        ):
            return PolicyDecision.DENY
        if self.retained_text.can_use(context=context, source=source) != PolicyDecision.ALLOW:
            return PolicyDecision.DENY
        key = self.keys.get(source.source_ref.source_id)
        if key is None or key.source_version_id != source.source_ref.source_version_id:
            return PolicyDecision.DENY
        current = self.store.status(source.source_ref.source_id)
        return (
            PolicyDecision.ALLOW
            if current["index_ack"] and current["index_key"] == key.model_dump()
            else PolicyDecision.DENY
        )


def structure_guarded(request, ports, store, keys, config=None):
    """Require independently supplied complete index keys; never invent extraction IDs.

    Recheck before returning. W4 still must revalidate at every subsequent use.
    This wrapper does not replace W1 authorization or semantic validation ports.
    """
    before = {
        source.source_ref.source_id: store.status(source.source_ref.source_id)["generation"]
        for source in request.sources
    }
    guarded = replace(ports, retained_text=RestrictionTextGuard(ports.retained_text, store, keys))
    response = structure(request, guarded, config)
    if any(
        store.status(source)["generation"] != generation for source, generation in before.items()
    ):
        return StructureResponse(
            status=ProcessingStatus.PAUSED,
            bundle=KnowledgeBundle(
                mode=request.context.mode,
                purpose=request.context.purpose,
                requirement_presence=RequirementPresenceState.NOT_ASSESSED,
                source_reviews=(),
                limitations=(
                    Limitation(
                        code="RESTRICTION_CONTEXT_CHANGED",
                        impact="처리 중 근거 사용 상태가 변경되어 반환을 보류했습니다.",
                    ),
                ),
            ),
        )
    return response
