from dataclasses import dataclass
from typing import Any

import mcp.types as types

from mcp_servers.knowledge.retriever import hybrid_retriever
from mcp_servers.knowledge import server_name


# ── Tool 实现 ──────────────────────────────────────────────────────────

async def search_knowledge_base(query: str) -> list[types.TextContent]:
    """通用混合检索公司制度文档。"""
    results = await hybrid_retriever.search(query)
    if not results:
        return [types.TextContent(type="text", text=f"未找到与 '{query}' 相关的制度文档")]
    formatted = [{
        "title": r["metadata"].get("title", ""),
        "content": r["content"],
        "relevance": round(r.get("rrf_score", r.get("score", 0)), 3),
    } for r in results]
    return [types.TextContent(type="text", text=str(formatted))]


async def get_benefit_policy(topic: str = "") -> list[types.TextContent]:
    """查询具体福利政策。"""
    topics = ["五险一金", "补充商业", "餐补", "交通补贴", "住房补贴", "年假", "培训", "福利"]
    if topic:
        topics = [topic]
    results = []
    for t in topics:
        hits = await hybrid_retriever.search(t)
        results.extend(hits)
    # 去重
    seen = set()
    unique = []
    for r in results:
        title = r["metadata"].get("title", "")
        if title not in seen:
            seen.add(title)
            unique.append({"title": title, "content": r["content"]})
    return [types.TextContent(type="text", text=str(unique))]


async def get_benefit_application_flow(benefit_type: str = "") -> list[types.TextContent]:
    """查询福利申请流程。"""
    flow = {
        "住房补贴": "OA 提交申请 → 上传租房合同 → 直属上级审批 → HR 核实 → 次月生效",
        "餐补": "无需申请，随工资自动发放",
        "交通补贴": "无需申请，随工资自动发放",
        "培训报销": "OA 提交申请 → 上传发票/证书 → 上级审批 → 财务审核 → 3个工作日内到账",
        "补充医疗报销": "保险公司 APP 提交 → 上传发票/病历 → 审核 → 5个工作日内到账",
        "年假": "飞书/钉钉提交请假申请 → 选择年假类型 → 上级审批 → 自动扣减余额",
    }
    if benefit_type and benefit_type in flow:
        return [types.TextContent(type="text", text=str({benefit_type: flow[benefit_type]}))]
    return [types.TextContent(type="text", text=str(flow))]


async def get_interview_guide() -> list[types.TextContent]:
    """面试指南和流程。"""
    guide = {
        "面试流程": "简历筛选 → 笔试/作品集 → 技术面(2轮) → HR面 → 终面，全程约2-3周",
        "面试形式": "支持线上面试（腾讯会议/飞书），也可线下到公司面试",
        "技术面重点": "算法与数据结构、系统设计、项目经验、代码能力",
        "准备建议": "复习计算机基础、熟悉公司产品、准备项目介绍、准备几个问题问面试官",
        "面试结果反馈": "每轮面试后3个工作日内反馈结果",
    }
    return [types.TextContent(type="text", text=str(guide))]


async def get_company_intro() -> list[types.TextContent]:
    """公司介绍、文化、发展历程。"""
    intro = {
        "成立": "2015年",
        "总部": "北京",
        "分公司": "上海、深圳、成都",
        "主营业务": "人工智能、企业服务 SaaS",
        "融资阶段": "C轮",
        "员工规模": "3000+",
        "核心价值观": "客户第一、创新驱动、团队协作、诚信正直",
        "特色活动": "每周五 Learning Hour、每月全员 Town Hall",
    }
    return [types.TextContent(type="text", text=str(intro))]


# ── Tool 注册表 ────────────────────────────────────────────────────────

@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Any

    def to_mcp_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
        )


KNOWLEDGE_TOOLS = [
    ToolDef(
        name="search_knowledge_base",
        description="通用混合检索公司制度文档（制度、政策、流程）",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索查询"}},
            "required": ["query"],
        },
        handler=search_knowledge_base,
    ),
    ToolDef(
        name="get_benefit_policy",
        description="查询具体福利政策（五险一金比例、补充医疗、餐补、房补等）",
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "福利主题关键词（选填）"}},
        },
        handler=get_benefit_policy,
    ),
    ToolDef(
        name="get_benefit_application_flow",
        description="查询福利申请流程",
        input_schema={
            "type": "object",
            "properties": {"benefit_type": {"type": "string", "description": "福利类型（选填）"}},
        },
        handler=get_benefit_application_flow,
    ),
    ToolDef(
        name="get_interview_guide",
        description="面试指南和面试流程说明",
        input_schema={"type": "object", "properties": {}},
        handler=get_interview_guide,
    ),
    ToolDef(
        name="get_company_intro",
        description="公司介绍、文化、发展历程",
        input_schema={"type": "object", "properties": {}},
        handler=get_company_intro,
    ),
]
