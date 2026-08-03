"""Pydantic 数据模型 — Request / Response schema。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Chat ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    user_id: int = 1001
    user_role: str = "employee"  # employee | hr_admin | interviewer


class ChatResponse(BaseModel):
    reply: str
    intent: str = ""
    session_id: str = ""


# ── Skill ──────────────────────────────────────────────────────────

class SkillDefinition(BaseModel):
    name: str = Field(..., description="唯一标识, e.g. send_onboarding_email")
    display_name: str = Field(..., description="中文名, e.g. 发送入职欢迎邮件")
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    reply_hint: str = ""
    examples: list[dict] = Field(default_factory=list)
    enabled: bool = True


class SkillResponse(SkillDefinition):
    pass


# ── Approval ────────────────────────────────────────────────────────

class ApprovalItem(BaseModel):
    id: str
    type: str = ""           # leave | expense | benefit
    applicant: str = ""
    applicant_id: int = 0
    department: str = ""
    title: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"  # pending | approved | rejected
    created_at: str = ""
    updated_at: str = ""


class ApprovalAction(BaseModel):
    approver_id: int
    action: str  # approve | reject
    comment: str = ""


# ── Knowledge ───────────────────────────────────────────────────────

class KnowledgeDoc(BaseModel):
    id: str = ""
    title: str
    content: str
    category: str = ""       # policy | benefit | guide | faq
    tags: list[str] = Field(default_factory=list)
    updated_at: str = ""


class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class KnowledgeSearchResult(BaseModel):
    title: str
    content: str
    relevance: float
    doc_id: str = ""


# ── Evaluation ──────────────────────────────────────────────────────

class EvalRequest(BaseModel):
    test_set: list[dict[str, str]] = Field(default_factory=list)
    # [{"question": "...", "ground_truth": "..."}, ...]


class EvalResult(BaseModel):
    question: str
    answer: str
    ground_truth: str = ""
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0


class EvalSummary(BaseModel):
    total: int = 0
    avg_faithfulness: float = 0.0
    avg_answer_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    avg_context_recall: float = 0.0
    results: list[EvalResult] = Field(default_factory=list)


# ── Auth ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    user_role: str
    display_name: str = ""


# ── Dashboard ───────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    total_conversations: int = 0
    total_skills: int = 0
    active_skills: int = 0
    pending_approvals: int = 0
    knowledge_docs: int = 0
    avg_response_time_ms: float = 0.0
