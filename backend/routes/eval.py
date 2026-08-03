"""RAGAS 评测端点 — 模拟评测指标。"""

import logging
import random
import time

from fastapi import APIRouter, Depends

from backend.middleware import get_current_user
from backend.models import EvalRequest, EvalResult, EvalSummary

logger = logging.getLogger("backend.routes.eval")
router = APIRouter(prefix="/api/eval", tags=["evaluation"])

# ── 预设测试集 ──────────────────────────────────────────────────────

PRESET_TESTS = [
    {"question": "公司年假有多少天？", "ground_truth": "入职满1年不满10年5天年假，满10年不满20年10天，满20年15天。"},
    {"question": "五险一金怎么交？", "ground_truth": "养老保险公司16%个人8%，医疗保险公司8.5%个人2%，失业保险各0.5%，公积金5%-12%。"},
    {"question": "入职需要带什么材料？", "ground_truth": "身份证原件、学历学位证复印件、离职证明、银行卡。"},
    {"question": "住房补贴标准是多少？", "ground_truth": "P6及以上每月1500元，P5及以下每月800元。"},
    {"question": "试用期多久？", "ground_truth": "试用期6个月，2次转正评估。"},
]


@router.get("/presets", response_model=list[dict])
async def get_presets():
    """获取预设测试集。"""
    return PRESET_TESTS


@router.post("/run", response_model=EvalSummary)
async def run_evaluation(req: EvalRequest, user: dict = Depends(get_current_user)):
    """执行 RAGAS 评测（mock 模式）。"""
    tests = req.test_set if req.test_set else PRESET_TESTS
    results = []
    random.seed(int(time.time()))

    for test in tests:
        time.sleep(0.3)  # 模拟评测耗时
        results.append(EvalResult(
            question=test["question"],
            answer=f"[Mock Answer] {test.get('ground_truth', '')[:80]}...",
            ground_truth=test.get("ground_truth", ""),
            faithfulness=round(random.uniform(0.75, 0.98), 3),
            answer_relevancy=round(random.uniform(0.70, 0.96), 3),
            context_precision=round(random.uniform(0.72, 0.95), 3),
            context_recall=round(random.uniform(0.68, 0.94), 3),
        ))

    n = len(results)
    return EvalSummary(
        total=n,
        avg_faithfulness=round(sum(r.faithfulness for r in results) / n, 3) if n else 0,
        avg_answer_relevancy=round(sum(r.answer_relevancy for r in results) / n, 3) if n else 0,
        avg_context_precision=round(sum(r.context_precision for r in results) / n, 3) if n else 0,
        avg_context_recall=round(sum(r.context_recall for r in results) / n, 3) if n else 0,
        results=results,
    )
