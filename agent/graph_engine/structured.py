import re
from agent.intent import INTENT_LABELS, IntentResult


def parse_object_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    import json
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def sanitize_intent(parsed: dict) -> IntentResult:
    intent = parsed.get("intent", "general_chat")
    entity = parsed.get("entity", {})
    if not isinstance(entity, dict):
        entity = {}
    if intent not in INTENT_LABELS:
        intent = "general_chat"
        entity = {}
    try:
        conf = float(parsed.get("confidence", 1.0))
    except (TypeError, ValueError):
        conf = 1.0
    conf = min(1.0, max(0.0, conf))
    return IntentResult(intent=intent, entity=entity, confidence=conf)


def filter_slots(raw: dict, slots_def: list) -> dict:
    out = {}
    for d in slots_def:
        key = d["key"]
        val = raw.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        if d.get("enum") and val not in d["enum"]:
            continue
        out[key] = val
    return out


def parse_entity_type(parsed: dict, intent: str, allowed: set[str]) -> str | None:
    etype = (parsed.get("entity") or {}).get("type")
    if intent and etype in allowed:
        return etype
    return None
