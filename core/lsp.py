from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Optional


BUILTIN_LSP: dict[str, dict] = {
    "pyright": {
        "command": ["pyright-langserver", "--stdio"],
        "extensions": [".py", ".pyi"],
    },
    "typescript": {
        "command": ["npx", "typescript-language-server", "--stdio"],
        "extensions": [".ts", ".tsx", ".js", ".jsx"],
    },
    "ruff": {
        "command": ["ruff", "server"],
        "extensions": [".py", ".pyi"],
    },
}


class LSPServer:
    def __init__(self, name: str, command: list[str], extensions: list[str]):
        self.name = name
        self.command = command
        self.extensions = extensions
        self._process: asyncio.subprocess.Process | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._stderr_task: asyncio.Task | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._ready = asyncio.Event()
        self._last_diagnostics: list[dict] = []

    async def start(self):
        if self._process:
            return
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._writer = self._process.stdin
            self._reader = self._process.stdout
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            asyncio.create_task(self._read_loop())
            await self._initialize()
        except Exception:
            self._process = None

    async def _drain_stderr(self):
        try:
            while self._process and self._process.stderr and not self._process.stderr.at_eof():
                await self._process.stderr.readline()
        except Exception:
            pass

    async def _send(self, msg: dict):
        if not self._writer:
            return
        content = json.dumps(msg)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        self._writer.write(header.encode() + content.encode())
        await self._writer.drain()

    async def _read_loop(self):
        while self._reader and not self._reader.at_eof():
            try:
                length = None
                while True:
                    header = await asyncio.wait_for(self._reader.readline(), timeout=5)
                    if not header:
                        return
                    header_str = header.decode("utf-8", errors="replace").strip()
                    if header_str.startswith("Content-Length:"):
                        length = int(header_str.split(":")[1].strip())
                    elif not header_str and length is not None:
                        break

                body = await asyncio.wait_for(
                    self._reader.readexactly(length), timeout=10
                )
                msg = json.loads(body.decode("utf-8"))

                method = msg.get("method")
                if method == "textDocument/publishDiagnostics":
                    diags = msg.get("params", {}).get("diagnostics", [])
                    if diags:
                        for d in diags:
                            self._last_diagnostics.append(d)

                msg_id = msg.get("id")
                if msg_id in self._pending:
                    self._pending[msg_id].set_result(msg)
            except (asyncio.TimeoutError, ValueError, json.JSONDecodeError):
                continue
            except Exception:
                break

    async def _initialize(self):
        self._msg_id += 1
        fut = asyncio.get_event_loop().create_future()
        self._pending[self._msg_id] = fut
        await self._send({
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": "initialize",
            "params": {
                "processId": None,
                "capabilities": {},
                "rootUri": None,
            },
        })
        try:
            await asyncio.wait_for(fut, timeout=10)
            self._ready.set()
            await self._send({
                "jsonrpc": "2.0",
                "method": "initialized",
                "params": {},
            })
        except asyncio.TimeoutError:
            pass

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def get_diagnostics(self, file_path: str) -> list[dict]:
        if not self._process:
            return []
        ready = await self.wait_ready()
        if not ready:
            return []

        self._last_diagnostics.clear()
        uri = Path(file_path).resolve().as_uri()

        await self._send({
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": "",
                },
            },
        })

        await asyncio.sleep(0.5)

        result = list(self._last_diagnostics)
        self._last_diagnostics.clear()
        return result

    async def shutdown(self):
        if not self._process:
            return
        try:
            await self._send({
                "jsonrpc": "2.0",
                "method": "shutdown",
                "params": {},
            })
            await self._send({
                "jsonrpc": "2.0",
                "method": "exit",
                "params": {},
            })
        except Exception:
            pass
        try:
            self._process.kill()
        except Exception:
            pass
        self._process = None
        self._ready.clear()

    def can_handle(self, ext: str) -> bool:
        return ext.lower() in self.extensions


class LSPManager:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._servers: dict[str, LSPServer] = {}

    def start_for_file(self, file_path: str) -> LSPServer | None:
        if not self.enabled:
            return None
        ext = Path(file_path).suffix.lower()
        for name, cfg in BUILTIN_LSP.items():
            if ext in cfg["extensions"] and name not in self._servers:
                server = LSPServer(name, cfg["command"], cfg["extensions"])
                self._servers[name] = server
                asyncio.create_task(server.start())
        for server in self._servers.values():
            if server.can_handle(ext):
                return server
        return None

    def get_for_file(self, file_path: str) -> LSPServer | None:
        ext = Path(file_path).suffix.lower()
        for server in self._servers.values():
            if server.can_handle(ext) and server._ready.is_set():
                return server
        return None

    async def shutdown_all(self):
        for server in self._servers.values():
            await server.shutdown()
        self._servers.clear()
