from w3_knowledge.relations import deterministic_id


def test_deterministic_id_is_stable_and_order_sensitive_at_call_site() -> None:
    assert deterministic_id("claim", "a", "b") == deterministic_id("claim", "a", "b")
    assert deterministic_id("claim", "a", "b") != deterministic_id("claim", "b", "a")
