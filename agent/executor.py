import logging
import os
from typing import Any, AsyncIterator

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_classic.memory import ConversationBufferWindowMemory
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from config import config
from agent.intent import IntentResult, intent_classifier
from agent.memory import MemoryManager, memory_manager
from agent.prompts import HR_SYSTEM_PROMPT
from agent.router import route_tools
from agent.skill_manager import CustomSkill, skill_manager

logger = logging.getLogger(__name__)

# 需要绑定调用者身份的参数名
_IDENTITY_PARAMS = {"employee_id", "applicant_id", "operator_id", "assignee_id"}

# 写入类工具 — 需要用户确认才能执行
_WRITE_TOOLS = {
    "approval_start_approval", "approval_approve_request", "approval_reject_request",
    "feishu_submit_leave_request", "feishu_create_doc", "feishu_create_calendar_event",
    "feishu_create_task", "feishu_send_feishu_mail", "feishu_write_sheet",
}

# 待确认操作：session_id → {tool, args}
_pending_confirmations: dict[str, dict[str, Any]] = {}


def _apply_identity_guard(
    tools: list[BaseTool], user_id: int, user_role: str
) -> list[BaseTool]:
    """非 admin 用户强制 identity 参数（employee_id 等）= user_id，防止越权查询他人数据。"""
    if user_role == "hr_admin":
        return tools

    wrapped = []
    for t in tools:
        _orig_arun = t._arun

        async def _guarded(**kwargs: Any) -> Any:
            for param in _IDENTITY_PARAMS:
                if param in kwargs:
                    logger.warning(
                        "Identity guard: overriding %s from %s to user_id=%s",
                        param, kwargs[param], user_id,
                    )
                    kwargs[param] = user_id
            return await _orig_arun(**kwargs)

        t._arun = _guarded
        wrapped.append(t)

    return wrapped


def _apply_confirmation_guard(
    tools: list[BaseTool], session_id: str
) -> list[BaseTool]:
    """对写入类工具包装确认步骤：首次调用返回确认提示，用户确认后才执行。"""
    wrapped = []
    for t in tools:
        if t.name not in _WRITE_TOOLS:
            wrapped.append(t)
            continue

        _orig_arun = t._arun

        def _make_confirmed(orig, tname, sid):
            async def _guarded(**kwargs):
                pending = _pending_confirmations.get(sid)
                if pending and pending["tool"] == tname:
                    # 用户已确认，执行并从待确认列表中移除
                    del _pending_confirmations[sid]
                    return await orig(**kwargs)
                # 首次调用：存储并返回确认提示
                _pending_confirmations[sid] = {"tool": tname, "args": kwargs}
                return (
                    f"⚠️ 确认操作：{tname}\n"
                    f"参数：{kwargs}\n\n"
                    f"请回复「确认」执行此操作，或回复「取消」放弃。"
                )
            return _guarded

        t._arun = _make_confirmed(_orig_arun, t.name, session_id)
        wrapped.append(t)

    return wrapped


def _check_pending_confirmation(session_id: str, user_message: str) -> str | None:
    """检查用户是否在对 pending 操作做确认/取消。返回响应文本或 None（继续正常流程）。"""
    pending = _pending_confirmations.get(session_id)
    if not pending:
        return None

    msg = user_message.strip()
    if msg in ("确认", "confirm", "yes", "是", "好的", "可以", "ok"):
        # 保留 pending，下次 tool 调用时会执行
        return None  # 继续走 agent 流程，tool guard 检测到 pending 会放行
    elif msg in ("取消", "cancel", "no", "否", "不要", "算了"):
        del _pending_confirmations[session_id]
        return "已取消操作。"
    else:
        # 用户说了别的内容，取消 pending 并按新消息处理
        del _pending_confirmations[session_id]
        return None


# ── MCP Server 进程启动配置 ─────────────────────────────────────────

# MCP 子进程需要继承的环境变量
def _mcp_inherit_env() -> dict[str, str]:
    env = {
        "PYTHONPATH": os.path.dirname(os.path.dirname(__file__)),
        "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
        "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE", "1"),
        "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE", "1"),
    }
    for key in ("DEEPSEEK_API_KEY", "FEISHU_APP_ID", "FEISHU_APP_SECRET",
                "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


MCP_SERVER_CONFIG: dict[str, dict[str, Any]] = {
    "hris": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.hris.server"],
        "env": _mcp_inherit_env(),
    },
    "feishu": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.feishu.server"],
        "env": _mcp_inherit_env(),
    },
    "approval": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.approval.server"],
        "env": _mcp_inherit_env(),
    },
    "knowledge": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_servers.knowledge.server"],
        "env": _mcp_inherit_env(),
    },
}


class HRAgent:
    """HR 智能助手 Agent — 编排层核心。

    链路: 用户输入 → 意图分类 → Tool 路由 → LLM(DeepSeek) 选 Tool → MCP 执行 → 生成回复。
    """

    def __init__(
        self,
        memory_mgr: MemoryManager | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
    ):
        self._memory_mgr = memory_mgr or memory_manager
        self._llm = ChatOpenAI(
            model=config.llm.chat_model,
            api_key=llm_api_key or config.llm.api_key,
            base_url=llm_base_url or config.llm.base_url,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        self._mcp_client: MultiServerMCPClient | None = None
        self._all_tools: list[BaseTool] = []

    # ── 生命周期 ─────────────────────────────────────────────────

    async def start(self):
        """连接 MCP Server，加载全部 Tool。"""
        logger.info("Starting HR Agent, connecting to MCP servers...")
        # tool_name_prefix=True: tool 名用 server_toolname (下划线)
        # 避免 DeepSeek API 拒绝 . 号在 function name 中
        self._mcp_client = MultiServerMCPClient(MCP_SERVER_CONFIG, tool_name_prefix=True)
        self._all_tools = await self._mcp_client.get_tools()
        logger.info("Loaded %d tools from %d MCP servers", len(self._all_tools), len(MCP_SERVER_CONFIG))

    async def stop(self):
        """关闭 MCP 连接和记忆管理器。"""
        if self._mcp_client:
            try:
                await self._mcp_client.close()
            except AttributeError:
                pass
            self._mcp_client = None
        await self._memory_mgr.close()

    # ── 核心调用 ─────────────────────────────────────────────────

    async def chat(
        self,
        user_message: str,
        session_id: str = "default",
        user_id: int = 0,
        user_role: str = "employee",
    ) -> str:
        """完整对话链路，返回最终回复文本。"""
        # 0. 热加载 Skill（检测文件变化）
        skill_manager.check_reload()

        # 1. 意图分类
        intent = await intent_classifier.classify(user_message)

        # 2. Tool 路由
        tools = route_tools(intent, self._all_tools)

        # 3. 自定义 Skill 匹配（热插拔核心）
        matched_skill = skill_manager.match(user_message)
        skill_tools: list[BaseTool] = []
        if matched_skill and matched_skill.tools:
            all_names = {t.name for t in self._all_tools}
            skill_tool_names = skill_manager.get_tool_names(matched_skill, all_names)
            skill_tools = [t for t in self._all_tools if t.name in skill_tool_names]
            # 合并 Tool（去重）
            existing_names = {t.name for t in tools}
            for st in skill_tools:
                if st.name not in existing_names:
                    tools.append(st)

        # 4. general_chat 且无 Skill 匹配 → 无需 Tool，直接 LLM 回复
        entity = intent.entity if intent.entity else None
        if intent.intent == "general_chat" and not matched_skill:
            return await self._direct_reply(user_message, user_role, entity)

        # 5. 无可用 Tool → 用 Skill 上下文直接回复
        if not tools:
            return await self._direct_reply_with_skill(user_message, user_role, matched_skill, entity)

        # 6. 身份绑定：非 admin 用户强制 identity 参数 = user_id
        tools = _apply_identity_guard(tools, user_id, user_role)

        # 6.5 写入工具确认守卫
        tools = _apply_confirmation_guard(tools, session_id)

        # 7. 构建 memory（每次调用独立，避免并发覆盖实例属性）
        memory = self._memory_mgr.create_summary_memory()
        await self._memory_mgr.load_context(session_id, memory)

        # 8. 构建 Prompt（注入 entity 上下文 + Skill Prompt）
        prompt = self._build_prompt_with_skill(user_role, matched_skill, entity)
        user_input = user_message
        if matched_skill and matched_skill.reply_hint:
            user_input = f"{user_message}\n\n[系统提示：{matched_skill.reply_hint}]"

        # 9. 创建 AgentExecutor（局部变量，不共享给其他并发请求）
        agent = create_tool_calling_agent(
            llm=self._llm,
            tools=tools,
            prompt=prompt,
        )
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            memory=memory,
            max_iterations=config.agent_max_iterations,
            verbose=True,
            handle_parsing_errors=True,
        )

        # 10. 执行
        result = await agent_executor.ainvoke({"input": user_input})
        output = result.get("output", "")

        # 9. 持久化对话
        await self._memory_mgr.save_turn(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            assistant_message=output,
            intent=intent.intent,
        )

        return output

    async def chat_stream(
        self,
        user_message: str,
        session_id: str = "default",
        user_id: int = 0,
        user_role: str = "employee",
    ) -> AsyncIterator[str]:
        """流式对话 — 逐 token 产出回复。"""
        # 0. 热加载 Skill
        skill_manager.check_reload()

        # 1. 意图分类
        intent = await intent_classifier.classify(user_message)

        # 2. Tool 路由
        tools = route_tools(intent, self._all_tools)

        # 3. 自定义 Skill 匹配
        matched_skill = skill_manager.match(user_message)
        if matched_skill and matched_skill.tools:
            all_names = {t.name for t in self._all_tools}
            skill_tool_names = skill_manager.get_tool_names(matched_skill, all_names)
            skill_tools = [t for t in self._all_tools if t.name in skill_tool_names]
            existing_names = {t.name for t in tools}
            for st in skill_tools:
                if st.name not in existing_names:
                    tools.append(st)

        # 4. general_chat 且无 Skill 匹配 → 直接 LLM 流式回复
        entity = intent.entity if intent.entity else None
        if (intent.intent == "general_chat" and not matched_skill) or not tools:
            async for chunk in self._direct_reply_stream_with_skill(user_message, user_role, matched_skill, entity):
                yield chunk
            return

        # 4.5 身份绑定：非 admin 用户强制 identity 参数 = user_id
        tools = _apply_identity_guard(tools, user_id, user_role)

        # 4.6 写入工具确认守卫
        tools = _apply_confirmation_guard(tools, session_id)

        # 5. 构建 memory + prompt（局部变量，避免并发覆盖）
        memory = self._memory_mgr.create_summary_memory()
        await self._memory_mgr.load_context(session_id, memory)
        prompt = self._build_prompt_with_skill(user_role, matched_skill, entity)
        user_input = user_message
        if matched_skill and matched_skill.reply_hint:
            user_input = f"{user_message}\n\n[系统提示：{matched_skill.reply_hint}]"

        # 6. 创建 AgentExecutor（局部变量）
        agent = create_tool_calling_agent(llm=self._llm, tools=tools, prompt=prompt)
        agent_executor = AgentExecutor(
            agent=agent, tools=tools, memory=memory,
            max_iterations=config.agent_max_iterations,
            verbose=True, handle_parsing_errors=True,
        )

        # 7. 使用 astream_events 实现 token 级流式
        full_output = ""
        async for event in agent_executor.astream_events(
            {"input": user_input}, version="v2",
        ):
            kind = event.get("event", "")
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    full_output += chunk.content
                    yield chunk.content

        # 8. 如果流式没产出，回退 ainvoke
        if not full_output:
            result = await agent_executor.ainvoke({"input": user_input})
            full_output = result.get("output", "")
            yield full_output

        # 9. 持久化
        await self._memory_mgr.save_turn(
            session_id=session_id, user_id=user_id,
            user_message=user_message, assistant_message=full_output,
            intent=intent.intent,
        )

    # ── 内部方法 ─────────────────────────────────────────────────

    def _build_system_text(self, user_role: str, entity: dict | None = None) -> str:
        system_text = HR_SYSTEM_PROMPT
        if user_role == "interviewer":
            system_text += "\n## 当前用户：面试者\n你只能提供面试相关信息、公司介绍和福利政策。"
        elif user_role == "employee":
            system_text += "\n## 当前用户：员工\n你可以查询该员工自己的信息，以及所有公开的制度政策。"
        elif user_role == "hr_admin":
            system_text += "\n## 当前用户：HR 管理员\n你有权查询管辖范围内员工信息、进行审批操作。请注意合规。"
        # 注入 entity 上下文，帮助 LLM 理解用户具体意图
        if entity:
            parts = []
            etype = entity.get("type", "")
            if etype:
                type_labels = {
                    "salary": "薪资查询", "attendance": "考勤查询", "leave_balance": "假期余额",
                    "calendar": "日程查询", "task": "任务查询", "profile": "个人信息",
                    "employee": "员工信息", "team": "团队成员", "org": "组织架构",
                    "benefit": "福利政策", "company": "公司介绍", "doc": "文档查询",
                    "leave": "请假/年假", "interview": "面试相关", "onboarding": "入职相关",
                    "approve": "审批通过", "reject": "审批驳回",
                    "query_my": "我的审批", "query_pending": "待审批",
                    "mail": "邮件", "sheet": "表格", "approval": "发起审批",
                }
                label = type_labels.get(etype, etype)
                parts.append(f"用户意图: {label}")
            for key in ("name", "topic", "action", "target", "date"):
                val = entity.get(key, "")
                if val:
                    parts.append(f"{key}={val}")
            if parts:
                system_text += f"\n\n## 用户意图上下文\n" + " | ".join(parts)
        return system_text

    def _build_prompt(self, user_role: str, entity: dict | None = None) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_messages([
            ("system", self._build_system_text(user_role, entity)),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

    def _build_prompt_with_skill(self, user_role: str, skill: CustomSkill | None = None, entity: dict | None = None) -> ChatPromptTemplate:
        """构建 Prompt，如有自定义 Skill 匹配则注入 Skill 执行指令，同时注入 entity 上下文。"""
        system_text = self._build_system_text(user_role, entity)
        if skill:
            system_text += f"\n\n## 自定义业务技能: {skill.display_name}\n{skill.system_prompt}"
        return ChatPromptTemplate.from_messages([
            ("system", system_text),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

    async def _direct_reply(self, user_message: str, user_role: str, entity: dict | None = None) -> str:
        response = await self._llm.ainvoke([
            {"role": "system", "content": self._build_system_text(user_role, entity)},
            {"role": "user", "content": user_message},
        ])
        return response.content

    async def _direct_reply_with_skill(
        self, user_message: str, user_role: str, skill: CustomSkill | None, entity: dict | None = None,
    ) -> str:
        """带 Skill 上下文的无 Tool 对话。"""
        system_text = self._build_system_text(user_role, entity)
        if skill:
            system_text += f"\n\n## 自定义业务技能: {skill.display_name}\n{skill.system_prompt}"
        user_input = user_message
        if skill and skill.reply_hint:
            user_input = f"{user_message}\n\n[系统提示：{skill.reply_hint}]"
        response = await self._llm.ainvoke([
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_input},
        ])
        return response.content

    async def _direct_reply_stream(self, user_message: str, user_role: str, entity: dict | None = None) -> AsyncIterator[str]:
        stream = self._llm.astream([
            {"role": "system", "content": self._build_system_text(user_role, entity)},
            {"role": "user", "content": user_message},
        ])
        async for chunk in stream:
            if chunk.content:
                yield chunk.content

    async def _direct_reply_stream_with_skill(
        self, user_message: str, user_role: str, skill: CustomSkill | None, entity: dict | None = None,
    ) -> AsyncIterator[str]:
        """带 Skill 上下文的流式无 Tool 对话。"""
        system_text = self._build_system_text(user_role, entity)
        if skill:
            system_text += f"\n\n## 自定义业务技能: {skill.display_name}\n{skill.system_prompt}"
        user_input = user_message
        if skill and skill.reply_hint:
            user_input = f"{user_message}\n\n[系统提示：{skill.reply_hint}]"
        stream = self._llm.astream([
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_input},
        ])
        async for chunk in stream:
            if chunk.content:
                yield chunk.content


# ── 全局单例 ────────────────────────────────────────────────────────

class _LazyHRAgent:
    """Lazy proxy — defers HRAgent creation until first use."""

    def __init__(self):
        self._instance: HRAgent | None = None

    def _ensure(self) -> HRAgent:
        if self._instance is None:
            self._instance = HRAgent()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._ensure(), name)


hr_agent: HRAgent = _LazyHRAgent()  # type: ignore
