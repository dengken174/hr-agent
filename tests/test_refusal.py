from agent.intent import IntentResult, should_refuse, OUT_OF_SCOPE_REPLY, INTENT_LABELS


def test_intent_labels_contains_out_of_scope():
    assert "out_of_scope" in INTENT_LABELS


def test_should_refuse_out_of_scope():
    r = IntentResult(intent="out_of_scope")
    assert should_refuse(r) is True


def test_should_refuse_low_confidence_non_chat():
    r = IntentResult(intent="search_own_info", confidence=0.4)
    assert should_refuse(r) is True


def test_should_not_refuse_general_chat_low_confidence():
    r = IntentResult(intent="general_chat", confidence=0.4)
    assert should_refuse(r) is False


def test_should_not_refuse_normal():
    r = IntentResult(intent="policy_query", confidence=0.9)
    assert should_refuse(r) is False


def test_out_of_scope_reply_is_nonempty():
    assert len(OUT_OF_SCOPE_REPLY) > 20
