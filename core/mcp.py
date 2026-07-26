from __future__ import annotations
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from tools.registry import ToolRegistry, ToolDef


@dataclass
class MCPServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict


class MCPServer:
    def __init__(self, name: str, config: MCPServerConfig):
        self.name = name
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._tools: list[MCPTool] = []
        self._request_id = 0

    async def start(self):
        env = os.environ.copy()
        for k, v in self.config.env.items():
            env[k] = os.path.expandvars(v)

        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self._initialize()

    async def _initialize(self):
        result = await self._request("initialize", {
            "protocolVersion": "0.1.0",
            "capabilities": {},
            "clientInfo": {"name": "luna", "version": "1.0.0"},
        })
        if result:
            await self._request("notifications/initialized", {})
            tools_result = await self._request("tools/list", {})
            if tools_result and "tools" in tools_result:
                for t in tools_result["tools"]:
                    self._tools.append(MCPTool(
                        name=f"{self.name}_{t['name']}",
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}),
                    ))

    async def _request(self, method: str, params: dict) -> dict | None:
        if self._process is None or self._process.stdin is None:
            return None
        self._request_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        try:
            self._process.stdin.write((json.dumps(req) + "\n").encode())
            await self._process.stdin.drain()
            line = await asyncio.wait_for(self._process.stdout.readline(), timeout=30)
            resp = json.loads(line.decode())
            if "result" in resp:
                return resp["result"]
            if "error" in resp:
                return None
        except Exception:
            pass
        return None

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        stripped = tool_name[len(self.name) + 1:]
        result = await self._request("tools/call", {
            "name": stripped,
            "arguments": arguments,
        })
        if result is None:
            return f"[MCP Error: no response from {self.name}/{stripped}]"
        content = result.get("content", [])
        parts = []
        for c in content:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts) if parts else str(result)

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools)

    async def stop(self):
        if self._process:
            self._process.kill()
            await self._process.wait()
            self._process = None


class MCPManager:
    def __init__(self):
        self.servers: dict[str, MCPServer] = {}

    async def add_server(self, name: str, config: MCPServerConfig):
        if name in self.servers:
            await self.servers[name].stop()
        server = MCPServer(name, config)
        await server.start()
        self.servers[name] = server
        return server

    def register_tools(self, registry: ToolRegistry):
        for srv_name, server in self.servers.items():
            for mcp_tool in server.tools:
                srv = server
                handler = lambda t_name=mcp_tool.name, s=srv, **kw: s.call_tool(t_name, kw)
                td = ToolDef(
                    name=mcp_tool.name,
                    description=mcp_tool.description,
                    parameters=mcp_tool.input_schema.get("properties", {}),
                    required=mcp_tool.input_schema.get("required", []),
                    handler=handler,
                )
                registry.register(td)

    async def shutdown_all(self):
        for server in self.servers.values():
            await server.stop()
        self.servers.clear()
