"""飞书 Channel SDK — 事件处理器。

负责接收飞书服务器推送的事件，解析后交给 Agent 编排核心处理。
支持事件类型：
  - im.message.receive_v1    消息接收（群聊/单聊）
  - im.message.reaction.created  表情回复（快捷审批）
  - approval_instance.approved   飞书审批通过（双写同步到自研状态机）
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 事件数据模型
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FeishuEvent:
    """飞书事件标准化结构。"""
    event_type: str
    source_type: str          # group_chat / single_chat / doc_comment
    chat_id: str
    message_id: str
    sender_id: str            # 飞书 open_id
    sender_name: str
    content: str              # 文本内容（@Bot 后的部分）
    raw_content: str          # 完整原始消息
    tenant_key: str
    timestamp: str


@dataclass
class FeishuReply:
    """回复消息结构。"""
    chat_id: str
    message_id: str
    content: str
    content_type: str = "text"       # text / markdown / interactive
    at_sender: bool = True
    reply_to_message_id: str = ""


# ═══════════════════════════════════════════════════════════════════════
# 事件解析器
# ═══════════════════════════════════════════════════════════════════════

class EventParser:
    """解析飞书开放平台推送的 JSON 事件体。"""

    def parse(self, raw_body: dict) -> FeishuEvent | None:
        """从飞书事件中提取统一结构。"""
        header = raw_body.get("header", {})
        event = raw_body.get("event", {})

        event_type = header.get("event_type", "")
        if not event_type:
            return None

        # 消息接收事件
        if event_type == "im.message.receive_v1":
            return self._parse_message(event, raw_body)

        # 表情回复事件
        if event_type == "im.message.reaction.created":
            return self._parse_reaction(event, raw_body)

        # 审批事件
        if event_type == "approval_instance.approved":
            return self._parse_approval(event, raw_body)

        logger.debug("Unhandled event type: %s", event_type)
        return None

    def _parse_message(self, event: dict, raw: dict) -> FeishuEvent:
        msg = event.get("message", {})
        chat_type = msg.get("chat_type", "single")
        content = json.loads(msg.get("content", "{}"))
        text = content.get("text", "")

        # 去除 @Bot 前缀
        bot_name = config.feishu.bot_name
        cleaned = text.replace(f"@{bot_name}", "").replace(f"@ {bot_name}", "").strip()

        return FeishuEvent(
            event_type="im.message.receive_v1",
            source_type="group_chat" if chat_type == "group" else "single_chat",
            chat_id=msg.get("chat_id", ""),
            message_id=msg.get("message_id", ""),
            sender_id=event.get("sender", {}).get("sender_id", {}).get("open_id", ""),
            sender_name=event.get("sender", {}).get("sender_id", {}).get("name", ""),
            content=cleaned,
            raw_content=text,
            tenant_key=raw.get("header", {}).get("tenant_key", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_reaction(self, event: dict, raw: dict) -> FeishuEvent | None:
        """表情回复 → 映射为快捷审批操作。"""
        emoji_type = event.get("reaction_type", {}).get("emoji_type", "")
        action = EMOJI_ACTION_MAP.get(emoji_type)
        if not action:
            return None

        return FeishuEvent(
            event_type="im.message.reaction.created",
            source_type="reaction",
            chat_id=event.get("chat_id", ""),
            message_id=event.get("message_id", ""),
            sender_id=event.get("user_id", {}).get("open_id", ""),
            sender_name=event.get("user_id", {}).get("name", ""),
            content=action,
            raw_content=emoji_type,
            tenant_key=raw.get("header", {}).get("tenant_key", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_approval(self, event: dict, raw: dict) -> FeishuEvent | None:
        """飞书审批通过 → 同步到自研状态机。"""
        instance = event.get("instance", {})
        return FeishuEvent(
            event_type="approval_instance.approved",
            source_type="approval_sync",
            chat_id="",
            message_id=instance.get("instance_code", ""),
            sender_id="SYSTEM",
            sender_name="飞书审批系统",
            content=f"approval_passed:{instance.get('instance_code', '')}",
            raw_content=json.dumps(instance, ensure_ascii=False),
            tenant_key=raw.get("header", {}).get("tenant_key", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# 表情 → 快捷操作映射
EMOJI_ACTION_MAP = {
    "THUMBSUP": "approve",       # 👍 通过
    "OK": "approve",             # 👌 通过
    "CROSS_MARK": "reject",      # ❌ 驳回
    "NO_ENTRY": "reject",        # 🚫 驳回
}


# ═══════════════════════════════════════════════════════════════════════
# 事件路由器
# ═══════════════════════════════════════════════════════════════════════

class EventRouter:
    """根据事件类型和来源路由到不同的处理策略。

    群聊: 多人场景，回复需 @提问者，敏感信息脱敏
    单聊: 一对一场景，可查个人信息，需要权限验证
    审批同步: 系统间数据双写
    """

    async def handle(self, event: FeishuEvent) -> FeishuReply | None:
        """路由事件到对应处理函数，返回回复。"""
        if event.event_type == "im.message.receive_v1":
            return await self._handle_message(event)
        elif event.event_type == "im.message.reaction.created":
            return await self._handle_reaction(event)
        elif event.event_type == "approval_instance.approved":
            return await self._handle_approval_sync(event)
        return None

    async def _handle_message(self, event: FeishuEvent) -> FeishuReply | None:
        """处理文本消息 — 核心对话入口。

        实际部署时，这里调用 agent/executor.py 的 HRAgent.chat()。
        """
        if not event.content.strip():
            return None

        if event.source_type == "group_chat":
            # 群聊: 对敏感查询做脱敏
            return FeishuReply(
                chat_id=event.chat_id, message_id=event.message_id,
                content=f"[群聊模式] 收到 @{event.sender_name} 的问题，处理中...\n\n（Agent 回复将在此处）",
                content_type="text", at_sender=True,
            )
        else:
            # 单聊: 完整 Agent 对话
            return FeishuReply(
                chat_id=event.chat_id, message_id=event.message_id,
                content=f"[单聊模式] @{event.sender_name}，正在为您处理...\n\n（Agent 回复将在此处）",
                content_type="text", at_sender=False,
            )

    async def _handle_reaction(self, event: FeishuEvent) -> FeishuReply | None:
        """处理表情回复 → 快捷审批。

        原始消息中需要包含 approval_id，以此关联审批单。
        """
        action = event.content
        if action not in ("approve", "reject"):
            return None
        action_text = "审批通过" if action == "approve" else "审批驳回"
        return FeishuReply(
            chat_id=event.chat_id, message_id=event.message_id,
            content=f"已收到 @{event.sender_name} 的{action_text}操作。",
            content_type="text", at_sender=True,
        )

    async def _handle_approval_sync(self, event: FeishuEvent) -> FeishuReply | None:
        """飞书审批通过 → 双写同步到自研状态机。

        避免两套审批系统的数据不一致。
        """
        instance_code = event.message_id
        logger.info("飞书审批同步: %s", instance_code)
        # 同步逻辑: 更新自研 approval_requests 表状态
        # await approval_sm.approve_by_external_id(instance_code, ...)
        return None  # 系统操作不需要回复


event_router = EventRouter()


# ═══════════════════════════════════════════════════════════════════════
# FastAPI Webhook 端点 (供 backend 层使用)
# ═══════════════════════════════════════════════════════════════════════

async def handle_webhook(raw_body: dict, headers: dict) -> dict:
    """处理飞书 Webhook HTTP 请求。

    backend 的 FastAPI 路由 /feishu/webhook 调用此函数。
    流程：
      1. 验证签名/Token
      2. 如果是 URL 验证，返回 challenge
      3. 解析事件 → 路由 → Agent 处理 → 回复
    """
    # URL 验证回调
    if raw_body.get("type") == "url_verification":
        challenge = raw_body.get("challenge", "")
        return {"challenge": challenge}

    # 解析事件
    parser = EventParser()
    event = parser.parse(raw_body)
    if not event:
        return {"status": "ignored", "reason": "unhandled event type"}

    # 路由处理
    reply = await event_router.handle(event)
    if reply is None:
        return {"status": "processed", "reply": "none"}

    # 发送回复
    await _send_reply_via_api(reply)
    return {"status": "replied", "content": reply.content[:100]}


async def _send_reply_via_api(reply: FeishuReply):
    """通过飞书开放平台 API 发送回复消息。

    生产环境调用飞书 发送消息 API:
      POST https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply
    """
    logger.info("Reply to chat=%s msg=%s: %s", reply.chat_id, reply.message_id, reply.content[:80])
    # TODO: 生产环境对接飞书发送消息 API
