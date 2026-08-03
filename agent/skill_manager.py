"""热插拔自定义 Skill 系统。

HR 在 skills.json 中用中文描述业务需求，Agent 运行时自动加载，
无需重启服务、无需写代码。
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Skill 配置文件路径
SKILLS_FILE = Path(__file__).parent.parent / "mcp_servers" / "skills.json"


@dataclass
class CustomSkill:
    """一个 HR 自定义的业务 Skill。"""
    name: str                          # 唯一标识, e.g. "send_onboarding_email"
    display_name: str                  # 中文名, e.g. "发送入职欢迎邮件"
    description: str                   # 功能描述
    triggers: list[str] = field(default_factory=list)  # 触发关键词
    tools: list[str] = field(default_factory=list)      # 需要的 Tool 列表
    system_prompt: str = ""            # 给 LLM 的执行指令
    reply_hint: str = ""               # 回复时额外提示
    examples: list[dict] = field(default_factory=list)  # few-shot 示例
    enabled: bool = True               # 是否启用

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "triggers": self.triggers,
            "tools": self.tools,
            "system_prompt": self.system_prompt,
            "reply_hint": self.reply_hint,
            "examples": self.examples,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CustomSkill":
        return cls(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            description=data.get("description", ""),
            triggers=data.get("triggers", []),
            tools=data.get("tools", []),
            system_prompt=data.get("system_prompt", ""),
            reply_hint=data.get("reply_hint", ""),
            examples=data.get("examples", []),
            enabled=data.get("enabled", True),
        )


class SkillManager:
    """Skill 管理器 — 热加载 + CRUD + 匹配。"""

    def __init__(self, skills_file: Path | None = None):
        self._file = Path(skills_file or SKILLS_FILE)
        self._skills: dict[str, CustomSkill] = {}
        self._last_mtime: float = 0
        self._ensure_file()
        self.reload()

    def _ensure_file(self):
        """确保 skills.json 存在，首次创建时写入示例 Skill。"""
        if not self._file.exists():
            self._file.parent.mkdir(parents=True, exist_ok=True)
            default_skills = [
                {
                    "name": "send_onboarding_email",
                    "display_name": "发送入职欢迎邮件",
                    "description": "给新员工发送入职欢迎邮件，自动查询邮箱并抄送HRBP",
                    "triggers": ["入职邮件", "欢迎邮件", "发入职通知", "welcome email", "给.*发入职"],
                    "tools": ["hris_search_employee", "feishu_send_feishu_mail"],
                    "system_prompt": "用户想给新员工发入职欢迎邮件。\n1. 从对话中提取新员工姓名\n2. 用 hris_search_employee 查到该员工的邮箱\n3. 用 feishu_send_feishu_mail 发送邮件，主题为「欢迎加入公司！」，内容包含报到时间、需带材料、入职当天安排\n4. 回复用户告知发送结果",
                    "reply_hint": "邮件已发送，同时抄送了HRBP和直属上级。",
                    "examples": [
                        {"user": "帮我给张三发入职欢迎邮件", "assistant": "好的，查到张三的邮箱是 zhangsan@company.com，已发送入职欢迎邮件，抄送 HRBP 李四。"}
                    ],
                    "enabled": True
                },
                {
                    "name": "probation_reminder",
                    "display_name": "试用期到期提醒",
                    "description": "查询即将试用期到期的员工列表并发送提醒",
                    "triggers": ["试用期", "转正提醒", "试用到期", "试用期到期"],
                    "tools": ["hris_search_employee", "hris_get_team_members", "feishu_send_notification"],
                    "system_prompt": "用户想查看试用期到期情况。\n1. 确定用户关注的部门或范围\n2. 用 hris_get_team_members 获取员工列表\n3. 根据入职日期判断试用期是否即将到期（6个月试用期）\n4. 汇总将到期人员列表回复用户",
                    "reply_hint": "如有即将到期人员，建议提醒相关主管安排转正评估。",
                    "examples": [
                        {"user": "技术部有哪些人试用期快到了", "assistant": "技术部目前有2位员工试用期将在本月到期：张三（到期日6月15日）、王五（到期日6月28日）。建议通知相关主管准备转正评估。"}
                    ],
                    "enabled": True
                },
                {
                    "name": "attendance_weekly_report",
                    "display_name": "部门考勤周报",
                    "description": "生成指定部门的周考勤汇总",
                    "triggers": ["考勤周报", "考勤汇总", "出勤统计", "考勤报表"],
                    "tools": ["hris_get_team_members", "feishu_get_my_attendance", "feishu_write_sheet"],
                    "system_prompt": "用户想要生成部门考勤周报。\n1. 确定部门名称和日期范围\n2. 用 hris_get_team_members 获取部门成员\n3. 用 feishu_get_my_attendance 查询考勤数据\n4. 用 feishu_write_sheet 写入多维表格\n5. 回复用户表格链接",
                    "reply_hint": "考勤异常已标红，请HR核实。",
                    "examples": [],
                    "enabled": True
                },
            ]
            self._file.write_text(json.dumps(default_skills, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("Created default skills.json with 3 example skills")

    def reload(self):
        """从文件重新加载 Skill 列表（热插拔核心）。"""
        try:
            mtime = self._file.stat().st_mtime
            if mtime == self._last_mtime:
                return  # 没有变化，跳过
            self._last_mtime = mtime
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            self._skills = {}
            for item in raw:
                skill = CustomSkill.from_dict(item)
                self._skills[skill.name] = skill
            enabled = sum(1 for s in self._skills.values() if s.enabled)
            logger.info("Loaded %d skills (%d enabled) from %s", len(self._skills), enabled, self._file)
        except Exception:
            logger.exception("Failed to reload skills from %s", self._file)

    def check_reload(self):
        """检查文件是否有更新，有则热加载。每次请求时调用。"""
        try:
            mtime = self._file.stat().st_mtime
            if mtime != self._last_mtime:
                self.reload()
        except Exception:
            pass

    @property
    def all_skills(self) -> list[CustomSkill]:
        return [s for s in self._skills.values() if s.enabled]

    def get(self, name: str) -> CustomSkill | None:
        return self._skills.get(name)

    def match(self, user_message: str) -> CustomSkill | None:
        """用关键词匹配用户意图是否命中某个自定义 Skill。

        返回第一个匹配的 Skill，无匹配返回 None。
        """
        import re
        msg_lower = user_message.lower()
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            for trigger in skill.triggers:
                # 支持简单正则（HR 配的关键词如果包含 .* 等正则符号，按正则匹配）
                if any(c in trigger for c in ".*+?[]()"):
                    try:
                        if re.search(trigger, user_message):
                            return skill
                    except re.error:
                        pass
                # 普通关键词包含匹配
                if trigger.lower() in msg_lower:
                    return skill
        return None

    def add_skill(self, skill: CustomSkill):
        """添加 Skill 并持久化到文件。"""
        self._skills[skill.name] = skill
        self._persist()

    def remove_skill(self, name: str):
        """移除 Skill 并持久化。"""
        self._skills.pop(name, None)
        self._persist()

    def toggle_skill(self, name: str, enabled: bool):
        """启用/禁用 Skill 并持久化。"""
        if name in self._skills:
            self._skills[name].enabled = enabled
            self._persist()

    def _persist(self):
        data = [s.to_dict() for s in self._skills.values()]
        self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_mtime = self._file.stat().st_mtime

    def get_tool_names(self, skill: CustomSkill, all_tool_names: set[str]) -> list[str]:
        """返回 Skill 需要的 Tool 中实际存在的那些。"""
        return [t for t in skill.tools if t in all_tool_names]


# 全局单例
skill_manager = SkillManager()
