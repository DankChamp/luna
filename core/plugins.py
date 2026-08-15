from __future__ import annotations
import importlib.util
import sys
from dataclasses import field
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional

from tools.registry import ToolDef, ToolRegistry
from core.errors import PluginError


class Plugin:
    """Base class for plugins."""

    def __init__(self, name: str):
        self.name = name
        self.version = "1.0.0"
        self.description = ""

    def initialize(self, registry: ToolRegistry) -> None:
        """Initialize the plugin. Called once on load."""
        pass

    def shutdown(self) -> None:
        """Clean up plugin resources."""
        pass


class ToolPlugin(Plugin):
    """Plugin that provides one or more tools."""

    def __init__(self, name: str):
        super().__init__(name)
        self.tools: list[ToolDef] = []

    def get_tools(self) -> list[ToolDef]:
        """Return tools provided by this plugin."""
        return self.tools


class PluginManager:
    """Manages plugin discovery and loading."""

    def __init__(self, plugin_dirs: list[str | Path] | None = None):
        self.plugin_dirs = [Path(d).expanduser().resolve() for d in (plugin_dirs or [])]
        self._plugins: dict[str, Plugin] = {}
        self._tool_to_plugin: dict[str, str] = {}

    def add_plugin_dir(self, path: str | Path) -> None:
        """Add a plugin directory."""
        p = Path(path).expanduser().resolve()
        if p not in self.plugin_dirs:
            self.plugin_dirs.append(p)

    def discover_plugins(self) -> list[str]:
        """Discover all available plugins."""
        discovered = []
        for dir_path in self.plugin_dirs:
            if not dir_path.exists():
                continue
            for item in sorted(dir_path.iterdir()):
                if item.is_dir() and (item / "plugin.py").exists():
                    discovered.append(item.name)
        return discovered

    def load_plugin(self, name: str) -> Plugin | None:
        """Load a plugin by name."""
        for dir_path in self.plugin_dirs:
            plugin_path = dir_path / name
            if not plugin_path.exists():
                continue
            plugin_file = plugin_path / "plugin.py"
            if not plugin_file.exists():
                continue

            try:
                spec = importlib.util.spec_from_file_location(f"luna_plugin_{name}", plugin_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)

                # Look for Plugin class
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, Plugin) and attr is not Plugin:
                        plugin = attr(name)
                        self._plugins[name] = plugin
                        return plugin

            except Exception as e:
                raise PluginError(f"Failed to load plugin '{name}': {e}", name)

        return None

    def load_all_plugins(self) -> dict[str, Plugin]:
        """Load all discovered plugins."""
        for name in self.discover_plugins():
            try:
                self.load_plugin(name)
            except Exception as e:
                print(f"Warning: Failed to load plugin '{name}': {e}")
        return self._plugins.copy()

    def get_plugin(self, name: str) -> Plugin | None:
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        return list(self._plugins.keys())

    def unload_plugin(self, name: str) -> bool:
        if name in self._plugins:
            plugin = self._plugins[name]
            try:
                plugin.shutdown()
            except Exception:
                pass
            del self._plugins[name]
            # Remove tool mappings
            tools_to_remove = [t for t, p in self._tool_to_plugin.items() if p == name]
            for t in tools_to_remove:
                del self._tool_to_plugin[t]
            return True
        return False

    def initialize_all(self, registry: ToolRegistry) -> None:
        """Initialize all loaded plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.initialize(registry)
                # Register tools
                for tool in getattr(plugin, 'tools', []):
                    registry.register(tool)
                    self._tool_to_plugin[tool.name] = plugin.name
            except Exception as e:
                raise PluginError(f"Failed to initialize plugin '{plugin.name}': {e}", plugin.name)


class FunctionPlugin(Plugin):
    """Plugin that wraps a simple function as a tool."""

    def __init__(
        self,
        name: str,
        func: Callable[..., Awaitable[str]],
        description: str,
        parameters: dict[str, Any],
        required: list[str] = field(default_factory=list),
    ):
        super().__init__(name)
        self.tools = [
            ToolDef(
                name=name,
                description=description,
                parameters=parameters,
                required=required,
                handler=func,
            )
        ]


def create_function_plugin(
    name: str,
    func: Callable[..., Awaitable[str]],
    description: str,
    parameters: dict[str, Any],
    required: list[str] = field(default_factory=list),
) -> FunctionPlugin:
    """Create a plugin from a simple async function."""
    return FunctionPlugin(name, func, description, parameters, required)


def create_tool_plugin(
    name: str,
    tools: list[ToolDef],
    description: str = "",
) -> ToolPlugin:
    """Create a plugin from a list of ToolDefs."""
    plugin = ToolPlugin(name)
    plugin.description = description
    plugin.tools = tools
    return plugin


# Example plugin structure for users:
"""
# ~/.luna/plugins/my_plugin/plugin.py
from core.tools.registry import ToolDef
from core.plugins import ToolPlugin

class MyPlugin(ToolPlugin):
    def __init__(self):
        super().__init__("my_plugin")
        self.description = "My custom tools"
        self.tools = [
            ToolDef(
                name="my_tool",
                description="Does something cool",
                parameters={
                    "input": {"type": "string", "description": "Input text"}
                },
                required=["input"],
                handler=self.my_handler,
            )

    async def my_handler(self, input: str) -> str:
        return f"Processed: {input}"
"""