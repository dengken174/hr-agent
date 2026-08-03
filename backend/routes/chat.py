"""SSE 流式对话端点。"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from backend.middleware import get_current_user
from backend.models import ChatRequest, ChatResponse
from agent.executor import hr_agent

logger = logging.getLogger("backend.routes.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    """SSE 流式对话 — 逐 token 推送回复。"""

    # 用鉴权后的身份覆盖请求中的身份（防止前端篡改）
    user_id = user["user_id"]
    user_role = user["role"]

    async def event_generator():
        try:
            # 等待 Agent 就绪（如果还没 start）
            if hr_agent._mcp_client is None:
                await hr_agent.start()

            async for chunk in hr_agent.chat_stream(
                user_message=req.message,
                session_id=req.session_id,
                user_id=user_id,
                user_role=user_role,
            ):
                yield {"event": "token", "data": json.dumps({"text": chunk}, ensure_ascii=False)}

            yield {"event": "done", "data": json.dumps({"status": "completed"})}

        except Exception as e:
            logger.exception("Chat stream error")
            yield {"event": "error", "data": json.dumps({"error": str(e)})}

    return EventSourceResponse(event_generator())


@router.post("", response_model=ChatResponse)
async def chat_sync(req: ChatRequest, user: dict = Depends(get_current_user)):
    """同步对话 — 等待完整回复后返回。"""
    user_id = user["user_id"]
    user_role = user["role"]

    if hr_agent._mcp_client is None:
        await hr_agent.start()

    reply = await hr_agent.chat(
        user_message=req.message,
        session_id=req.session_id,
        user_id=user_id,
        user_role=user_role,
    )

    return ChatResponse(reply=reply, session_id=req.session_id)
