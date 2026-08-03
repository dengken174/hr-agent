"""飞书 CLI 工具直接验证 — 不经过 MCP 协议，直接调用 tool 函数。

验证所有飞书 CLI 工具（文档/日历/表格/邮件/任务）的 mock 实现逻辑。
生产环境需要设置 FEISHU_APP_ID + FEISHU_APP_SECRET 环境变量后测试真实 API。
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


async def test_all():
    from mcp_servers.feishu.tools import (
        # 基础通讯
        get_my_attendance, get_leave_balance, submit_leave_request,
        get_holiday_calendar, send_notification,
        # 文档
        create_doc, read_doc, search_docs,
        # 日历
        create_calendar_event, query_calendar, cancel_calendar_event,
        # 表格
        read_sheet, write_sheet,
        # 邮件
        send_feishu_mail,
        # 任务
        create_task, list_tasks, complete_task,
    )

    passed = 0
    failed = 0

    def check(name, result):
        nonlocal passed, failed
        text = result[0].text
        ok = "error" not in text.lower() and "不存在" not in text
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {text[:120]}...")
        if not ok:
            failed += 1
        else:
            passed += 1
        print()
        return ok

    print("=" * 55)
    print("  飞书 CLI 工具直接验证")
    print("=" * 55)

    # ── I. 基础通讯 ──
    print("\n--- I. 基础通讯 (5 tools) ---\n")
    check("get_my_attendance", await get_my_attendance(1001))
    check("get_leave_balance", await get_leave_balance(1001))
    check("get_holiday_calendar", await get_holiday_calendar())
    check("submit_leave_request", await submit_leave_request(
        1001, "年假", "2026-07-20", "2026-07-21", "测试"))
    check("send_notification", await send_notification(1002, "测试通知"))

    # ── II. 文档 ──
    print("--- II. 飞书 CLI: 文档 (3 tools) ---\n")
    check("create_doc", await create_doc("测试文档", "# 测试内容"))
    check("search_docs", await search_docs("入职"))
    check("read_doc", await read_doc("doc_001"))

    # ── III. 日历 ──
    print("--- III. 飞书 CLI: 日历 (3 tools) ---\n")
    r = await create_calendar_event(
        "面试-测试", "2026-07-25T10:00", "2026-07-25T11:00",
        ["面试官A", "候选人B"], "技术终面")
    check("create_calendar_event", r)
    check("query_calendar", await query_calendar("2026-07-20", "2026-07-30"))
    check("cancel_calendar_event", await cancel_calendar_event("evt_001"))

    # ── IV. 表格 ──
    print("--- IV. 飞书 CLI: 表格 (2 tools) ---\n")
    check("read_sheet", await read_sheet("sheet_attendance"))
    check("write_sheet", await write_sheet("sheet_attendance",
        [["测试", "2026-07-18", "09:00", "正常"]]))

    # ── V. 邮件 ──
    print("--- V. 飞书 CLI: 邮件 (1 tool) ---\n")
    check("send_feishu_mail", await send_feishu_mail(
        "hr@company.com", "测试邮件", "这是测试内容"))

    # ── VI. 任务 ──
    print("--- VI. 飞书 CLI: 任务 (3 tools) ---\n")
    check("create_task", await create_task(
        "测试任务", "张三", "2026-07-25", "任务描述"))
    check("list_tasks", await list_tasks(assignee="张三"))
    check("complete_task", await complete_task("task_001"))

    # ── 结果 ──
    print("=" * 55)
    total = passed + failed
    print(f"  结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" - 全部通过")
    print("=" * 55)

    return failed == 0


async def test_production_readiness():
    """检查生产环境配置是否就绪。"""
    from config import config
    print("\n--- 生产环境就绪检查 ---\n")
    checks = [
        ("FEISHU_APP_ID", bool(config.feishu.app_id)),
        ("FEISHU_APP_SECRET", bool(config.feishu.app_secret)),
        ("FEISHU_VERIFICATION_TOKEN", bool(config.feishu.verification_token)),
    ]
    for name, ok in checks:
        status = "已配置" if ok else "未配置 (mock 模式)"
        print(f"  {name}: {status}")

    if not config.feishu.app_id:
        print("\n  提示: 设置环境变量后即可对接真实飞书 API:")
        print("    export FEISHU_APP_ID=cli_xxx")
        print("    export FEISHU_APP_SECRET=xxx")
        print("    export FEISHU_VERIFICATION_TOKEN=xxx")
    else:
        print("\n  生产环境配置就绪，可对接真实飞书 API。")


if __name__ == "__main__":
    asyncio.run(test_all())
    asyncio.run(test_production_readiness())
