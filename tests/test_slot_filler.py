import asyncio

import pytest
from agent.slot_filler import (
    LEAVE_SLOTS, SlotState, SlotFiller, init_state, apply_extracted, next_ask, confirm_summary,
)


async def _fake_full_extractor(intent, user_message, missing_keys):
    return {"leave_type": "年假", "start_date": "2026-08-18", "end_date": "2026-08-20"}


def test_init_state_marks_all_required_missing():
    st = init_state("leave_request", LEAVE_SLOTS)
    assert st.missing == ["leave_type", "start_date", "end_date"]
    assert st.slots == {}


def test_apply_extracted_fills_missing_and_recomputes():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"leave_type": "年假"})
    assert st.slots == {"leave_type": "年假"}
    assert st.missing == ["start_date", "end_date"]


def test_apply_extracted_ignores_unknown_keys():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"bogus": "x", "leave_type": "事假"})
    assert "bogus" not in st.slots
    assert st.slots["leave_type"] == "事假"


def test_next_ask_returns_correct_prompt():
    assert "请假类型" in next_ask(LEAVE_SLOTS, "leave_type")
    assert "从哪天开始" in next_ask(LEAVE_SLOTS, "start_date")


def test_confirm_summary_lists_filled_slots():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"leave_type": "年假", "start_date": "2026-08-18", "end_date": "2026-08-20"})
    summary = confirm_summary(LEAVE_SLOTS, st.slots)
    assert "年假" in summary
    assert "2026-08-18" in summary


def test_handle_returns_confirmation_and_stores_pending():
    sf = SlotFiller(extractor=_fake_full_extractor)
    entity = {"type": "leave"}
    result = asyncio.run(sf.handle("s1", "请年假 8月18到20", "start_operation", entity))
    assert isinstance(result, str)
    assert "确认" in result
    assert sf._pending_confirms["s1"]["slots"]["leave_type"] == "年假"
    assert sf._pending_confirms["s1"]["tool"] == "feishu_submit_leave_request"


def test_consume_confirm_returns_tool_dict():
    sf = SlotFiller()
    sf._pending_confirms["s1"] = {
        "slots": {"leave_type": "年假", "start_date": "2026-08-18", "end_date": "2026-08-20"},
        "tool": "feishu_submit_leave_request",
    }
    result = sf.consume("s1", "确认")
    assert result == {
        "action": "confirm",
        "tool": "feishu_submit_leave_request",
        "args": {"leave_type": "年假", "start_date": "2026-08-18", "end_date": "2026-08-20"},
    }


def test_consume_cancel_returns_cancel_string():
    sf = SlotFiller()
    sf._pending_confirms["s1"] = {
        "slots": {"leave_type": "年假"}, "tool": "feishu_submit_leave_request",
    }
    assert sf.consume("s1", "取消") == "已取消操作。"


def test_consume_unrelated_returns_none():
    sf = SlotFiller()
    sf._pending_confirms["s1"] = {
        "slots": {"leave_type": "年假"}, "tool": "feishu_submit_leave_request",
    }
    assert sf.consume("s1", "帮我查工资") is None


def test_consume_no_pending_returns_none():
    sf = SlotFiller()
    assert sf.consume("s1", "确认") is None


def test_consume_with_explicit_tool():
    f = SlotFiller()
    f.store_pending("s1", {"leave_type": "年假", "start_date": "8/18"}, "feishu_submit_leave_request")
    assert f.has_pending("s1")
    r = f.consume("s1", "确认")
    assert r == {"action": "confirm", "tool": "feishu_submit_leave_request",
                 "args": {"leave_type": "年假", "start_date": "8/18"}}
    assert not f.has_pending("s1")


def test_consume_cancel_drops_pending():
    f = SlotFiller()
    f.store_pending("s2", {"a": 1}, "approval_approve_request")
    assert f.consume("s2", "取消") == "已取消操作。"
    assert not f.has_pending("s2")


def test_pop_pending_no_side_effect_when_empty():
    f = SlotFiller()
    assert f.pop_pending("nope") is None


def test_has_pending_false_initially_true_after_fill():
    sf = SlotFiller(extractor=_fake_full_extractor)
    assert sf.has_pending("s1") is False
    asyncio.run(sf.handle("s1", "请年假 8月18到20", "start_operation", {"type": "leave"}))
    assert sf.has_pending("s1") is True


def test_has_active_true_during_collection():
    sf = SlotFiller(extractor=_fake_full_extractor)
    assert sf.has_active("s1") is False
    sf._sessions["s1"] = init_state("leave_request", LEAVE_SLOTS)
    assert sf.has_active("s1") is True


def test_handle_after_fill_moves_active_to_pending():
    sf = SlotFiller(extractor=_fake_full_extractor)
    sf._sessions["s1"] = init_state("leave_request", LEAVE_SLOTS)
    assert sf.has_active("s1") is True
    assert sf.has_pending("s1") is False

    result = asyncio.run(sf.handle("s1", "请年假 8月18到20", "start_operation", {"type": "leave"}))
    assert isinstance(result, str)

    assert sf.has_active("s1") is False
    assert sf.has_pending("s1") is True
