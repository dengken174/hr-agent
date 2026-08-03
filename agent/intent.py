import json
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from config import config
from agent.prompts import INTENT_CLASSIFICATION_PROMPT


@dataclass
class IntentResult:
    intent: str
    entity: dict = field(default_factory=dict)


INTENT_LABELS = frozenset({
    "search_own_info",
    "search_others_info",
    "policy_query",
    "process_query",
    "start_operation",
    "approval_action",
    "general_chat",
})


class IntentClassifier:
    """使用 DeepSeek few-shot 做意图分类，返回 intent + entity。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key or config.llm.api_key,
            base_url=base_url or config.llm.base_url,
        )
        self._model = config.llm.chat_model

    async def classify(self, user_message: str) -> IntentResult:
        prompt = INTENT_CLASSIFICATION_PROMPT.replace("{user_message}", user_message)

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )

        raw = response.choices[0].message.content or "{}"
        parsed = self._extract_json(raw)
        intent = parsed.get("intent", "general_chat")
        entity = parsed.get("entity", {})

        if intent not in INTENT_LABELS:
            intent = "general_chat"
            entity = {}

        return IntentResult(intent=intent, entity=entity)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        """从 LLM 回复中提取 JSON，兼容 markdown code block。"""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


intent_classifier = IntentClassifier()
