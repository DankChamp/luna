from __future__ import annotations
import logging
from typing import Any, Callable, Awaitable, Protocol
from dataclasses import dataclass

from core.permissions import PermissionEvaluator
from core.providers.base import ToolCall

logger = logging.getLogger("luna.tools.executor")


@dataclass
class ExecutionResult:
    """Result of tool execution."""
    success: bool
    result: Any = None
    error: str | None = None
    snapshot_id: str | None = None


class ToolsProtocol(Protocol):
    """Protocol for tools registry - avoids circular import."""
    def get(self, name: str) -> Any: ...
    @property
    def definitions(self) -> list[dict]: ...
    def set_permissions(self, evaluator: Any) -> None: ...


class ToolExecutor:
    """
    Full tool execution pipeline.
    
    Handles:
    - Permission evaluation (allow/ask/deny)
    - Pre-execution snapshots (for undo/redo)
    - Tool execution via provided tools registry
    - LSP diagnostics refresh
    - Result formatting and truncation
    - Post-execution callbacks
    """
    
    def __init__(
        self,
        tools: ToolsProtocol,
        permissions: Any | None = None,
        lsp_diagnostics_callback: Callable[[], Awaitable[None]] | None = None,
        format_callback: Callable[[str], str] | None = None,
    ):
        from core.permissions import PermissionEvaluator
        self.tools = tools
        self.permissions = permissions or __import__('core.permissions').permissions.PermissionEvaluator()
        self._lsp_callback = lsp_diagnostics_callback
        self._format_callback = format_callback
        self._history: list[dict] = []
        self._history_index = -1
    
    async def execute(self, tool_call: ToolCall) -> str:
        """
        Execute a tool call through the full pipeline.
        
        Returns:
            Formatted result string for the agent
        """
        tool_name = tool_call.name
        tool = self.tools.get(tool_name)
        
        if not tool:
            return f"Error: Unknown tool '{tool_name}'"
        
        # Check permissions
        command_arg = tool_call.arguments.get("command") if tool_name == "bash" else None
        perm_result = self.permissions.evaluate(tool_name, command=command_arg)
        if perm_result == "deny":
            return f"Error: Tool '{tool_name}' denied by policy"
        elif perm_result == "ask":
            return f"Error: Tool '{tool_name}' requires confirmation (ask mode): {perm_result}"
        
        # Create snapshot before execution (for undo)
        snapshot_id = self._snapshot_before(tool_name, tool_call.arguments)
        
        try:
            # Execute the tool
            result = await tool.handler(**tool_call.arguments)
            
            # Post-execution: LSP diagnostics refresh
            if self._lsp_callback:
                try:
                    await self._lsp_callback()
                except Exception as e:
                    logger.debug("LSP callback failed: %s", e)
            
            # Format result
            formatted = self._format_result(result)
            
            # Record in history
            self._record_execution(tool_name, tool_call.arguments, result, snapshot_id)
            
            return formatted
            
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            error_msg = f"Error executing {tool_name}: {e}"
            return error_msg
    
    def _snapshot_before(self, tool_name: str, arguments: dict) -> str | None:
        """Create snapshot before tool execution for undo support."""
        mutating_tools = {"write", "edit", "bash"}
        if tool_name not in mutating_tools:
            return None
        
        snapshot_id = f"{tool_name}_{len(self._history)}"
        
        snapshot = {
            "id": snapshot_id,
            "tool": tool_name,
            "arguments": arguments,
            "timestamp": __import__("time").time(),
        }
        
        self._history = self._history[:self._history_index + 1]
        self._history.append(snapshot)
        self._history_index = len(self._history) - 1
        
        return snapshot_id
    
    def _record_execution(self, tool_name: str, arguments: dict, result: Any, snapshot_id: str | None):
        """Record execution in history."""
        record = {
            "tool": tool_name,
            "arguments": arguments,
            "result": str(result)[:500],
            "snapshot_id": snapshot_id,
            "timestamp": __import__("time").time(),
        }
        self._history = self._history[:self._history_index + 1]
        self._history.append(record)
        self._history_index = len(self._history) - 1
    
    def _format_result(self, result: Any) -> str:
        """Format tool result for agent consumption."""
        if result is None:
            return "(no output)"
        
        text = str(result)
        
        if self._format_callback:
            text = self._format_callback(text)
        
        max_len = 50_000
        if len(text) > max_len:
            text = text[:max_len] + f"\n... (truncated, {len(result)} total chars)"
        
        return text
    
    def undo_last(self) -> list[str]:
        if self._history_index < 0:
            return ["Nothing to undo"]
        
        snapshot = self._history[self._history_index]
        if snapshot.get("tool") in ("write", "edit"):
            self._history_index -= 1
            return [f"Undid {snapshot['tool']} (snapshot: {snapshot['id']})"]
        
        self._history_index -= 1
        return [f"Undid {snapshot.get('tool', 'action')}"]
    
    def redo_last(self) -> list[str]:
        if self._history_index >= len(self._history) - 1:
            return ["Nothing to redo"]
        
        self._history_index += 1
        snapshot = self._history[self._history_index]
        return [f"Redid {snapshot.get('tool', 'action')} (snapshot: {snapshot['id']})"]
    
    def get_history(self) -> list[dict]:
        """Get execution history."""
        return self._history[:self._history_index + 1]