"""MCP Server 独立测试工具 — 不依赖 LangChain，直接通过 stdio 调用 MCP Server。"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


async def test_feishu():
    """测试飞书 MCP Server 的全部 Tools (基础通讯 + CLI 文档/日历/表格/邮件/任务)。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_servers.feishu.server"],
    )

    print("=" * 50)
    print("  飞书 MCP Server 测试 (Channel SDK + CLI)")
    print("=" * 50)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"\n已注册 {len(tools.tools)} 个 Tools:\n")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")

            # ── 基础通讯 ──
            print("\n" + "=" * 40)
            print("  I. 基础通讯")
            print("=" * 40)

            print("\n[通讯] get_my_attendance (employee_id=1001)")
            result = await session.call_tool("get_my_attendance", {"employee_id": 1001})
            print(f"  OK: {result.content[0].text[:100]}...")

            print("\n[通讯] get_leave_balance (employee_id=1001)")
            result = await session.call_tool("get_leave_balance", {"employee_id": 1001})
            print(f"  OK: {result.content[0].text[:100]}...")

            print("\n[通讯] get_holiday_calendar")
            result = await session.call_tool("get_holiday_calendar", {})
            print(f"  OK: {result.content[0].text[:100]}...")

            print("\n[通讯] submit_leave_request")
            result = await session.call_tool("submit_leave_request", {
                "employee_id": 1001, "leave_type": "年假",
                "start_date": "2026-07-20", "end_date": "2026-07-21", "reason": "家人来访",
            })
            print(f"  OK: {result.content[0].text[:120]}...")

            print("\n[通讯] send_notification (to 1002)")
            result = await session.call_tool("send_notification", {
                "target_employee_id": 1002, "message": "您的请假审批已通过",
            })
            print(f"  OK: {result.content[0].text[:80]}...")

            # ── CLI: 文档 ──
            print("\n" + "=" * 40)
            print("  II. 飞书 CLI — 文档")
            print("=" * 40)

            print("\n[文档] create_doc")
            result = await session.call_tool("create_doc", {
                "title": "入职指南-2026", "content": "# 入职指南\n\n欢迎加入！",
            })
            print(f"  OK: {result.content[0].text[:150]}...")

            print("\n[文档] search_docs (query='入职')")
            result = await session.call_tool("search_docs", {"query": "入职"})
            print(f"  OK: {result.content[0].text[:120]}...")

            print("\n[文档] read_doc (doc_001)")
            result = await session.call_tool("read_doc", {"doc_id": "doc_001"})
            print(f"  OK: {result.content[0].text[:80]}...")

            # ── CLI: 日历 ──
            print("\n" + "=" * 40)
            print("  III. 飞书 CLI — 日历")
            print("=" * 40)

            print("\n[日历] create_calendar_event")
            result = await session.call_tool("create_calendar_event", {
                "title": "面试-高级工程师-王五",
                "start_time": "2026-07-25T10:00:00",
                "end_time": "2026-07-25T11:00:00",
                "attendees": ["面试官A", "王五"],
                "description": "技术面第三轮",
            })
            print(f"  OK: {result.content[0].text[:150]}...")

            print("\n[日历] query_calendar (2026-07-22 ~ 2026-07-26)")
            result = await session.call_tool("query_calendar", {
                "start_date": "2026-07-22", "end_date": "2026-07-26",
            })
            print(f"  OK: {result.content[0].text[:120]}...")

            # ── CLI: 表格 ──
            print("\n" + "=" * 40)
            print("  IV. 飞书 CLI — 表格")
            print("=" * 40)

            print("\n[表格] read_sheet (sheet_attendance)")
            result = await session.call_tool("read_sheet", {"sheet_id": "sheet_attendance"})
            print(f"  OK: {result.content[0].text[:120]}...")

            print("\n[表格] write_sheet (sheet_attendance)")
            result = await session.call_tool("write_sheet", {
                "sheet_id": "sheet_attendance",
                "values": [["张三", "2026-07-18", "08:55", "正常"]],
            })
            print(f"  OK: {result.content[0].text[:80]}...")

            # ── CLI: 邮件 ──
            print("\n" + "=" * 40)
            print("  V. 飞书 CLI — 邮件")
            print("=" * 40)

            print("\n[邮件] send_feishu_mail")
            result = await session.call_tool("send_feishu_mail", {
                "to_email": "candidate@example.com",
                "subject": "录用通知书 - 高级工程师",
                "body": "恭喜您通过面试...",
            })
            print(f"  OK: {result.content[0].text[:120]}...")

            # ── CLI: 任务 ──
            print("\n" + "=" * 40)
            print("  VI. 飞书 CLI — 任务")
            print("=" * 40)

            print("\n[任务] create_task")
            result = await session.call_tool("create_task", {
                "title": "完成入职手续", "assignee": "张三",
                "due": "2026-07-25", "description": "包括合同签署、IT设备领取",
            })
            print(f"  OK: {result.content[0].text[:150]}...")

            print("\n[任务] list_tasks (assignee=张三)")
            result = await session.call_tool("list_tasks", {"assignee": "张三"})
            print(f"  OK: {result.content[0].text[:120]}...")

            print("\n[任务] complete_task (task_001)")
            result = await session.call_tool("complete_task", {"task_id": "task_001"})
            print(f"  OK: {result.content[0].text[:80]}...")

            print("\n" + "=" * 50)
            print("  飞书 MCP Server 全部测试通过！(基础 5 + 文档 3 + 日历 2 + 表格 2 + 邮件 1 + 任务 3)")
            print("=" * 50)


async def test_hris():
    """测试 HRIS MCP Server。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_servers.hris.server"],
    )

    print("=" * 50)
    print("  HRIS MCP Server 测试")
    print("=" * 50)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"\n已注册 {len(tools.tools)} 个 Tools\n")

            # 测试 search_employee
            print("测试: search_employee (keyword='张')")
            result = await session.call_tool("search_employee", {"keyword": "张"})
            print(f"  → {result.content[0].text}\n")

            # 测试 get_my_salary
            print("测试: get_my_salary (employee_id=1001)")
            result = await session.call_tool("get_my_salary", {"employee_id": 1001})
            print(f"  → {result.content[0].text}\n")

            # 测试 get_org_structure
            print("测试: get_org_structure")
            result = await session.call_tool("get_org_structure", {})
            print(f"  → {result.content[0].text}\n")

            print("全部测试通过！")


async def test_approval():
    """测试审批 MCP Server。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_servers.approval.server"],
    )

    print("=" * 50)
    print("  审批工作流 MCP Server 测试")
    print("=" * 50)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"\n已注册 {len(tools.tools)} 个 Tools\n")

            # 发起审批
            print("测试: start_approval (张三请年假)")
            result = await session.call_tool("start_approval", {
                "applicant_id": 1001,
                "req_type": "leave",
                "title": "张三申请年假 2026-07-20 ~ 2026-07-21",
                "body": {"leave_type": "年假", "start_date": "2026-07-20", "end_date": "2026-07-21"},
                "assignee_id": 1002,
            })
            resp = json.loads(result.content[0].text.replace("'", '"'))
            req_id = resp["request_id"]
            print(f"  → {json.dumps(resp, ensure_ascii=False)}\n")

            # 查询待审批
            print("测试: query_pending_approvals (审批人=1002)")
            result = await session.call_tool("query_pending_approvals", {"assignee_id": 1002})
            print(f"  → {result.content[0].text}\n")

            # 审批通过
            print(f"测试: approve_request (id={req_id})")
            result = await session.call_tool("approve_request", {
                "request_id": req_id,
                "operator_id": 1002,
                "comment": "同意",
            })
            print(f"  → {result.content[0].text}\n")

            # 查看详情
            print(f"测试: get_approval_detail (id={req_id})")
            result = await session.call_tool("get_approval_detail", {"request_id": req_id})
            print(f"  → {result.content[0].text}\n")

            print("全部测试通过！")


async def test_knowledge():
    """测试知识库 MCP Server。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_servers.knowledge.server"],
        env={"PYTHONPATH": os.path.dirname(__file__), "HF_ENDPOINT": "https://hf-mirror.com",
             **{k: v for k, v in os.environ.items() if k in ("DEEPSEEK_API_KEY",)}},
    )

    print("=" * 50)
    print("  知识库 RAG MCP Server 测试")
    print("=" * 50)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"\n已注册 {len(tools.tools)} 个 Tools\n")

            # 测试混合检索
            print("测试: search_knowledge_base ('五险一金')")
            result = await session.call_tool("search_knowledge_base", {"query": "五险一金"})
            print(f"  → {result.content[0].text}\n")

            # 测试公司介绍
            print("测试: get_company_intro")
            result = await session.call_tool("get_company_intro", {})
            print(f"  → {result.content[0].text}\n")

            # 测试面试指南
            print("测试: get_interview_guide")
            result = await session.call_tool("get_interview_guide", {})
            print(f"  → {result.content[0].text}\n")

            print("全部测试通过！")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="MCP Server 测试工具")
    parser.add_argument("server", nargs="?", default="feishu",
                        choices=["feishu", "hris", "approval", "knowledge", "all"],
                        help="要测试的 MCP Server")
    args = parser.parse_args()

    if args.server == "all":
        await test_hris()
        await test_feishu()
        await test_approval()
        await test_knowledge()
    elif args.server == "feishu":
        await test_feishu()
    elif args.server == "hris":
        await test_hris()
    elif args.server == "approval":
        await test_approval()
    elif args.server == "knowledge":
        await test_knowledge()


if __name__ == "__main__":
    asyncio.run(main())
