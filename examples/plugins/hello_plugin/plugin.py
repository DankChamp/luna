"""Example Luna plugin - Hello world tool."""
from __future__ import annotations

from core.plugins import ToolPlugin
from tools.registry import ToolDef


class HelloPlugin(ToolPlugin):
    """Example plugin that provides a hello tool."""

    def __init__(self, name: str = "hello_plugin"):
        super().__init__(name)
        self.description = "Example plugin with a hello tool"
        self.tools = [
            ToolDef(
                name="hello",
                description="Say hello to someone",
                parameters={
                    "name": {
                        "type": "string",
                        "description": "Name to greet",
                    },
                    "greeting": {
                        "type": "string",
                        "description": "Greeting to use (optional)",
                    },
                },
                required=["name"],
                handler=self.hello_handler,
            )
        ]

    async def hello_handler(self, name: str, greeting: str = "Hello") -> str:
        """Say hello to someone."""
        return f"{greeting}, {name}! 👋 This is from the hello_plugin example."
