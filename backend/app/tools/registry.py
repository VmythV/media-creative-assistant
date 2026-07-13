"""Tool Registry：MCP 兼容的工具定义与进程内执行。

工具定义（name / description / JSON Schema 参数）遵循 MCP tool 规范，
第二阶段可无改动包装为独立 MCP Server。
"""

import inspect
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Any]


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._log_sink: Callable[[str, str, str, str, str | None], None] | None = None

    def register(self, name: str, description: str, parameters: dict) -> Callable:
        def decorator(fn: Callable) -> Callable:
            if name in self._tools:
                raise ValueError(f"tool already registered: {name}")
            self._tools[name] = ToolDef(name, description, parameters, fn)
            return fn

        return decorator

    def get(self, name: str) -> ToolDef:
        return self._tools[name]

    def list(self) -> list[dict]:
        """MCP 风格的工具清单。"""
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in self._tools.values()
        ]

    def set_log_sink(self, sink: Callable[[str, str, str, str, str | None], None]) -> None:
        """sink(task_id, tool, input_summary, output_summary, error)"""
        self._log_sink = sink

    async def execute(self, name: str, arguments: dict, task_id: str | None = None) -> ToolResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        input_summary = _summarize(arguments)
        if name not in self._tools:
            result = ToolResult(tool=name, ok=False, error=f"unknown tool: {name}")
            self._log(task_id, name, input_summary, "", result.error)
            return result
        tool = self._tools[name]
        try:
            out = tool.handler(**arguments)
            if inspect.isawaitable(out):
                out = await out
            result = ToolResult(tool=name, ok=True, output=out)
            self._log(task_id, name, input_summary, _summarize(out), None)
        except Exception as e:  # noqa: BLE001 - 工具失败必须被捕获并记录
            result = ToolResult(tool=name, ok=False, error=f"{type(e).__name__}: {e}")
            self._log(task_id, name, input_summary, "", result.error)
        return result

    def _log(self, task_id: str, tool: str, inp: str, out: str, err: str | None) -> None:
        if self._log_sink is not None:
            try:
                self._log_sink(task_id, tool, inp, out, err)
            except Exception:  # noqa: BLE001 - 日志失败不影响工具执行
                pass


def _summarize(value: Any, limit: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


registry = ToolRegistry()
