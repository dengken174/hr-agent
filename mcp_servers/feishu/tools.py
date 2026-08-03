"""飞书 MCP Server — Tools 实现（真实 API + Mock 降级）。

当 FEISHU_APP_ID + FEISHU_APP_SECRET 配置时调用飞书开放平台 API，
否则降级为 mock 数据。
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import mcp.types as types

from mcp_servers.feishu import server_name
from mcp_servers.feishu.client import feishu_client, FeishuAPIError
from config import config

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 实用工具
# ═══════════════════════════════════════════════════════════════════════

def _api_or_mock(use_api: bool) -> str:
    return "api" if use_api else "mock"


def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


# ═══════════════════════════════════════════════════════════════════════
# I. 基础通讯
# ═══════════════════════════════════════════════════════════════════════

async def send_notification(user_id: str, message: str) -> list[types.TextContent]:
    """给指定用户发送飞书消息。

    Feishu API: POST /im/v1/messages?receive_id_type=open_id
    用户需在飞书 APP 中开通"获取用户 ID"权限，user_id 使用 open_id。
    """
    if not feishu_client.is_configured:
        return _text(f"[mock] 已向用户 {user_id} 发送通知：{message}")

    try:
        content = '{"text":"' + message.replace('"', '\\"').replace('\n', '\\n') + '"}'
        data = await feishu_client.post(
            "/im/v1/messages",
            params={"receive_id_type": "open_id"},
            body={
                "receive_id": user_id,
                "msg_type": "text",
                "content": content,
            },
        )
        return _text(str({"status": "sent", "message_id": data.get("data", {}).get("message_id")}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def get_my_attendance(employee_id: int, month: str | None = None) -> list[types.TextContent]:
    """查询打卡记录。

    注意: 飞书 attendance API 需要额外权限审批，当前降级为 mock。
    生产环境接入: POST /attendance/v1/user_stats_fields/query
    """
    records = _mock_attendance(employee_id)
    today = date.today()
    target = month or f"{today.year}-{today.month:02d}"
    filtered = [r for r in records if r["date"].startswith(target)]
    return _text(str(filtered or f"{target} 无打卡记录"))


async def get_leave_balance(employee_id: int) -> list[types.TextContent]:
    """查询假期余额。

    注意: 假期余额依赖飞书审批-假期插件，需在管理后台配置假期类型。
    当前降级为 mock。
    """
    balance = _mock_leave_balance(employee_id)
    if not balance:
        return _text("未找到假期余额信息")
    return _text(str(balance))


async def submit_leave_request(
    employee_id: str, leave_type: str,
    start_date: str, end_date: str, reason: str = "",
) -> list[types.TextContent]:
    """发起请假申请。

    Feishu API: POST /approval/v4/instances
    需先在飞书管理后台创建审批模板，获取 approval_code 后配置到环境变量。
    """
    approval_code = config.feishu.approval_code
    if not feishu_client.is_configured or not approval_code:
        return _text(str({
            "status": "submitted", "employee_id": employee_id,
            "leave_type": leave_type, "start_date": start_date, "end_date": end_date,
            "reason": reason, "mode": "mock",
            "message": f"[mock] 请假申请已提交。{start_date} ~ {end_date}，类型：{leave_type}。"
                       f"配置 FEISHU_APPROVAL_CODE 后启用真实审批。",
        }))

    try:
        data = await feishu_client.post(
            "/approval/v4/instances",
            body={
                "approval_code": approval_code,
                "user_id": employee_id,
                "form": json.dumps([
                    {"id": "leave_type", "type": "input", "value": leave_type},
                    {"id": "start_date", "type": "input", "value": start_date},
                    {"id": "end_date", "type": "input", "value": end_date},
                    {"id": "reason", "type": "textarea", "value": reason},
                ]),
            },
        )
        return _text(str({"status": "submitted", "instance_code": data.get("data", {}).get("instance_code")}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def get_holiday_calendar(year: int = 2026) -> list[types.TextContent]:
    """查询公司放假安排。需在飞书日历中创建节假日日历后通过 calendar API 查询。"""
    if not feishu_client.is_configured:
        return _text(str(_mock_holidays()))

    try:
        cal_id = config.feishu.holiday_calendar_id or config.feishu.primary_calendar_id
        start_of_year = f"{year}-01-01T00:00:00+08:00"
        end_of_year = f"{year}-12-31T23:59:59+08:00"
        data = await feishu_client.get(
            f"/calendar/v4/calendars/{cal_id}/events",
            params={"start_time": start_of_year, "end_time": end_of_year, "page_size": 50},
        )
        items = data.get("data", {}).get("items", [])
        holidays = [e.get("summary", "") for e in items if e.get("summary")]
        return _text(str(holidays or _mock_holidays()))
    except FeishuAPIError:
        return _text(str(_mock_holidays()))


# ═══════════════════════════════════════════════════════════════════════
# II. 飞书 CLI — 文档
# ═══════════════════════════════════════════════════════════════════════

async def create_doc(title: str, content: str = "", folder_token: str = "") -> list[types.TextContent]:
    """创建飞书文档。

    Feishu API: POST /docx/v1/documents
    """
    if not feishu_client.is_configured:
        import uuid; doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        return _text(str({"doc_id": doc_id, "title": title, "url": f"https://xxx.feishu.cn/docx/{doc_id}", "mode": "mock"}))

    try:
        body: dict[str, Any] = {"title": title}
        if folder_token:
            body["folder_token"] = folder_token
        data = await feishu_client.post("/docx/v1/documents", body=body)
        doc = data.get("data", {}).get("document", {})
        doc_id = doc.get("document_id", "")

        # 写入内容（失败不影响文档创建结果）
        write_ok = False
        if content and doc_id:
            try:
                await _write_doc_content(doc_id, content)
                write_ok = True
            except Exception as e:
                logger.warning("Failed to write doc content: %s", e)

        return _text(str({
            "doc_id": doc_id, "title": title,
            "url": f"https://xxx.feishu.cn/docx/{doc_id}" if doc_id else "",
            "status": "created",
            "content_written": write_ok,
        }))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def read_doc(doc_id: str) -> list[types.TextContent]:
    """读取飞书文档纯文本内容。

    Feishu API: GET /docx/v1/documents/{document_id}/raw_content
    """
    if not feishu_client.is_configured:
        return _text(f"[mock] 文档 {doc_id} 的内容...")

    try:
        data = await feishu_client.get(f"/docx/v1/documents/{doc_id}/raw_content")
        text = data.get("data", {}).get("content", "")
        return _text(text or "(空文档)")
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def search_docs(query: str, page_size: int = 10) -> list[types.TextContent]:
    """搜索飞书文档。

    Feishu API: 使用搜索 API POST /search/v2/search (需 search:resource 权限)
    或降级使用 drive 文件列表 GET /drive/v1/files
    """
    if not feishu_client.is_configured:
        return _text(str(_mock_search(query)))

    try:
        data = await feishu_client.post(
            "/search/v2/search",
            body={"query": query, "from": 0, "size": page_size},
        )
        items = []
        for item in data.get("data", {}).get("items", []):
            doc = item.get("document", item)
            items.append({
                "title": doc.get("title", ""),
                "url": doc.get("url", ""),
                "doc_id": doc.get("document_id", ""),
            })
        return _text(str(items or f"未找到与 '{query}' 相关的文档"))
    except FeishuAPIError:
        return _text(str(_mock_search(query)))


async def _write_doc_content(doc_id: str, content: str):
    """向文档写入文本内容。

    先查文档 blocks 获取 page block_id，再向其 children 追加文本。
    """
    # 获取文档 block 树，找到 page 级别的 block_id
    data = await feishu_client.get(f"/docx/v1/documents/{doc_id}/blocks")
    page_block_id = doc_id  # fallback
    for block in data.get("data", {}).get("items", []):
        if block.get("block_type") == 1:  # page block
            page_block_id = block.get("block_id", doc_id)
            # 拿 page 的直接子 block 作为父节点
            for child_id in block.get("children", []):
                page_block_id = child_id if child_id else page_block_id
                break
            break

    blocks = []
    for line in content.strip().split("\n"):
        if not line.strip():
            continue
        text_content = line
        if line.startswith("# "):
            text_content = line[2:]
        elif line.startswith("## "):
            text_content = line[3:]

        blocks.append({
            "block_type": 2,
            "text": {
                "elements": [{"text_run": {"content": text_content}}],
            },
        })

    if blocks:
        await feishu_client.post(
            f"/docx/v1/documents/{doc_id}/blocks/{page_block_id}/children",
            body={"children": blocks, "index": -1},
        )


# ═══════════════════════════════════════════════════════════════════════
# III. 飞书 CLI — 日历
# ═══════════════════════════════════════════════════════════════════════

async def create_calendar_event(
    title: str, start_time: str, end_time: str,
    attendees: list[str] | None = None, description: str = "",
    calendar_id: str = "",
) -> list[types.TextContent]:
    """创建日历事件。

    Feishu API: POST /calendar/v4/calendars/{calendar_id}/events
    需在飞书开放平台开通 calendar:calendar 权限。
    """
    if not feishu_client.is_configured:
        import uuid; evt_id = f"evt_{uuid.uuid4().hex[:8]}"
        return _text(str({"event_id": evt_id, "title": title, "start": start_time, "end": end_time, "mode": "mock"}))

    cal_id = calendar_id or config.feishu.primary_calendar_id
    try:
        body: dict[str, Any] = {
            "summary": title,
            "start_time": {"timestamp": _to_timestamp(start_time)},
            "end_time": {"timestamp": _to_timestamp(end_time)},
        }
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"type": "user", "user_id": a} for a in attendees]

        data = await feishu_client.post(f"/calendar/v4/calendars/{cal_id}/events", body=body)
        evt = data.get("data", {}).get("event", {})
        return _text(str({"status": "created", "event_id": evt.get("event_id"), "title": title}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def query_calendar(
    start_date: str, end_date: str,
    attendee: str = "", calendar_id: str = "",
) -> list[types.TextContent]:
    """查询日历忙闲。

    Feishu API: GET /calendar/v4/calendars/{calendar_id}/events
    """
    if not feishu_client.is_configured:
        return _text(str(_mock_calendar_events()))

    cal_id = calendar_id or config.feishu.primary_calendar_id
    try:
        params: dict[str, Any] = {
            "start_time": str(int(_parse_due_timestamp(start_date))),
            "end_time": str(int(_parse_due_timestamp(end_date))),
            "page_size": 50,
        }
        data = await feishu_client.get(f"/calendar/v4/calendars/{cal_id}/events", params=params)
        events = []
        for e in data.get("data", {}).get("items", []):
            events.append({
                "event_id": e.get("event_id"),
                "title": e.get("summary"),
                "start": e.get("start_time", {}).get("date_time", ""),
                "end": e.get("end_time", {}).get("date_time", ""),
                "attendees": [a.get("user_id") for a in e.get("attendees", [])],
            })
        if attendee:
            events = [e for e in events if attendee in str(e.get("attendees", []))]
        return _text(str(events))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def cancel_calendar_event(event_id: str, calendar_id: str = "") -> list[types.TextContent]:
    """取消日历事件。

    Feishu API: DELETE /calendar/v4/calendars/{calendar_id}/events/{event_id}
    """
    if not feishu_client.is_configured:
        return _text(str({"status": "cancelled", "event_id": event_id, "mode": "mock"}))

    cal_id = calendar_id or config.feishu.primary_calendar_id
    try:
        await feishu_client.delete(f"/calendar/v4/calendars/{cal_id}/events/{event_id}")
        return _text(str({"status": "cancelled", "event_id": event_id}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


# ═══════════════════════════════════════════════════════════════════════
# IV. 飞书 CLI — 表格
# ═══════════════════════════════════════════════════════════════════════

async def read_sheet(sheet_id: str, range_str: str = "Sheet1!A1:Z100") -> list[types.TextContent]:
    """读取电子表格。

    Feishu API: GET /sheets/v2/spreadsheets/{spreadsheet_token}/values/{range}
    需在飞书开放平台开通 sheets:spreadsheet 权限。
    """
    if not feishu_client.is_configured:
        return _text(str(_mock_sheet(sheet_id)))

    if not sheet_id or sheet_id == "sheet_attendance":
        sheet_id = config.feishu.default_spreadsheet_token
    if not sheet_id:
        return _text("未配置 FEISHU_SHEET_TOKEN，请设置默认表格 token")

    try:
        data = await feishu_client.get(f"/sheets/v2/spreadsheets/{sheet_id}/values/{range_str}")
        values = data.get("data", {}).get("value_range", {}).get("values", [])
        return _text(str(values))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def write_sheet(
    sheet_id: str, values: list[list],
    range_str: str = "Sheet1!A1",
) -> list[types.TextContent]:
    """写入电子表格。

    Feishu API: PUT /sheets/v2/spreadsheets/{spreadsheet_token}/values (覆盖)
    或 POST /sheets/v2/spreadsheets/{spreadsheet_token}/values_append (追加)
    """
    if not feishu_client.is_configured:
        return _text(str({"status": "written", "rows_added": len(values), "mode": "mock"}))

    if not sheet_id or sheet_id == "sheet_attendance":
        sheet_id = config.feishu.default_spreadsheet_token
    if not sheet_id:
        return _text("未配置 FEISHU_SHEET_TOKEN")

    try:
        await feishu_client.post(
            f"/sheets/v2/spreadsheets/{sheet_id}/values_append",
            params={"insertDataOption": "INSERT_ROWS"},
            body={"valueRange": {"range": range_str, "values": values}},
        )
        return _text(str({"status": "written", "rows_added": len(values)}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


# ═══════════════════════════════════════════════════════════════════════
# V. 飞书 CLI — 邮件
# ═══════════════════════════════════════════════════════════════════════

async def send_feishu_mail(
    to_email: str, subject: str, body: str, cc: str = "",
) -> list[types.TextContent]:
    """发送邮件。

    Feishu API: POST /mail/v1/users/{user_id}/mails
    需在飞书开放平台开通 mail:mail 权限，user_id 为发件人 open_id。
    """
    if not feishu_client.is_configured:
        return _text(str({"status": "sent", "to": to_email, "subject": subject, "mode": "mock"}))

    sender_id = config.feishu.mail_sender_id
    if not sender_id:
        return _text("未配置 FEISHU_MAIL_SENDER_ID（发件人 open_id），无法发送邮件")

    try:
        data = await feishu_client.post(
            f"/mail/v1/users/{sender_id}/mails",
            body={
                "to": [{"address": to_email}],
                "subject": subject,
                "html_body": body.replace("\n", "<br>"),
                "cc": [{"address": cc}] if cc else [],
            },
        )
        return _text(str({"status": "sent", "to": to_email, "subject": subject}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


# ═══════════════════════════════════════════════════════════════════════
# VI. 飞书 CLI — 任务
# ═══════════════════════════════════════════════════════════════════════

async def create_task(
    title: str, assignee: str, due: str = "",
    description: str = "", user_id_type: str = "open_id",
) -> list[types.TextContent]:
    """创建任务。

    Feishu API: POST /task/v1/tasks
    需在飞书开放平台开通 task:task 权限。
    """
    if not feishu_client.is_configured:
        import uuid; tid = f"task_{uuid.uuid4().hex[:8]}"
        return _text(str({"task_id": tid, "title": title, "assignee": assignee, "status": "未开始", "mode": "mock"}))

    try:
        body: dict[str, Any] = {
            "summary": title,
            "origin": {"platform_i18n_name": '{"zh_cn": "HR智能助手", "en_us": "HR Agent"}'},
        }
        if assignee and assignee != "ou_test" and assignee != "张三":
            body["collaborator_ids"] = [assignee]
        if description:
            body["description"] = description
        if due:
            body["due"] = {"time": str(int(_parse_due_timestamp(due))), "timezone": "Asia/Shanghai"}

        data = await feishu_client.post(
            "/task/v1/tasks",
            params={"user_id_type": user_id_type},
            body=body,
        )
        t = data.get("data", {}).get("task", {})
        return _text(str({"task_id": t.get("id"), "title": title, "assignee": assignee, "status": "created"}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def list_tasks(
    assignee: str = "", status: str = "",
    user_id_type: str = "open_id", page_size: int = 50,
) -> list[types.TextContent]:
    """查询任务列表。

    Feishu API: GET /task/v1/tasks
    """
    if not feishu_client.is_configured:
        return _text(str(_mock_tasks_filtered(assignee, status)))

    try:
        params: dict[str, Any] = {"user_id_type": user_id_type, "page_size": page_size}
        if assignee:
            params["collaborator_user_id"] = assignee

        data = await feishu_client.get("/task/v1/tasks", params=params)
        tasks = []
        for t in data.get("data", {}).get("items", []):
            ts = {
                "task_id": t.get("id"),
                "title": t.get("summary"),
                "status": "已完成" if t.get("is_completed") else "进行中",
                "due": t.get("due", {}).get("time", ""),
            }
            if status:
                if status == ts["status"]:
                    tasks.append(ts)
            else:
                tasks.append(ts)
        return _text(str(tasks or "暂无任务"))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


async def complete_task(task_id: str, user_id_type: str = "open_id") -> list[types.TextContent]:
    """完成任务。

    Feishu API: PATCH /task/v1/tasks/{task_id}
    """
    if not feishu_client.is_configured:
        return _text(str({"status": "completed", "task_id": task_id, "mode": "mock"}))

    try:
        # Feishu Task API: complete endpoint
        await feishu_client.post(
            f"/task/v1/tasks/{task_id}/complete",
            params={"user_id_type": user_id_type},
            body={},
        )
        return _text(str({"status": "completed", "task_id": task_id}))
    except FeishuAPIError as e:
        return _text(str({"status": "error", "code": e.code, "message": e.message}))


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _to_timestamp(dt_str: str) -> str:
    """将 ISO datetime 转为 Unix 秒级时间戳。"""
    from datetime import timezone as tz
    try:
        dt = datetime.fromisoformat(dt_str)
        return str(int(dt.replace(tzinfo=tz.utc).timestamp()))
    except (ValueError, AttributeError):
        return dt_str


def _to_iso8601(date_str: str, *, is_start: bool) -> str:
    """将 YYYY-MM-DD 转为 ISO 8601 格式。"""
    if "T" in date_str:
        return date_str
    suffix = "T00:00:00+08:00" if is_start else "T23:59:59+08:00"
    return f"{date_str}{suffix}"


def _parse_due_timestamp(date_str: str) -> float:
    """将日期字符串转为 Unix 时间戳。"""
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc).timestamp()


# ═══════════════════════════════════════════════════════════════════════
# Mock 数据 (API 不可用时的降级)
# ═══════════════════════════════════════════════════════════════════════

def _mock_attendance(employee_id: int) -> list[dict]:
    return {
        1001: [
            {"date": "2026-07-14", "check_in": "09:05", "check_out": "18:30", "status": "正常"},
            {"date": "2026-07-15", "check_in": "08:55", "check_out": "19:00", "status": "正常"},
            {"date": "2026-07-16", "check_in": "09:30", "check_out": "18:00", "status": "迟到"},
            {"date": "2026-07-17", "check_in": "08:50", "check_out": "18:30", "status": "正常"},
        ],
    }.get(employee_id, [])


def _mock_leave_balance(employee_id: int) -> dict | None:
    return {
        1001: {"年假": 5.0, "病假": 10.0, "调休": 3.0, "婚假": 10.0, "产假/陪产假": 15.0},
    }.get(employee_id)


def _mock_holidays() -> list[str]:
    return [
        "2026-01-01 元旦", "2026-01-28 ~ 2026-02-03 春节",
        "2026-04-05 清明节", "2026-05-01 ~ 2026-05-05 劳动节",
        "2026-06-19 端午节", "2026-09-25 中秋节",
        "2026-10-01 ~ 2026-10-07 国庆节",
    ]


def _mock_search(query: str) -> list[dict]:
    docs = {
        "doc_001": {"title": "入职指南", "snippet": "欢迎加入..."},
        "doc_002": {"title": "年假政策", "snippet": "入职满1年..."},
        "doc_003": {"title": "报销制度", "snippet": "交通报销..."},
    }
    return [{"doc_id": k, **v} for k, v in docs.items() if query.lower() in k.lower() or query in v["title"]]


def _mock_calendar_events() -> list[dict]:
    return [
        {"event_id": "evt_001", "title": "面试-高级工程师-张三", "start": "2026-07-22T10:00", "end": "2026-07-22T11:00"},
        {"event_id": "evt_002", "title": "新员工入职培训", "start": "2026-07-25T09:00", "end": "2026-07-25T17:00"},
    ]


def _mock_sheet(sheet_id: str) -> list[list]:
    sheets = {
        "sheet_attendance": [["姓名", "日期", "打卡时间", "状态"], ["张三", "2026-07-14", "09:05", "正常"]],
    }
    return sheets.get(sheet_id, [["(空表格)"]])


def _mock_tasks_filtered(assignee: str, status: str) -> list[dict]:
    tasks = [
        {"task_id": "task_001", "title": "完成入职手续", "assignee": "张三", "status": "进行中", "due": "2026-07-25"},
        {"task_id": "task_002", "title": "签署劳动合同", "assignee": "张三", "status": "未开始", "due": "2026-07-26"},
        {"task_id": "task_003", "title": "IT设备发放", "assignee": "IT部门", "status": "已完成", "due": "2026-07-20"},
    ]
    result = tasks
    if assignee:
        result = [t for t in result if assignee in t["assignee"]]
    if status:
        result = [t for t in result if t["status"] == status]
    return result


# ═══════════════════════════════════════════════════════════════════════
# Tool 注册表
# ═══════════════════════════════════════════════════════════════════════

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


FEISHU_TOOLS = [
    # ── 基础通讯 ──
    ToolDef("get_my_attendance", "查询本月打卡记录",
        {"type": "object", "properties": {
            "employee_id": {"type": "integer"},
            "month": {"type": "string", "description": "YYYY-MM"},
        }, "required": ["employee_id"]},
        get_my_attendance),
    ToolDef("get_leave_balance", "查询年假/病假/调休余额",
        {"type": "object", "properties": {
            "employee_id": {"type": "integer"},
        }, "required": ["employee_id"]},
        get_leave_balance),
    ToolDef("submit_leave_request", "发起请假申请 (API: approval/v4/instances)",
        {"type": "object", "properties": {
            "employee_id": {"type": "string"}, "leave_type": {"type": "string"},
            "start_date": {"type": "string"}, "end_date": {"type": "string"},
            "reason": {"type": "string"},
        }, "required": ["employee_id", "leave_type", "start_date", "end_date"]},
        submit_leave_request),
    ToolDef("get_holiday_calendar", "查询公司放假安排",
        {"type": "object", "properties": {"year": {"type": "integer"}}},
        get_holiday_calendar),
    ToolDef("send_notification", "给指定用户发送飞书消息 (API: im/v1/messages)",
        {"type": "object", "properties": {
            "user_id": {"type": "string", "description": "用户的 open_id"},
            "message": {"type": "string"},
        }, "required": ["user_id", "message"]},
        send_notification),

    # ── 文档 ──
    ToolDef("create_doc", "创建飞书文档 (API: docx/v1/documents)",
        {"type": "object", "properties": {
            "title": {"type": "string"}, "content": {"type": "string"},
            "folder_token": {"type": "string"},
        }, "required": ["title"]},
        create_doc),
    ToolDef("read_doc", "读取飞书文档内容 (API: docx/{id}/raw_content)",
        {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]},
        read_doc),
    ToolDef("search_docs", "搜索飞书文档 (API: search/v2/search)",
        {"type": "object", "properties": {
            "query": {"type": "string"}, "page_size": {"type": "integer"},
        }, "required": ["query"]},
        search_docs),

    # ── 日历 ──
    ToolDef("create_calendar_event", "创建日历事件 (API: calendar/v4/calendars/{id}/events)",
        {"type": "object", "properties": {
            "title": {"type": "string"}, "start_time": {"type": "string"},
            "end_time": {"type": "string"}, "attendees": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"}, "calendar_id": {"type": "string"},
        }, "required": ["title", "start_time", "end_time"]},
        create_calendar_event),
    ToolDef("query_calendar", "查询日历忙闲 (API: calendar/v4/calendars/{id}/events)",
        {"type": "object", "properties": {
            "start_date": {"type": "string"}, "end_date": {"type": "string"},
            "attendee": {"type": "string"}, "calendar_id": {"type": "string"},
        }, "required": ["start_date", "end_date"]},
        query_calendar),
    ToolDef("cancel_calendar_event", "取消日历事件 (API: calendar/v4/calendars/{id}/events/{eid})",
        {"type": "object", "properties": {
            "event_id": {"type": "string"}, "calendar_id": {"type": "string"},
        }, "required": ["event_id"]},
        cancel_calendar_event),

    # ── 表格 ──
    ToolDef("read_sheet", "读取电子表格 (API: sheets/v2/spreadsheets/{token}/values/{range})",
        {"type": "object", "properties": {
            "sheet_id": {"type": "string"}, "range_str": {"type": "string"},
        }, "required": ["sheet_id"]},
        read_sheet),
    ToolDef("write_sheet", "写入电子表格 (API: sheets/v2/spreadsheets/{token}/values_append)",
        {"type": "object", "properties": {
            "sheet_id": {"type": "string"}, "values": {"type": "array", "items": {"type": "array"}},
            "range_str": {"type": "string"},
        }, "required": ["sheet_id", "values"]},
        write_sheet),

    # ── 邮件 ──
    ToolDef("send_feishu_mail", "发送邮件 (API: mail/v1/users/{id}/mails)",
        {"type": "object", "properties": {
            "to_email": {"type": "string"}, "subject": {"type": "string"},
            "body": {"type": "string"}, "cc": {"type": "string"},
        }, "required": ["to_email", "subject", "body"]},
        send_feishu_mail),

    # ── 任务 ──
    ToolDef("create_task", "创建任务 (API: task/v1/tasks)",
        {"type": "object", "properties": {
            "title": {"type": "string"}, "assignee": {"type": "string", "description": "用户 open_id"},
            "due": {"type": "string"}, "description": {"type": "string"},
            "user_id_type": {"type": "string", "description": "open_id/union_id/user_id"},
        }, "required": ["title", "assignee"]},
        create_task),
    ToolDef("list_tasks", "查询任务列表 (API: task/v1/tasks)",
        {"type": "object", "properties": {
            "assignee": {"type": "string"}, "status": {"type": "string"},
            "user_id_type": {"type": "string"}, "page_size": {"type": "integer"},
        }},
        list_tasks),
    ToolDef("complete_task", "完成任务 (API: task/v1/tasks/{id})",
        {"type": "object", "properties": {
            "task_id": {"type": "string"}, "user_id_type": {"type": "string"},
        }, "required": ["task_id"]},
        complete_task),
]
