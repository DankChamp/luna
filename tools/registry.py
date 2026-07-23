from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from core.providers.base import ToolCall


ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: ToolHandler
    required: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef):
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    @property
    def definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": t.parameters,
                        "required": t.required,
                    },
                },
            }
            for t in self._tools.values()
        ]

    async def execute(self, call: ToolCall) -> str:
        tool = self._tools.get(call.name)
        if not tool:
            return f"Error: unknown tool '{call.name}'"
        try:
            result = await tool.handler(**call.arguments)
            return str(result)
        except Exception as e:
            return f"Error executing {call.name}: {e}"

    def register_all(self, *tools: ToolDef):
        for t in tools:
            self.register(t)
