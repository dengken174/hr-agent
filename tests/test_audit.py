from db.audit import AuditRepo


def test_build_insert_contains_columns():
    sql, params = AuditRepo._build_insert(
        1001, "employee", "read_salary", "denied", {"employee_id": 1002},
        "salary", "1002",
    )
    assert "INSERT INTO audit_log" in sql
    assert params[0] == 1001          # user_id
    assert params[1] == "employee"    # user_role
    assert params[2] == "read_salary" # action
    assert params[3] == "salary"      # resource_type
    assert params[4] == "1002"        # resource_id
    assert params[6] == "denied"      # result


def test_build_insert_detail_json():
    sql, params = AuditRepo._build_insert(
        1001, "employee", "read_salary", "success", {"employee_id": 1001},
        "salary", "1001",
    )
    # detail 是 JSON 字符串（index 5）
    import json
    assert json.loads(params[5]) == {"employee_id": 1001}
