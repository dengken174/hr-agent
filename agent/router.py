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


def route_tools(intent: IntentResult, all_tools: Sequence[BaseTool]) -> list[BaseTool]:
    """根据意图分类结果过滤 Tool 子集。

    general_chat 返回空列表，不需要 Tool。
    未知 intent 返回全量 tools 兜底。
    """
    target_names = INTENT_TOOL_MAP.get(intent.intent)
    if target_names is None:
        # Fail closed: unknown intent = general_chat (no tools, safest path)
        return []

    if not target_names:
        return []

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
