"""请假等结构化写操作的槽位状态机。

流程: 意图命中 start_operation+leave → 逐槽收集参数 → 二次确认 → 执行。
状态机控流程（确定性），LLM 只做槽位提取（可注入，便于测试）。
"""

from dataclasses import dataclass, field

LEAVE_SLOTS = [
    {"key": "leave_type", "required": True, "ask": "请假类型是？（年假/事假/病假）", "enum": ["年假", "事假", "病假"]},
    {"key": "start_date", "required": True, "ask": "从哪天开始？（如 8月18日）"},
    {"key": "end_date", "required": True, "ask": "到哪天结束？"},
    {"key": "reason", "required": False, "ask": "事由（可不填）"},
]

# 意图 → 槽位定义映射（本次仅请假）
SLOT_DEFS = {"leave": LEAVE_SLOTS}


@dataclass
class SlotState:
    intent: str
    slots: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)


def init_state(intent: str, slots_def: list) -> SlotState:
    missing = [s["key"] for s in slots_def if s["required"]]
    return SlotState(intent=intent, missing=missing)


def apply_extracted(state: SlotState, extracted: dict) -> None:
    for key in list(state.missing):
        if key in extracted and extracted[key]:
            state.slots[key] = extracted[key]
    state.missing = [k for k in state.missing if k not in state.slots]


def next_ask(slots_def: list, missing_key: str) -> str:
    for s in slots_def:
        if s["key"] == missing_key:
            return s["ask"]
    return ""


def confirm_summary(slots_def: list, slots: dict) -> str:
    parts = [f"{s['key']}={slots.get(s['key'], '')}" for s in slots_def if slots.get(s["key"])]
    return "请确认以下信息：\n" + "\n".join(parts) + "\n\n回复「确认」提交，或回复「取消」放弃。"


class SlotFiller:
    """槽位状态机。extractor 可注入，默认走 LLM 提取。"""

    def __init__(self, extractor=None):
        self._sessions: dict[str, SlotState] = {}
        self._pending_confirms: dict[str, dict] = {}
        self._extractor = extractor or _llm_extract_slots

    async def handle(self, session_id: str, user_message: str, intent: str, entity: dict) -> str | dict:
        slots_def = SLOT_DEFS.get(entity.get("type", "")) if entity else None
        if not slots_def:
            return None  # 非槽位场景

        state = self._sessions.get(session_id)
        if state is None:
            state = init_state(intent, slots_def)
            self._sessions[session_id] = state

        extracted = await self._extractor(intent, user_message, state.missing)
        apply_extracted(state, extracted)

        if state.missing:
            return next_ask(slots_def, state.missing[0])

        self._pending_confirms[session_id] = dict(state.slots)
        del self._sessions[session_id]
        return confirm_summary(slots_def, state.slots)

    def consume(self, session_id: str, user_message: str) -> str | dict | None:
        """处理二次确认。返回确认后的 tool dict / 取消文本 / None（当作新消息，丢弃 pending）。"""
        if session_id not in self._pending_confirms:
            return None
        stored_slots = self._pending_confirms.pop(session_id)
        msg = user_message.strip()
        if msg in {"确认", "confirm", "yes", "是", "好的", "可以", "ok"}:
            return {"tool": "feishu_submit_leave_request", "args": stored_slots}
        if msg in {"取消", "cancel", "no", "否", "不要", "算了"}:
            return "已取消操作。"
        return None


async def _llm_extract_slots(intent: str, user_message: str, missing_keys: list) -> dict:
    """LLM 从用户消息中提取槽位值（默认实现，测试中会被 mock 替换）。"""
    from agent.intent import intent_classifier
    client = intent_classifier._ensure()._client
    from config import config
    from agent.prompts import SLOT_EXTRACTION_PROMPT

    prompt = SLOT_EXTRACTION_PROMPT.replace("{user_message}", user_message).replace(
        "{missing_keys}", ", ".join(missing_keys)
    )
    resp = await client.chat.completions.create(
        model=config.llm.chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=256,
    )
    raw = resp.choices[0].message.content or "{}"
    import json, re
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0)) if m else {}
