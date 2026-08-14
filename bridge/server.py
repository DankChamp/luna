from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import struct
import traceback
from contextlib import asynccontextmanager
from typing import Optional

from aiohttp import web
from aiohttp.web import Request, Response, WebSocketResponse

from core.agent_core import AgentCore
from core.observability import get_tracer, get_logger, trace_span, get_metrics, MetricNames


WS_MAGIC = "258EAFA5-E914-47DA-95CA-5AB5FB11B5D3"

# Endpoints that let a caller execute the agent (bash/tools included) or write
# into memory as a trusted source ("emma"). These require the shared bridge
# token below. Read-only status endpoints stay open since they're harmless.
_PRIVILEGED_PATHS = {"/api/chat", "/api/ingest", "/ws", "/api/delegate"}


def _token_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison so auth can't be brute-forced via timing."""
    if not expected:
        return False
    return hmac.compare_digest(provided or "", expected)


def _ws_accept(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + WS_MAGIC).encode()).digest()
    ).decode()


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Luna</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d0d1a; color: #e0e0e0; font-family: 'JetBrains Mono', 'Fira Code', monospace; display: flex; height: 100vh; }
  #sidebar { width: 240px; background: #1a1a2e; padding: 16px; border-right: 1px solid #2a2a4a; display: flex; flex-direction: column; }
  #sidebar h1 { color: #ff00ff; font-size: 18px; margin-bottom: 16px; }
  #sidebar .info { font-size: 12px; color: #666; margin-bottom: 8px; }
  #sidebar .info span { color: #00ffff; }
  #main { flex: 1; display: flex; flex-direction: column; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; }
  .msg { margin-bottom: 16px; max-width: 80%; }
  .msg.user { margin-left: auto; }
  .msg.user .bubble { background: #2a1a4a; border: 1px solid #ff00ff44; }
  .msg.assistant .bubble { background: #1a2a2e; border: 1px solid #00ffff44; }
  .bubble { padding: 12px 16px; border-radius: 8px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; }
  .label { font-size: 11px; color: #666; margin-bottom: 4px; }
  #input-area { padding: 16px; border-top: 1px solid #2a2a4a; display: flex; gap: 8px; }
  #input { flex: 1; background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 6px; padding: 10px 14px; color: #e0e0e0; font-family: inherit; font-size: 14px; outline: none; }
  #input:focus { border-color: #ff00ff; }
  #send { background: #ff00ff; border: none; border-radius: 6px; padding: 10px 20px; color: #fff; font-family: inherit; font-size: 14px; cursor: pointer; }
  #send:hover { background: #cc00cc; }
  #status { font-size: 12px; color: #00ff41; margin-top: auto; }
</style>
</head>
<body>
<div id="sidebar">
  <h1>✦ Luna</h1>
  <div class="info">Provider: <span id="provider">loading...</span></div>
  <div class="info">Mode: <span id="mode">loading...</span></div>
  <div class="info">Messages: <span id="msg-count">0</span></div>
  <div id="status">● connected</div>
</div>
<div id="main">
  <div id="messages"></div>
  <div id="input-area">
    <input id="input" type="text" placeholder="Type a message..." autofocus>
    <button id="send">Send</button>
  </div>
</div>
<script>
const ws = new WebSocket('ws://' + location.host + '/ws');
const msgs = document.getElementById('messages');
const input = document.getElementById('input');
const send = document.getElementById('send');
ws.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.type === 'chunk') {
    let last = msgs.lastElementChild;
    if (!last || last.dataset.role !== 'assistant') {
      last = addMsg('assistant', '');
    }
    last.querySelector('.bubble').textContent += data.text;
    msgs.scrollTop = msgs.scrollHeight;
  } else if (data.type === 'done') {
    document.getElementById('msg-count').textContent = data.count;
  } else if (data.type === 'status') {
    document.getElementById('provider').textContent = data.provider;
    document.getElementById('mode').textContent = data.mode;
  }
};
function addMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.dataset.role = role;
  div.innerHTML = '<div class="label">' + role + '</div><div class="bubble">' + escapeHtml(text) + '</div>';
  msgs.appendChild(div);
  return div;
}
function escapeHtml(t) { return t.replace(/&/g,'&').replace(/</g,'<').replace(/>/g,'>'); }
function sendMsg() {
  const text = input.value.trim();
  if (!text) return;
  addMsg('user', text);
  input.value = '';
  ws.send(JSON.stringify({type: 'chat', message: text}));
}
send.addEventListener('click', sendMsg);
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendMsg(); });
</script>
</body>
</html>"""


class BridgeServer:
    """Async bridge server using aiohttp for proper async support."""
    
    def __init__(self, agent: 'AgentCore', bridge_token: str = ""):
        self.agent = agent
        self.bridge_token = bridge_token
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._ws_lock = asyncio.Lock()
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        
        # Observability
        self._tracer = get_tracer("luna")
        self._logger = get_logger("luna.bridge")
        self._metrics = get_metrics()

    def _bearer_token(self, request: Request) -> str:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return ""

    def _authorized(self, request: Request) -> bool:
        """Only Emma (holder of the shared bridge token) may call privileged routes."""
        if not self.bridge_token:
            return False
        return _token_matches(self._bearer_token(request), self.bridge_token)

    async def _handle_index(self, request: Request) -> Response:
        return web.Response(text=HTML_PAGE, content_type="text/html")

    async def _handle_health(self, request: Request) -> Response:
        return web.json_response({"status": "ok"})

    async def _handle_status(self, request: Request) -> Response:
        info = {
            "status": "ok",
            "provider": self.agent._get_provider_name(),
            "mode": self.agent.config.mode.value,
            "messages": len(self.agent.messages),
            "tools": len(self.agent.tools.definitions),
        }
        return web.json_response(info)

    async def _handle_api_chat(self, request: Request) -> Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized: missing or invalid bridge token"}, status=401)
        
        message = None
        system = ""
        
        if request.method == "GET":
            message = request.query.get("message")
        else:
            try:
                data = await request.json()
                message = data.get("message")
                system = data.get("system", "")
            except Exception:
                return web.json_response({"error": "invalid JSON body"}, status=400)
        
        if not message:
            return web.json_response({"error": "missing message"}, status=400)
        
        result = await self._run_agent(message, system=system)
        if result:
            return web.json_response({"response": result})
        return web.json_response({"error": "no output"}, status=500)

    async def _handle_history(self, request: Request) -> Response:
        msgs = self.agent.messages
        recent = [{"role": m.get("role"), "content": m.get("content", "")[:300]} for m in msgs[-20:]] if msgs else []
        return web.json_response({"messages": recent, "count": len(msgs)})

    async def _handle_ingest(self, request: Request) -> Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized: missing or invalid bridge token"}, status=401)
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        
        fact = data.get("fact", "")
        tags = data.get("tags", [])
        if not fact:
            return web.json_response({"error": "missing fact"}, status=400)
        
        mem = getattr(self.agent, "memory", None)
        if mem:
            mem.add_fact(fact, source="emma")
        return web.json_response({"ok": True, "fact": fact})

    async def _handle_delegate(self, request: Request) -> Response:
        """Handle delegation requests from Emma with WebSocket streaming support."""
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized: missing or invalid bridge token"}, status=401)
        
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        
        delegation_id = data.get("delegation_id", "")
        task_type = data.get("task_type", "code")
        task = data.get("task", "")
        context = data.get("context", {})
        constraints = data.get("constraints", {})
        
        if not task:
            return web.json_response({"error": "missing task"}, status=400)
        
        # Check if client wants WebSocket streaming
        wants_ws = data.get("stream", False)
        
        # Metrics
        self._metrics.increment(MetricNames.DELEGATION_STARTED, labels={"type": task_type})
        
        with trace_span("luna", "delegate", tags={"delegation_id": delegation_id, "task_type": task_type}) as span:
            span.set_tag("delegation_id", delegation_id)
            span.set_tag("task_type", task_type)
            
            if wants_ws:
                # For streaming, we upgrade to WebSocket and handle there
                self._metrics.increment(MetricNames.DELEGATION_COMPLETED, labels={"type": task_type, "mode": "ws"})
                return web.json_response({
                    "delegation_id": delegation_id,
                    "status": "accepted",
                    "message": "Connect to /ws with same delegation_id for streaming"
                })
            
            # Non-streaming: run agent and return result
            system_context = self._build_delegation_context(context, task_type)
            
            with self._metrics.timer(MetricNames.DELEGATION_DURATION, labels={"type": task_type}):
                result = await self._run_agent(task, system=system_context)
            
            summary = self._extract_summary(result) if result else ""
            status = "completed" if result else "failed"
            self._metrics.increment(MetricNames.DELEGATION_COMPLETED if result else MetricNames.DELEGATION_FAILED, 
                                   labels={"type": task_type})
            
            return web.json_response({
                "delegation_id": delegation_id,
                "status": status,
                "summary": summary,
                "files_changed": [],
                "tests_run": 0,
                "tests_passed": 0,
                "next_steps": [],
                "artifacts": {}
            })

    def _build_delegation_context(self, context: dict, task_type: str) -> str:
        """Build system prompt context for delegated task."""
        parts = []
        
        parts.append(f"[DELEGATED TASK — {task_type.upper()}]")
        parts.append("You are Luna, a coding specialist. Emma has delegated this task to you.")
        parts.append("Work in the existing REPL session context. Use your tools to complete the task.")
        parts.append("")
        
        if context.get("project_path"):
            parts.append(f"Project: {context['project_path']}")
        if context.get("relevant_files"):
            parts.append(f"Relevant files: {', '.join(context['relevant_files'])}")
        if context.get("git_branch"):
            parts.append(f"Git branch: {context['git_branch']}")
        if context.get("recent_changes"):
            parts.append(f"Recent changes: {context['recent_changes']}")
        
        parts.append("")
        parts.append("Constraints:")
        if context.get("max_duration_seconds"):
            parts.append(f"- Max duration: {context['max_duration_seconds']}s")
        if context.get("require_tests"):
            parts.append("- Write/run tests for changes")
        
        return "\n".join(parts)

    def _extract_summary(self, response: str | None) -> str:
        """Extract a brief summary from agent response."""
        if not response:
            return "No response from agent"
        lines = [l.strip() for l in response.split("\n") if l.strip()]
        if not lines:
            return "Task completed"
        for line in lines:
            if len(line) > 20:
                return line[:200]
        return lines[0][:200]

    async def _handle_ws(self, request: Request) -> web.WebSocketResponse:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized: missing or invalid bridge token"}, status=401)
        
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        async with self._ws_lock:
            self._ws_clients.add(ws)
        
        try:
            # Send initial status
            await ws.send_json({
                "type": "status",
                "provider": self.agent._get_provider_name(),
                "mode": self.agent.config.mode.value,
            })
            
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")
                        if msg_type == "chat":
                            await self._stream_agent_ws(ws, data["message"])
                        elif msg_type == "delegate":
                            # Handle delegated task streaming
                            print(f"DEBUG: Received delegate message, calling _stream_delegation_ws")
                            await self._stream_delegation_ws(ws, data)
                            print(f"DEBUG: _stream_delegation_ws returned")
                    except json.JSONDecodeError:
                        print("DEBUG: JSON decode error")
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    print(f"DEBUG: WebSocket error: {ws.exception()}")
                    break
                elif msg.type == web.WSMsgType.CLOSE:
                    print(f"DEBUG: WebSocket close received")
                    break
        except Exception as e:
            print(f"DEBUG: Exception in _handle_ws: {e}")
            traceback.print_exc()
        finally:
            async with self._ws_lock:
                self._ws_clients.discard(ws)
        
        return ws

    async def _stream_delegation_ws(self, ws: web.WebSocketResponse, data: dict):
        """Stream a delegated task with tool events."""
        from core.providers.base import TextChunk, ToolExecStart, ToolExecEnd
        
        delegation_id = data.get("delegation_id", "")
        task_type = data.get("task_type", "code")
        task = data.get("task", "")
        context = data.get("context", {})
        constraints = data.get("constraints", {})
        
        if not task:
            await ws.send_json({"type": "error", "delegation_id": delegation_id, "message": "missing task"})
            return
        
        # Metrics
        self._metrics.increment(MetricNames.DELEGATION_STARTED, labels={"type": task_type, "mode": "ws"})
        
        with trace_span("luna", "delegate_stream", tags={"delegation_id": delegation_id, "task_type": task_type}) as span:
            span.set_tag("delegation_id", delegation_id)
            span.set_tag("task_type", task_type)
            
            # Build system context for delegation
            system_context = self._build_delegation_context(context, task_type)
            
            # Send started event
            await ws.send_json({
                "type": "delegation_started",
                "delegation_id": delegation_id,
                "task_type": task_type
            })
            
            try:
                files_changed = set()
                tests_run = 0
                tests_passed = 0
                
                # Set emma_context for delegation
                self.agent._emma_context = system_context
                
                with self._metrics.timer(MetricNames.DELEGATION_DURATION, labels={"type": task_type, "mode": "ws"}):
                    async for event in self.agent.run(task):
                        if isinstance(event, TextChunk):
                            await ws.send_json({
                                "type": "chunk",
                                "delegation_id": delegation_id,
                                "text": event.text
                            })
                        elif isinstance(event, ToolExecStart):
                            # Track file changes from tool calls
                            tool_name = event.name
                            args = event.arguments
                            await ws.send_json({
                                "type": "tool_start",
                                "delegation_id": delegation_id,
                                "tool": tool_name,
                                "args": args
                            })
                            # Metrics
                            self._metrics.increment(MetricNames.TOOL_EXECUTIONS, labels={"tool": tool_name})
                            
                            with self._metrics.timer(MetricNames.TOOL_DURATION, labels={"tool": tool_name}):
                                pass  # Timing will be captured on ToolExecEnd
                            
                            # Track files from write/edit tools
                            if tool_name in ("write", "edit") and "path" in args:
                                files_changed.add(args["path"])
                        elif isinstance(event, ToolExecEnd):
                            result = event.result
                            await ws.send_json({
                                "type": "tool_end",
                                "delegation_id": delegation_id,
                                "tool": event.name,
                                "result_preview": str(result)[:200] if result else ""
                            })
                            # Track test results
                            if event.name in ("bash", "test") and "test" in str(result).lower():
                                tests_run += 1
                                if "passed" in str(result).lower() or "ok" in str(result).lower():
                                    tests_passed += 1
                        elif isinstance(event, str):
                            # Final text response
                            await ws.send_json({
                                "type": "chunk",
                                "delegation_id": delegation_id,
                                "text": event
                            })
                
                # Send completion
                summary = self._extract_summary(event) if isinstance(event, str) else "Task completed"
                await ws.send_json({
                    "type": "delegation_completed",
                    "delegation_id": delegation_id,
                    "status": "completed",
                    "summary": summary,
                    "files_changed": list(files_changed),
                    "tests_run": tests_run,
                    "tests_passed": tests_passed,
                    "next_steps": [],
                    "artifacts": {}
                })
            except Exception as e:
                self._metrics.increment(MetricNames.DELEGATION_FAILED, labels={"type": task_type, "mode": "ws"})
                span.set_error(e)
                await ws.send_json({
                    "type": "delegation_failed",
                    "delegation_id": delegation_id,
                    "status": "failed",
                    "error": str(e)
                })

    async def _stream_agent_ws(self, ws: web.WebSocketResponse, message: str):
        from core.providers.base import TextChunk
        full = ""
        async for event in self.agent.run(message):
            if isinstance(event, TextChunk):
                full += event.text
                await ws.send_json({"type": "chunk", "text": event.text})
            elif isinstance(event, str):
                full = event
                await ws.send_json({"type": "chunk", "text": event})
        await ws.send_json({"type": "done", "count": len(self.agent.messages) // 2})

    async def _run_agent(self, message: str, system: str = "") -> str | None:
        from core.providers.base import TextChunk
        if system:
            self.agent._emma_context = system
        full = ""
        async for event in self.agent.run(message):
            if isinstance(event, TextChunk):
                full += event.text
            elif isinstance(event, str):
                full = event
        return full if full else None

    async def _broadcast(self, data: dict):
        msg = json.dumps(data)
        async with self._ws_lock:
            clients = list(self._ws_clients)
        for client in clients:
            try:
                await client.send_str(msg)
            except Exception:
                pass

    @asynccontextmanager
    async def _lifespan(self, app: web.Application):
        # Startup
        yield
        # Shutdown
        async with self._ws_lock:
            for ws in self._ws_clients:
                await ws.close()
            self._ws_clients.clear()

    def create_app(self) -> web.Application:
        app = web.Application()
        app.cleanup_ctx.append(self._lifespan)
        
        # Routes
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/api/chat", self._handle_api_chat)
        app.router.add_post("/api/chat", self._handle_api_chat)
        app.router.add_get("/history", self._handle_history)
        app.router.add_post("/api/ingest", self._handle_ingest)
        app.router.add_post("/api/delegate", self._handle_delegate)
        app.router.add_get("/ws", self._handle_ws)
        
        self._app = app
        return app

    async def start(self, host: str = "127.0.0.1", port: int = 8701):
        if host not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"⚠  WARNING: binding to {host} exposes Luna beyond this machine. "
                "Make sure a bridge token is set (EMMA_API_KEY in .env) and that "
                "this port is firewalled from anything but Emma."
            )
        if not self.bridge_token:
            print(
                "⚠  WARNING: no bridge token configured (EMMA_API_KEY is empty). "
                "/api/chat and /api/ingest are DISABLED until you set one — "
                "Luna will not accept commands or 'Emma' context from anyone "
                "without it. Set EMMA_API_KEY in .env on both Luna and Emma to "
                "the same shared secret to enable the bridge."
            )

        app = self.create_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        print(f"Luna server listening on http://{host}:{port}")

    async def stop(self):
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()


async def start_server(agent: 'AgentCore', host: str = "127.0.0.1", port: int = 8701, bridge_token: str = ""):
    """Start the bridge server (async, non-blocking)."""
    server = BridgeServer(agent, bridge_token)
    await server.start(host, port)
    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()