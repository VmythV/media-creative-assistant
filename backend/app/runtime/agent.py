"""Agent Runtime：Session 管理 + 通用 tool-use 循环。

Planning/Understanding 等 Agent 共享该运行时；Session 持久化到 SQLite，
避免无状态调用导致上下文丢失（设计文档 5.3）。
"""

import json
import logging
import uuid

from app.providers import get_llm_provider
from app.store.db import db_session
from app.store.models import AgentSession
from app.tools.registry import registry

logger = logging.getLogger("mca.agent")

MAX_TOOL_ITERATIONS = 8


class Session:
    """Agent 会话：对话历史 + 上下文，持久化到 agent_sessions 表。"""

    def __init__(self, session_id: str | None = None):
        self.id = session_id or uuid.uuid4().hex[:16]
        self.messages: list[dict] = []
        self.context: dict = {}
        if session_id:
            self._load()

    def _load(self) -> None:
        with db_session() as db:
            row = db.get(AgentSession, self.id)
            if row:
                self.messages = list(row.messages)
                self.context = dict(row.context)

    def save(self) -> None:
        with db_session() as db:
            row = db.get(AgentSession, self.id)
            if row is None:
                row = AgentSession(id=self.id)
                db.add(row)
            row.messages = self.messages
            row.context = self.context
            db.commit()

    def append(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})


async def run_tool_loop(
    session: Session,
    user_message: str,
    *,
    system: str | None = None,
    tool_names: list[str] | None = None,
    json_mode: bool = False,
) -> str:
    """通用 tool-use 循环：模型可调用注册工具，直到产出最终文本。"""
    llm = get_llm_provider()
    if system and not any(m["role"] == "system" for m in session.messages):
        session.messages.insert(0, {"role": "system", "content": system})
    session.append("user", user_message)

    tools = None
    if tool_names:
        all_tools = {t["name"]: t for t in registry.list()}
        tools = [
            {
                "type": "function",
                "function": {
                    "name": n,
                    "description": all_tools[n]["description"],
                    "parameters": all_tools[n]["inputSchema"],
                },
            }
            for n in tool_names
            if n in all_tools
        ]

    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await llm.chat(session.messages, tools=tools, json_mode=json_mode)
        if not resp.get("tool_calls"):
            session.append("assistant", resp["content"])
            session.save()
            return resp["content"]
        # 记录 assistant 的工具调用并执行
        session.messages.append(
            {
                "role": "assistant",
                "content": resp["content"] or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in resp["tool_calls"]
                ],
            }
        )
        for tc in resp["tool_calls"]:
            try:
                arguments = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
            except json.JSONDecodeError:
                arguments = {}
            result = await registry.execute(tc["name"], arguments)
            session.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(
                        result.output if result.ok else {"error": result.error},
                        ensure_ascii=False,
                        default=str,
                    )[:4000],
                }
            )
    session.save()
    raise RuntimeError(f"超过最大工具调用轮数 ({MAX_TOOL_ITERATIONS})")
