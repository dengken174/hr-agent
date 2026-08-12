from typing import Sequence

from langchain_core.tools import BaseTool

from agent.intent import IntentResult

# ── 意图 → Tool 名称映射表 ───────────────────────────────────────────
# tool_name_prefix=True → 格式: server_toolname (DeepSeek API 兼容)

INTENT_TOOL_MAP: dict[str, list[str]] = {
    "search_own_info": [
        "hris_get_my_profile",
        "hris_get_my_salary",
        "feishu_get_my_attendance",
        "feishu_get_leave_balance",
        "feishu_query_calendar",
        "feishu_list_tasks",
    ],
    "search_others_info": [
        "hris_search_employee",
        "hris_get_team_members",
        "hris_get_org_structure",
        "feishu_read_sheet",
    ],
    "policy_query": [
        "knowledge_search_knowledge_base",
        "knowledge_get_benefit_policy",
        "knowledge_get_company_intro",
        "feishu_search_docs",
        "feishu_read_doc",
    ],
    "process_query": [
        "knowledge_search_knowledge_base",
        "knowledge_get_benefit_application_flow",
        "knowledge_get_interview_guide",
        "knowledge_get_onboarding_info",
        "feishu_read_doc",
    ],
    "start_operation": [
        "feishu_submit_leave_request",
        "feishu_create_doc",
        "feishu_create_calendar_event",
        "feishu_create_task",
        "feishu_send_feishu_mail",
        "feishu_write_sheet",
        "approval_start_approval",
    ],
    "approval_action": [
        "approval_query_my_approvals",
        "approval_query_pending_approvals",
        "approval_approve_request",
        "approval_reject_request",
        "approval_get_approval_detail",
    ],
    "general_chat": [],
}

# ── Intent + Entity.type → 更窄的 Tool 子集（entity-aware 二级路由）────

ENTITY_TOOL_MAP: dict[str, dict[str, list[str]]] = {
    "search_own_info": {
        "salary": ["hris_get_my_salary"],
        "attendance": ["feishu_get_my_attendance"],
        "leave_balance": ["feishu_get_leave_balance"],
        "calendar": ["feishu_query_calendar"],
        "task": ["feishu_list_tasks"],
        "profile": ["hris_get_my_profile"],
    },
    "search_others_info": {
        "employee": ["hris_search_employee"],
        "team": ["hris_get_team_members"],
        "org": ["hris_get_org_structure"],
        "sheet": ["feishu_read_sheet"],
    },
    "policy_query": {
        "benefit": ["knowledge_get_benefit_policy"],
        "company": ["knowledge_get_company_intro"],
        "doc": ["feishu_search_docs", "feishu_read_doc"],
    },
    "process_query": {
        "leave": ["knowledge_get_benefit_application_flow"],
        "interview": ["knowledge_get_interview_guide"],
        "onboarding": ["knowledge_get_onboarding_info"],
    },
    "start_operation": {
        "leave": ["feishu_submit_leave_request"],
        "doc": ["feishu_create_doc"],
        "calendar": ["feishu_create_calendar_event"],
        "task": ["feishu_create_task"],
        "mail": ["feishu_send_feishu_mail"],
        "sheet": ["feishu_write_sheet"],
        "approval": ["approval_start_approval"],
    },
    "approval_action": {
        "approve": ["approval_approve_request"],
        "reject": ["approval_reject_request"],
        "query_my": ["approval_query_my_approvals"],
        "query_pending": ["approval_query_pending_approvals"],
        "detail": ["approval_get_approval_detail"],
    },
}


def _entity_narrow_tools(intent: str, entity_type: str) -> list[str] | None:
    """根据 entity.type 进一步缩小工具范围。返回 None 表示保持全量意图工具集。"""
    if not entity_type:
        return None
    entity_map = ENTITY_TOOL_MAP.get(intent)
    if entity_map is None:
        return None
    return entity_map.get(entity_type)


def route_tools(intent: IntentResult, all_tools: Sequence[BaseTool]) -> list[BaseTool]:
    """根据意图+entity 二级路由过滤 Tool 子集。

    general_chat 返回空列表，不需要 Tool。
    未知 intent 返回空列表兜底（fail closed）。
    entity.type 命中 ENTITY_TOOL_MAP 时进一步缩小范围；未命中保持全量意图工具集。
    """
    target_names = INTENT_TOOL_MAP.get(intent.intent)
    if target_names is None:
        return []

    if not target_names:
        return []

    # Entity-aware narrowing: 用 entity.type 进一步过滤
    entity_type = intent.entity.get("type", "") if intent.entity else ""
    narrowed = _entity_narrow_tools(intent.intent, entity_type)
    if narrowed:
        name_set = set(narrowed)
        return [t for t in all_tools if t.name in name_set]

    name_set = set(target_names)
    return [t for t in all_tools if t.name in name_set]


def route_tools_with_semantic(
    intent: IntentResult,
    all_tools: Sequence[BaseTool],
    semantic_top_k: int = 0,
) -> list[BaseTool]:
    """两阶段融合路由：硬路由 + 语义路由 union。"""
    hard_tools = route_tools(intent, all_tools)
    if semantic_top_k <= 0:
        return hard_tools

    semantic_tools: list[BaseTool] = []

    seen = {t.name for t in hard_tools}
    for t in semantic_tools:
        if t.name not in seen:
            hard_tools.append(t)
            seen.add(t.name)
    return hard_tools
