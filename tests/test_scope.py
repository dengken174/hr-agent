from db.scope import DataScope, mask_salary, authorize


def test_can_read_salary_self():
    assert DataScope(1001, "employee").can_read_salary(1001) is True


def test_can_read_salary_other_denied():
    assert DataScope(1001, "employee").can_read_salary(1002) is False


def test_can_read_salary_hr_any():
    assert DataScope(2001, "hr_admin").can_read_salary(1001) is True


def test_can_search_others_employee_denied():
    assert DataScope(1001, "employee").can_search_others() is False


def test_can_search_others_hr():
    assert DataScope(2001, "hr_admin").can_search_others() is True


def test_can_view_approval_employee_own():
    assert DataScope(1001, "employee").can_view_approval(1001, None) is True


def test_can_view_approval_employee_assignee():
    assert DataScope(1001, "employee").can_view_approval(2002, 1001) is True


def test_can_view_approval_employee_other_denied():
    assert DataScope(1001, "employee").can_view_approval(2002, 3003) is False


def test_can_view_approval_hr():
    assert DataScope(2001, "hr_admin").can_view_approval(9999, 9999) is True


def test_mask_salary_list_range():
    assert mask_salary(28000, "list") == "25k-30k"


def test_mask_salary_self_plaintext():
    assert mask_salary(28000, "self") == "28000"


def test_mask_salary_export_masked():
    assert mask_salary(28000, "export") == "****"


def test_authorize_search_employee_hr_only():
    assert authorize(DataScope(2001, "hr_admin"), "hris_search_employee", {}) is True
    assert authorize(DataScope(1001, "employee"), "hris_search_employee", {}) is False


def test_authorize_get_team_members_hr_only():
    assert authorize(DataScope(1001, "employee"), "hris_get_team_members", {"dept": "技术部"}) is False


def test_authorize_public_tool_always_allowed():
    assert authorize(DataScope(1001, "employee"), "knowledge_search_knowledge_base", {}) is True
