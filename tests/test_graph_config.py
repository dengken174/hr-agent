import agent.graph_engine as ge

def test_defaults_and_env():
    assert ge.DEFAULTS["max_steps"] == 30
    assert ge.WRITE_TOOLS == frozenset({
        "approval_start_approval", "approval_approve_request", "approval_reject_request",
        "feishu_submit_leave_request", "feishu_create_doc", "feishu_create_calendar_event",
        "feishu_create_task", "feishu_send_feishu_mail", "feishu_write_sheet"})
    assert ge.engine_mode() in ("graph", "legacy")
    assert ge.checkpointer_backend() in ("memory", "redis")

def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    assert ge.engine_mode() == "legacy"
    monkeypatch.setenv("AGENT_CHECKPOINTER", "redis")
    assert ge.checkpointer_backend() == "redis"
