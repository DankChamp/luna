# Luna Plugin Development Guide

## Overview

Luna supports plugins to extend functionality without modifying core code. Plugins can:
- Provide new tools
- Add custom slash commands
- Modify configuration
- Integrate with external services

## Plugin Types

### 1. Directory-Based Plugins (Local)

Create a directory under `~/.luna/plugins/<name>/` with a `plugin.py` file:

```
~/.luna/plugins/
└── my_plugin/
    ├── plugin.py          # Main plugin class
    ├── pyproject.toml     # Optional: for dependencies
    └── README.md          # Optional: documentation
```

### 2. Entry Point Plugins (Distributable)

Publish as a PyPI package with entry points in `pyproject.toml`:

```toml
[project.entry-points."luna.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
```

## Plugin API

### Base Plugin Class

```python
from core.plugins import Plugin

class MyPlugin(Plugin):
    def __init__(self, name: str):
        super().__init__(name)
        self.version = "1.0.0"
        self.description = "What this plugin does"
    
    def initialize(self, registry: ToolRegistry) -> None:
        """Called once when plugin is loaded."""
        pass
    
    def shutdown(self) -> None:
        """Cleanup when plugin is unloaded."""
        pass
```

### Tool Plugin

```python
from core.plugins import ToolPlugin
from tools.registry import ToolDef

class MyToolPlugin(ToolPlugin):
    def __init__(self, name: str = "my_plugin"):
        super().__init__(name)
        self.description = "Provides custom tools"
        self.tools = [
            ToolDef(
                name="my_tool",
                description="Does something useful",
                parameters={
                    "input": {"type": "string", "description": "Input text"}
                },
                required=["input"],
                handler=self.my_handler,
            )
        ]
    
    async def my_handler(self, input: str) -> str:
        return f"Processed: {input}"
```

## Complete Example

### Directory Structure

```
my_plugin/
├── plugin.py
└── pyproject.toml
```

### plugin.py

```python
"""Example plugin: Weather tool."""
from __future__ import annotations

import httpx
from core.plugins import ToolPlugin
from tools.registry import ToolDef


class WeatherPlugin(ToolPlugin):
    """Plugin that provides weather information."""

    def __init__(self, name: str = "weather"):
        super().__init__(name)
        self.version = "1.0.0"
        self.description = "Get current weather for a location"
        self.tools = [
            ToolDef(
                name="weather",
                description="Get current weather for a city",
                parameters={
                    "city": {
                        "type": "string",
                        "description": "City name (e.g., 'San Francisco')"
                    },
                    "units": {
                        "type": "string",
                        "description": "Temperature units",
                        "enum": ["metric", "imperial"],
                        "default": "metric"
                    }
                },
                required=["city"],
                handler=self.get_weather,
            )
        ]

    async def get_weather(self, city: str, units: str = "metric") -> str:
        """Fetch weather from OpenWeatherMap API."""
        # In a real plugin, you'd use an API key from config
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "q": city,
                        "units": units,
                        "appid": "YOUR_API_KEY"  # Should come from config
                    },
                    timeout=10.0
                )
                if resp.status_code == 200:
                    data = resp.json()
                    temp = data["main"]["temp"]
                    desc = data["weather"][0]["description"]
                    unit = "°C" if units == "metric" else "°F"
                    return f"Weather in {city}: {temp}{unit}, {desc}"
                return f"Error: {resp.status_code}"
            except Exception as e:
                return f"Error fetching weather: {e}"
```

### pyproject.toml (for entry point distribution)

```toml
[project]
name = "luna-weather-plugin"
version = "1.0.0"
description = "Weather tool for Luna"
dependencies = ["httpx>=0.27"]

[project.entry-points."luna.plugins"]
weather = "luna_weather_plugin:WeatherPlugin"
```

## Installation

### Local Plugin

```bash
mkdir -p ~/.luna/plugins/weather
cp plugin.py ~/.luna/plugins/weather/
```

Then in Luna:
```
/plugin load weather
```

### Distributed Plugin

```bash
pip install luna-weather-plugin
```

Then in Luna:
```
/plugin load weather
```

## Plugin Discovery

Luna discovers plugins from:
1. **Local directories**: `~/.luna/plugins/`, `~/.config/luna/plugins/`
2. **Entry points**: Any installed package with `luna.plugins` entry point

Use `/plugin list` to see available/loaded plugins.

## Best Practices

1. **Handle errors gracefully** - Return error strings, don't raise exceptions
2. **Use async/await** - All handlers must be async
3. **Validate inputs** - Use JSON Schema in ToolDef parameters
4. **Respect permissions** - Tools inherit agent's permission settings
5. **Clean up resources** - Implement `shutdown()` for connections/files
6. **Version your plugin** - Include `version` attribute
7. **Document your tools** - Clear descriptions help the agent use them correctly

## Accessing Luna Services

Plugins receive the `ToolRegistry` in `initialize()`. For other services (config, router, etc.), use the `core` module:

```python
from core import get_logger, config_home

logger = get_logger("my_plugin")
config_dir = config_home()
```

## Testing Plugins

```python
import pytest
from core.plugins import PluginManager
from tools import create_default_registry

async def test_my_plugin():
    mgr = PluginManager(["/path/to/plugins"])
    plugins = mgr.load_all_plugins()
    
    assert "my_plugin" in plugins
    
    tools = create_default_registry()
    mgr.initialize_all(tools)
    
    # Test tool execution
    result = await tools.execute(ToolCall(id="1", name="my_tool", arguments={"input": "test"}))
    assert "Processed" in result
```

## Example Plugins

See `examples/plugins/` for working examples:
- `hello_plugin` - Simple greeting tool
- More examples coming soon...

## Publishing

To share your plugin:
1. Create a GitHub repo with `pyproject.toml` and entry point
2. Publish to PyPI: `pip publish`
3. Users install with `pip install your-plugin`
4. Plugin auto-discovered via entry points