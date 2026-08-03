"""飞书真实 API 全量测试。"""

import os, re, asyncio

os.environ.setdefault("FEISHU_APP_ID", "cli_xxx")
os.environ.setdefault("FEISHU_APP_SECRET", "xxx")

OPEN_ID = "ou_xxx"

from mcp_servers.feishu.client import feishu_client
from mcp_servers.feishu.tools import (
    create_doc, read_doc, search_docs,
    create_calendar_event, query_calendar, cancel_calendar_event,
    create_task, list_tasks, complete_task,
    send_notification,
)

PASSED = 0; FAILED = 0

def check(name, text):
    global PASSED, FAILED
    is_err = ("status': 'error'" in text or '"status": "error"' in text
              or (text.startswith("{") and "'error'" in text[:80]))
    ok = not is_err
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"        {text[:180]}")
    print()
    if ok: PASSED += 1
    else: FAILED += 1
    return text

def extract_id(text, key):
    m = re.search(r"['\"]" + key + r"['\"]\s*[:=]\s*['\"]?([a-zA-Z0-9_-]+)", text)
    return m.group(1) if m else ""


async def main():
    global PASSED, FAILED

    print("=" * 55)
    print(f"  飞书真实 API 全量测试")
    print(f"  open_id: {OPEN_ID}")
    print("=" * 55)

    # --- 文档 ---
    print("\n--- 文档 (docx) ---")
    r = await create_doc("HR测试-终验", "# 标题\n测试内容段落")
    text = check("create_doc", r[0].text)
    doc_id = extract_id(text, "doc_id")

    r = await read_doc(doc_id or "BgnGdc0j4ozv40xsx6TcnKKenzb")
    check("read_doc", r[0].text)

    r = await search_docs("入职")
    check("search_docs", r[0].text)

    # --- 日历 ---
    print("--- 日历 (calendar) ---")
    r = await create_calendar_event(
        "面试-终验", "2026-07-26T10:00:00+08:00",
        "2026-07-26T11:00:00+08:00", [OPEN_ID], "技术终面")
    text = check("create_calendar_event", r[0].text)
    event_id = extract_id(text, "event_id")

    r = await query_calendar("2026-07-20", "2026-07-30")
    check("query_calendar", r[0].text)

    if event_id:
        r = await cancel_calendar_event(event_id)
        check("cancel_calendar_event", r[0].text)

    # --- 任务 ---
    print("--- 任务 (task) ---")
    r = await create_task("入职checklist-终验", OPEN_ID, "2026-07-30", "合同签署和IT设备")
    text = check("create_task", r[0].text)
    task_id = extract_id(text, "task_id")

    r = await list_tasks(assignee=OPEN_ID)
    check("list_tasks", r[0].text)

    if task_id:
        r = await complete_task(task_id)
        check("complete_task", r[0].text)

    # --- 消息 ---
    print("--- 消息 (im) ---")
    r = await send_notification(OPEN_ID, "HR Agent API 测试消息")
    check("send_notification", r[0].text)

    # --- 结果 ---
    total = PASSED + FAILED
    print("=" * 55)
    print(f"  结果: {PASSED}/{total} 通过" + (" - 全部通过!" if FAILED == 0 else f"  {FAILED} 失败"))
    print("=" * 55)

    await feishu_client.close()


if __name__ == "__main__":
    asyncio.run(main())
