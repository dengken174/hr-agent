"""数据访问控制：DataScope 权限判定 + 薪资脱敏 + 工具授权。纯逻辑，可单测。"""

from dataclasses import dataclass


@dataclass
class DataScope:
    user_id: int
    role: str   # "employee" | "hr_admin" | "interviewer"

    def can_read_salary(self, emp_id: int) -> bool:
        if self.role == "hr_admin":
            return True
        return emp_id == self.user_id

    def can_search_others(self) -> bool:
        return self.role == "hr_admin"

    def can_view_approval(self, applicant_id: int, assignee_id: int | None) -> bool:
        if self.role == "hr_admin":
            return True
        return self.user_id in (applicant_id, assignee_id)


def mask_salary(amount: float, context: str) -> str:
    if context == "self":
        return f"{amount:.0f}"
    if context == "list":
        lower = int(amount // 5000) * 5000
        return f"{lower//1000}k-{(lower+5000)//1000}k"
    return "****"


# 查别人类工具：仅 HR 可用（其余工具靠身份绑定 + 工具内部逻辑）
_OTHERS_TOOLS = {"hris_search_employee", "hris_get_team_members"}


def authorize(scope: DataScope, tool_name: str, kwargs: dict) -> bool:
    if tool_name in _OTHERS_TOOLS:
        return scope.can_search_others()
    return True
