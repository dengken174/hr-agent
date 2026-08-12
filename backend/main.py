"""HR Agent — FastAPI 应用入口。"""

import logging
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from backend.middleware import AuditMiddleware
from backend.routes.chat import router as chat_router
from backend.routes.skills import router as skills_router
from backend.routes.approval import router as approval_router
from backend.routes.knowledge import router as knowledge_router
from backend.routes.eval import router as eval_router
from backend.routes.auth_feishu import router as auth_feishu_router
from backend.routes.tts import router as tts_router
from backend.routes.stt import router as stt_router
from agent.executor import hr_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend")

# ── 前端静态文件路径 ───────────────────────────────────────────────

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期 — 启动时连接 MCP Server，构建向量索引，关闭时释放。"""
    logger.info("Starting HR Agent backend...")
    await hr_agent.start()
    logger.info("Agent ready with %d tools", len(hr_agent._all_tools))

    # 自动构建知识库向量索引
    try:
        from db.vector_store import build_index
        from db.repositories import KnowledgeRepo
        docs = await KnowledgeRepo().list_all()
        if docs:
            count = await build_index(docs)
            logger.info("Vector index built: %d documents", count)
    except Exception:
        logger.warning("Vector index build skipped (embedding model may not be ready)")

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

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
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
app.include_router(tts_router)
app.include_router(stt_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "agent_ready": hr_agent._mcp_client is not None}


# ── 生产模式：托管前端静态资源 ─────────────────────────────────────

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    for static_file in FRONTEND_DIST.glob("*"):
        if static_file.is_file():
            # mount single files (favicon, icons, etc.) as individual routes
            pass  # handled by SPA catch-all below

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str, request: Request):
        """SPA fallback — serve index.html for all non-API paths."""
        # skip API routes (already handled by routers above)
        path = FRONTEND_DIST / full_path
        if full_path and path.exists() and path.is_file():
            return FileResponse(path)
        return FileResponse(FRONTEND_DIST / "index.html")

    logger.info("Frontend static files enabled from %s", FRONTEND_DIST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, reload=True)
