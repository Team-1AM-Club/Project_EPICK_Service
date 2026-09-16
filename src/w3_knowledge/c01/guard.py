"""C01 opt-in integration with existing knowledge structuring and policy ports."""

from dataclasses import replace

from ..models import PolicyDecision
from ..restriction.guard import structure_guarded as guarded_structure
from .contracts import IndexKey
from .store import Store


class RetainedTextGuard:
    def __init__(self, delegate, store):
        self.delegate, self.store = delegate, store

    def can_use(self, *, context, source):
        if self.delegate.can_use(context=context, source=source) != PolicyDecision.ALLOW:
            return PolicyDecision.DENY
        return (
            PolicyDecision.ALLOW
            if self.store.can_use_texts(
                source.source_ref.source_id, [artifact.text for artifact in source.artifacts]
            )
            else PolicyDecision.DENY
        )


def structure_guarded(request, ports, store, keys, config=None):
    if not isinstance(store, Store) or any(not isinstance(key, IndexKey) for key in keys.values()):
        raise ValueError("C01_STORE_AND_KEYS_REQUIRED")
    ports = replace(ports, retained_text=RetainedTextGuard(ports.retained_text, store))
    return guarded_structure(request, ports, store, keys, config)
