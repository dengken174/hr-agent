"""Skill CRUD — HR 通过 Web 页面管理自定义 Skill。"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware import get_current_user, require_role
from backend.models import SkillDefinition, SkillResponse
from agent.skill_manager import CustomSkill, skill_manager, SKILLS_FILE

logger = logging.getLogger("backend.routes.skills")
router = APIRouter(prefix="/api/skills", tags=["skills"])


def _ensure_hr_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员可管理 Skill")
    return user


@router.get("", response_model=list[SkillResponse])
async def list_skills(user: dict = Depends(get_current_user)):
    """列出所有 Skill（只读所有人可看）。"""
    skill_manager.check_reload()
    return [
        SkillResponse(
            name=s.name,
            display_name=s.display_name,
            description=s.description,
            triggers=s.triggers,
            tools=s.tools,
            system_prompt=s.system_prompt,
            reply_hint=s.reply_hint,
            examples=s.examples,
            enabled=s.enabled,
        )
        for s in skill_manager.all_skills
    ]


@router.post("", response_model=SkillResponse)
async def create_skill(data: SkillDefinition, user: dict = Depends(_ensure_hr_admin)):
    """新增 Skill 并持久化到 skills.json。"""
    skill_manager.check_reload()

    if skill_manager.get(data.name):
        raise HTTPException(status_code=409, detail=f"Skill '{data.name}' 已存在")

    skill = CustomSkill(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        triggers=data.triggers,
        tools=data.tools,
        system_prompt=data.system_prompt,
        reply_hint=data.reply_hint,
        examples=data.examples,
        enabled=data.enabled,
    )
    skill_manager.add_skill(skill)
    logger.info("HR user %s created skill: %s", user["display_name"], data.name)
    return SkillResponse(**skill.to_dict())


@router.put("/{name}", response_model=SkillResponse)
async def update_skill(name: str, data: SkillDefinition, user: dict = Depends(_ensure_hr_admin)):
    """更新 Skill。"""
    skill_manager.check_reload()

    if not skill_manager.get(name):
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")

    skill = CustomSkill(
        name=data.name,
        display_name=data.display_name,
        description=data.description,
        triggers=data.triggers,
        tools=data.tools,
        system_prompt=data.system_prompt,
        reply_hint=data.reply_hint,
        examples=data.examples,
        enabled=data.enabled,
    )
    skill_manager.add_skill(skill)
    logger.info("HR user %s updated skill: %s", user["display_name"], name)
    return SkillResponse(**skill.to_dict())


@router.delete("/{name}")
async def delete_skill(name: str, user: dict = Depends(_ensure_hr_admin)):
    """删除 Skill。"""
    skill_manager.check_reload()
    if not skill_manager.get(name):
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")
    skill_manager.remove_skill(name)
    logger.info("HR user %s deleted skill: %s", user["display_name"], name)
    return {"status": "deleted", "name": name}


@router.patch("/{name}/toggle")
async def toggle_skill(name: str, user: dict = Depends(_ensure_hr_admin)):
    """启用/禁用 Skill。"""
    skill_manager.check_reload()
    skill = skill_manager.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")
    new_state = not skill.enabled
    skill_manager.toggle_skill(name, new_state)
    logger.info("HR user %s %s skill: %s", user["display_name"], "enabled" if new_state else "disabled", name)
    return {"status": "ok", "name": name, "enabled": new_state}


@router.get("/tools", response_model=list[str])
async def list_available_tools():
    """返回所有可用的 Tool 名称（供 Skill 配置时选择）。"""
    from agent.executor import hr_agent
    if hr_agent._mcp_client is None:
        await hr_agent.start()
    return sorted(t.name for t in hr_agent._all_tools)
