import pytest
from agent.slot_filler import (
    LEAVE_SLOTS, SlotState, init_state, apply_extracted, next_ask, confirm_summary,
)


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
