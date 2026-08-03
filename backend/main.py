"""HR Agent — FastAPI 应用入口。"""

import logging
import sys
import os
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.middleware import AuditMiddleware
from backend.routes.chat import router as chat_router
from backend.routes.skills import router as skills_router
from backend.routes.approval import router as approval_router
from backend.routes.knowledge import router as knowledge_router
from backend.routes.eval import router as eval_router
from backend.routes.auth_feishu import router as auth_feishu_router
from agent.executor import hr_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时连接 MCP Server，关闭时释放。"""
    logger.info("Starting HR Agent backend...")
    await hr_agent.start()
    logger.info("Agent ready with %d tools", len(hr_agent._all_tools))
    yield
    logger.info("Shutting down...")
    await hr_agent.stop()


app = FastAPI(
    title="HR Agent API",
    description="HR 智能助手后端服务 — 对话 / Skill 管理 / 审批 / 知识库 / RAG 评测",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 审计日志 ────────────────────────────────────────────────────────

app.add_middleware(AuditMiddleware)

# ── 路由注册 ────────────────────────────────────────────────────────

app.include_router(chat_router)
app.include_router(skills_router)
app.include_router(approval_router)
app.include_router(knowledge_router)
app.include_router(eval_router)
app.include_router(auth_feishu_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "agent_ready": hr_agent._mcp_client is not None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, reload=True)
