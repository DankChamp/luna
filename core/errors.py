from __future__ import annotations
from typing import Any


class LunaError(Exception):
    """Base exception for all Luna errors."""

    def __init__(
        self,
        message: str,
        code: str = "LUNA_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ToolError(LunaError):
    """Error during tool execution."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="TOOL_ERROR", details=details)
        self.tool_name = tool_name
        if details is not None:
            self.details["tool_name"] = tool_name


class PermissionError(LunaError):
    """Permission denied error."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        action: str = "execute",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="PERMISSION_DENIED", details=details)
        self.tool_name = tool_name
        self.action = action
        if details is not None:
            self.details.update({"tool_name": tool_name, "action": action})


class ProviderError(LunaError):
    """AI provider error."""

    def __init__(
        self,
        message: str,
        provider: str,
        model: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="PROVIDER_ERROR", details=details)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        if details is not None:
            self.details.update(
                {
                    "provider": provider,
                    "model": model,
                    "status_code": status_code,
                }
            )


class ConfigError(LunaError):
    """Configuration error."""

    def __init__(
        self,
        message: str,
        config_path: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="CONFIG_ERROR", details=details)
        self.config_path = config_path
        if details is not None and config_path:
            self.details["config_path"] = config_path


class SessionError(LunaError):
    """Session management error."""

    def __init__(
        self,
        message: str,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="SESSION_ERROR", details=details)
        self.session_id = session_id
        if details is not None and session_id:
            self.details["session_id"] = session_id


class ConfigMigrationError(LunaError):
    """Configuration migration error."""

    def __init__(
        self,
        message: str,
        from_path: str,
        to_path: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="CONFIG_MIGRATION_ERROR", details=details)
        self.from_path = from_path
        self.to_path = to_path
        if details is not None:
            self.details.update({"from_path": from_path, "to_path": to_path})


class PluginError(LunaError):
    """Plugin system error."""

    def __init__(
        self,
        message: str,
        plugin_name: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="PLUGIN_ERROR", details=details)
        self.plugin_name = plugin_name
        if details is not None:
            self.details["plugin_name"] = plugin_name


class SubagentError(LunaError):
    """Subagent execution error."""

    def __init__(
        self,
        message: str,
        subagent_name: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="SUBAGENT_ERROR", details=details)
        self.subagent_name = subagent_name
        if details is not None:
            self.details["subagent_name"] = subagent_name


class ValidationError(LunaError):
    """Input validation error."""

    def __init__(
        self,
        message: str,
        field: str,
        value: Any = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="VALIDATION_ERROR", details=details)
        self.field = field
        self.value = value
        if details is not None:
            self.details.update({"field": field, "value": str(value) if value else None})


def is_luna_error(error: Exception) -> bool:
    """Check if an exception is a LunaError."""
    return isinstance(error, LunaError)


def get_error_code(error: Exception) -> str:
    """Extract error code from exception."""
    if isinstance(error, LunaError):
        return error.code
    return "UNKNOWN_ERROR"


def format_error(error: Exception) -> dict[str, Any]:
    """Format exception for JSON output."""
    if isinstance(error, LunaError):
        return error.to_dict()
    return {
        "code": "UNKNOWN_ERROR",
        "message": str(error),
        "details": {"type": type(error).__name__},
    }