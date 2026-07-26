from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable, Optional

from core.providers.base import ToolCall
from core.permissions import PermissionEvaluator


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
        self._blocked: set[str] = set()
        self._permissions: PermissionEvaluator = PermissionEvaluator()
        self._edit_history: list[dict] = []
        self._history_index: int = -1
        self.formatter: Optional[Callable[[str], Awaitable[str]]] = None
        self.lsp_diagnostics: Optional[Callable[[str], Awaitable[list[dict]]]] = None

    def register(self, tool: ToolDef):
        self._tools[tool.name] = tool

    def set_blocked(self, names: set[str]):
        self._blocked = names

    def set_permissions(self, evaluator: PermissionEvaluator | None):
        if evaluator is not None:
            self._permissions = evaluator

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
            if t.name not in self._blocked
        ]

    async def execute(self, call: ToolCall) -> str:
        if call.name in self._blocked:
            return f"Error: tool '{call.name}' is not available in the current mode"

        command_arg = call.arguments.get("command") if call.name == "bash" else None
        perm = self._permissions.evaluate(call.name, command=command_arg)
        if perm == "deny":
            return f"Error: tool '{call.name}' is denied by permission rules"
        if perm == "ask":
            return f"Error: tool '{call.name}' requires approval (ask mode not implemented in headless mode)"

        tool = self._tools.get(call.name)
        if not tool:
            return f"Error: unknown tool '{call.name}'"

        snapshot = None
        if call.name in ("write", "edit"):
            snapshot = self._snapshot_before(call)

        try:
            result = await tool.handler(**call.arguments)
            str_result = str(result)
            if snapshot is not None:
                entry = self._edit_history[self._history_index]
                entry["new_content"] = call.arguments.get("content", "")
                if call.name == "edit":
                    p = Path(call.arguments.get("path", "")).expanduser().resolve()
                    try:
                        entry["new_content"] = p.read_text(encoding="utf-8") if p.exists() else ""
                    except Exception:
                        entry["new_content"] = ""
            if call.name in ("write", "edit") and self.formatter:
                path = call.arguments.get("path", "")
                fmt_result = await self.formatter(path)
                if fmt_result:
                    str_result += f"\n[{fmt_result}]"
            if call.name in ("write", "edit") and self.lsp_diagnostics:
                path = call.arguments.get("path", "")
                diags = await self.lsp_diagnostics(path)
                if diags:
                    for d in diags[:5]:
                        msg = d.get("message", "")
                        if msg:
                            str_result += f"\n[LSP] {msg}"
            return str_result
        except Exception as e:
            return f"Error executing {call.name}: {e}"

    def register_all(self, *tools: ToolDef):
        for t in tools:
            self.register(t)

    def _snapshot_before(self, call: ToolCall) -> dict | None:
        path = call.arguments.get("path", "")
        if not path:
            return None
        p = Path(path).expanduser().resolve()
        old_content = ""
        if p.exists():
            try:
                old_content = p.read_text(encoding="utf-8")
            except Exception:
                pass

        if self._history_index < len(self._edit_history) - 1:
            self._edit_history = self._edit_history[: self._history_index + 1]

        entry = {
            "path": str(p),
            "old_content": old_content,
            "new_content": "",
        }
        self._edit_history.append(entry)
        self._history_index = len(self._edit_history) - 1
        return entry

    def undo_last(self) -> list[str]:
        if self._history_index < 0 or not self._edit_history:
            return []
        entry = self._edit_history[self._history_index]
        p = Path(entry["path"])
        try:
            p.write_text(entry["old_content"], encoding="utf-8")
            self._history_index -= 1
            return [str(p)]
        except Exception:
            return []

    def redo_last(self) -> list[str]:
        next_idx = self._history_index + 1
        if next_idx >= len(self._edit_history):
            return []
        entry = self._edit_history[next_idx]
        if not entry["new_content"]:
            return []
        p = Path(entry["path"])
        try:
            p.write_text(entry["new_content"], encoding="utf-8")
            self._history_index = next_idx
            return [str(p)]
        except Exception:
            return []
