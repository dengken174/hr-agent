from dataclasses import dataclass, field
from typing import Any

import mcp.types as types

from mcp_servers.hris import server_name

# ── 模拟员工数据 ──────────────────────────────────────────────────────

MOCK_EMPLOYEES: list[dict[str, Any]] = [
    {"id": 1001, "name": "张三", "dept": "技术部", "title": "高级工程师",
     "onboard_date": "2023-06-15", "salary": 28000, "email": "zhangsan@company.com",
     "manager": "李四", "level": "P7"},
    {"id": 1002, "name": "李四", "dept": "技术部", "title": "技术总监",
     "onboard_date": "2020-03-01", "salary": 45000, "email": "lisi@company.com",
     "manager": "王五", "level": "M3"},
    {"id": 1003, "name": "王五", "dept": "技术部", "title": "VP of Engineering",
     "onboard_date": "2018-01-10", "salary": 65000, "email": "wangwu@company.com",
     "manager": "CEO", "level": "M5"},
    {"id": 2001, "name": "赵六", "dept": "人力资源部", "title": "HRBP",
     "onboard_date": "2021-09-01", "salary": 18000, "email": "zhaoliu@company.com",
     "manager": "钱七", "level": "P5"},
    {"id": 2002, "name": "钱七", "dept": "人力资源部", "title": "HR 总监",
     "onboard_date": "2019-05-20", "salary": 35000, "email": "qianqi@company.com",
     "manager": "CEO", "level": "M4"},
    {"id": 3001, "name": "孙八", "dept": "产品部", "title": "产品经理",
     "onboard_date": "2024-02-01", "salary": 22000, "email": "sunba@company.com",
     "manager": "周九", "level": "P6"},
]

MOCK_ORG = {
    "name": "公司总部",
    "children": [
        {"name": "技术部", "head": "李四",
         "children": [
             {"name": "后端组", "head": "张三", "children": []},
             {"name": "前端组", "head": "前端负责人", "children": []},
         ]},
        {"name": "人力资源部", "head": "钱七", "children": []},
        {"name": "产品部", "head": "周九", "children": []},
    ],
}


def _find_emp(identifier: str | int) -> dict | None:
    """按 ID 或姓名查找员工。"""
    for emp in MOCK_EMPLOYEES:
        if isinstance(identifier, int) and emp["id"] == identifier:
            return emp
        if isinstance(identifier, str):
            if emp["name"] == identifier or str(emp["id"]) == identifier:
                return emp
    return None


# ── Tool 实现 ─────────────────────────────────────────────────────────

async def search_employee(keyword: str) -> list[types.TextContent]:
    """按姓名/工号/部门模糊搜索员工。返回不含薪资字段的基本信息。"""
    results = []
    for emp in MOCK_EMPLOYEES:
        if (keyword in emp["name"] or keyword in emp["dept"]
                or str(emp["id"]) == keyword):
            # 非本人查询不返回薪资
            results.append({
                "id": emp["id"], "name": emp["name"], "dept": emp["dept"],
                "title": emp["title"], "onboard_date": emp["onboard_date"],
                "email": emp["email"], "level": emp["level"],
            })
    return [types.TextContent(
        type="text",
        text=str(results) if results else f"未找到匹配 '{keyword}' 的员工",
    )]


async def get_my_profile(employee_id: int) -> list[types.TextContent]:
    """查自己的入职时间、职级、部门等信息。"""
    emp = _find_emp(employee_id)
    if not emp:
        return [types.TextContent(type="text", text=f"未找到员工 ID {employee_id}")]
    return [types.TextContent(type="text", text=str({
        "id": emp["id"], "name": emp["name"], "dept": emp["dept"],
        "title": emp["title"], "onboard_date": emp["onboard_date"],
        "email": emp["email"], "level": emp["level"], "manager": emp["manager"],
    }))]


async def get_my_salary(employee_id: int) -> list[types.TextContent]:
    """查自己的薪资明细、五险一金。仅返回本人薪资。"""
    emp = _find_emp(employee_id)
    if not emp:
        return [types.TextContent(type="text", text=f"未找到员工 ID {employee_id}")]
    return [types.TextContent(type="text", text=str({
        "salary": emp["salary"],
        "housing_fund": round(emp["salary"] * 0.12, 2),
        "pension": round(emp["salary"] * 0.08, 2),
        "medical": round(emp["salary"] * 0.02, 2),
        "unemployment": round(emp["salary"] * 0.005, 2),
    }))]


async def get_team_members(dept: str) -> list[types.TextContent]:
    """HR 查看管辖范围内的员工列表。"""
    members = [{
        "id": e["id"], "name": e["name"], "title": e["title"],
        "onboard_date": e["onboard_date"], "level": e["level"],
    } for e in MOCK_EMPLOYEES if dept in e["dept"]]
    return [types.TextContent(type="text", text=str(members))]


async def get_org_structure() -> list[types.TextContent]:
    """查询组织架构树。"""
    return [types.TextContent(type="text", text=str(MOCK_ORG))]


async def get_onboarding_info() -> list[types.TextContent]:
    """查询新员工入职指南、流程节点。"""
    guide = {
        "入职前": ["收到 offer → 确认入职 → 背景调查 → 体检"],
        "报到当天": ["9:00 HR 报到 → 签合同 → 领设备 → 开通账号 → 入职培训"],
        "第一周": ["部门 welcome meeting → 导师分配 → 系统培训"],
        "试用期": "6 个月，2 次转正评估",
        "需带材料": ["身份证原件", "学历学位证", "离职证明", "银行卡"],
    }
    return [types.TextContent(type="text", text=str(guide))]


# ── Tool 注册表 ───────────────────────────────────────────────────────

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


HRIS_TOOLS = [
    ToolDef(
        name="search_employee",
        description="按姓名/工号/部门模糊搜索【其他员工】的基本信息（不含薪资）。用于查【别人】的入职时间、部门、职级，如\"张三在哪个部门\"。区别于 get_my_profile（查自己）。仅 HR 可用。",
        input_schema={
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "姓名/工号/部门关键词"}},
            "required": ["keyword"],
        },
        handler=search_employee,
    ),
    ToolDef(
        name="get_my_profile",
        description="查询【自己】的入职时间、职级、部门、联系方式。用于\"我的入职时间/我的部门/我的职级\"等本人档案查询。区别于 search_employee（查别人）。",
        input_schema={
            "type": "object",
            "properties": {"employee_id": {"type": "integer", "description": "员工 ID（即本人 user_id）"}},
            "required": ["employee_id"],
        },
        handler=get_my_profile,
    ),
    ToolDef(
        name="get_my_salary",
        description="查询【自己】的薪资明细和五险一金。用于\"我工资多少/我的五险一金交多少\"等薪资查询。仅返回本人薪资，禁止查询他人。区别于 get_my_profile（非薪资信息）。",
        input_schema={
            "type": "object",
            "properties": {"employee_id": {"type": "integer", "description": "员工 ID（即本人 user_id）"}},
            "required": ["employee_id"],
        },
        handler=get_my_salary,
    ),
    ToolDef(
        name="get_team_members",
        description="查看【某部门】的员工列表（姓名/职级/入职时间）。用于\"技术部有哪些人/XX部门多少人\"。区别于 get_org_structure（组织架构层级树）。仅 HR 可用。",
        input_schema={
            "type": "object",
            "properties": {"dept": {"type": "string", "description": "部门名称，如\"技术部\""}},
            "required": ["dept"],
        },
        handler=get_team_members,
    ),
    ToolDef(
        name="get_org_structure",
        description="查询公司【组织架构树】（部门层级、负责人）。用于\"公司组织架构/部门结构/谁是部门负责人\"。区别于 get_team_members（某部门成员明细列表）。",
        input_schema={"type": "object", "properties": {}},
        handler=get_org_structure,
    ),
    ToolDef(
        name="get_onboarding_info",
        description="查询【新员工入职】指南、报到流程、需带材料。用于\"入职要带什么/入职流程/报到安排\"。区别于 get_interview_guide（面试流程）。",
        input_schema={"type": "object", "properties": {}},
        handler=get_onboarding_info,
    ),
]
