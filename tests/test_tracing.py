from mcp_servers.knowledge.tools import LOW_RELEVANCE_THRESHOLD, is_low_relevance


def test_is_low_relevance_empty():
    assert is_low_relevance([]) is True


def test_is_low_relevance_below_threshold():
    assert is_low_relevance([{"score": 0.1}]) is True


def test_is_low_relevance_above_threshold():
    assert is_low_relevance([{"score": 0.8}]) is False


def test_is_low_relevance_uses_first_result_only():
    assert is_low_relevance([{"score": 0.9}, {"score": 0.05}]) is False
