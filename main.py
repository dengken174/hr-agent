"""HR Agent 交互式 CLI 入口。"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from agent.executor import hr_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")


async def interactive():
    print("=" * 50)
    print("  HR 智能助手 Agent — CLI 测试")
    print("  输入 /quit 退出，/reset 清空会话")
    print("=" * 50)

    print("\n正在连接 MCP Servers...")
    await hr_agent.start()
    print("就绪！\n")

    session_id = "cli-test"
    user_id = 1001
    user_role = "employee"

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue

            if user_input == "/quit":
                break
            if user_input == "/reset":
                from agent.memory import memory_manager
                await memory_manager.clear_session(session_id)
                print("会话已清空。\n")
                continue
            if user_input == "/hr":
                user_role = "hr_admin"
                user_id = 2001
                print(f"已切换为 HR 管理员模式 (user_id={user_id}, role={user_role})\n")
                continue
            if user_input == "/emp":
                user_role = "employee"
                user_id = 1001
                print(f"已切换为员工模式 (user_id={user_id}, role={user_role})\n")
                continue

            print("Agent: ", end="", flush=True)
            try:
                reply = await hr_agent.chat(
                    user_message=user_input,
                    session_id=session_id,
                    user_id=user_id,
                    user_role=user_role,
                )
                print(reply + "\n")
            except Exception as e:
                logger.exception("Agent 调用失败")
                print(f"[错误] {e}\n")
    finally:
        await hr_agent.stop()


async def quick_test():
    """无 MCP 的快速功能测试 — 测试意图分类和路由逻辑。"""
    from agent.intent import intent_classifier

    tests = [
        "我这个月工资多少",
        "张三入职时间是什么时候",
        "年假怎么申请",
        "帮我请明天的假",
        "通过张三的请假",
        "你好",
    ]
    print("意图分类测试：")
    for msg in tests:
        result = await intent_classifier.classify(msg)
        print(f"  {msg:30s} → {result.intent}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        asyncio.run(quick_test())
    else:
        asyncio.run(interactive())
