from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from core.agent import Agent


WS_MAGIC = "258EAFA5-E914-47DA-95CA-5AB5FB11B5D3"


def _ws_accept(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + WS_MAGIC).encode()).digest()
    ).decode()


def _encode_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    header = bytearray([0x80 | opcode])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header += bytearray([126]) + struct.pack(">H", len(payload))
    else:
        header += bytearray([127]) + struct.pack(">Q", len(payload))
    return bytes(header) + payload


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
function escapeHtml(t) { return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
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


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    agent: Agent = None
    _ws_clients: list[_Handler] = []
    _ws_lock: threading.Lock = threading.Lock()

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/" or path == "":
            return self._send_html(HTML_PAGE)

        if path == "/health":
            return self._send_json(200, {"status": "ok"})

        if path == "/status":
            info = {
                "status": "ok",
                "provider": _Handler.agent.provider_name,
                "mode": _Handler.agent.mode.value,
                "messages": len(_Handler.agent.messages),
                "tools": len(_Handler.agent.tools.definitions),
            }
            return self._send_json(200, info)

        if path == "/api/chat":
            qs = parse_qs(parsed.query)
            message = qs.get("message", [None])[0]
            if not message:
                cl = int(self.headers.get("Content-Length", 0))
                if cl:
                    body = self.rfile.read(cl)
                    data = json.loads(body)
                    message = data.get("message")
            if not message:
                return self._send_json(400, {"error": "missing message"})
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._run_agent(message))
            finally:
                loop.close()
            if result:
                return self._send_json(200, {"response": result})
            return self._send_json(500, {"error": "no output"})

        if path == "/ws":
            self._handle_ws()
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            cl = int(self.headers.get("Content-Length", 0))
            if not cl:
                return self._send_json(400, {"error": "missing body"})
            body = self.rfile.read(cl)
            data = json.loads(body)
            message = data.get("message", "")
            if not message:
                return self._send_json(400, {"error": "missing message"})
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._run_agent(message))
            finally:
                loop.close()
            if result:
                return self._send_json(200, {"response": result})
            return self._send_json(500, {"error": "no output"})
        self._send_json(404, {"error": "not found"})

    def _read_ws_frame(self) -> tuple[int, bytes] | None:
        header = self.rfile.read(2)
        if len(header) < 2:
            return None
        b0, b1 = header[0], header[1]
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            ext = self.rfile.read(2)
            if len(ext) < 2:
                return None
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = self.rfile.read(8)
            if len(ext) < 8:
                return None
            length = struct.unpack(">Q", ext)[0]
        mask = self.rfile.read(4) if masked else b""
        payload = self.rfile.read(length)
        if len(payload) < length:
            return None
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _handle_ws(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept(key))
        self.end_headers()
        with _Handler._ws_lock:
            _Handler._ws_clients.append(self)
        try:
            while True:
                result = self._read_ws_frame()
                if result is None:
                    break
                opcode, payload = result
                if opcode == 0x8:
                    break
                if opcode == 0x1:
                    data = json.loads(payload.decode("utf-8"))
                    if data.get("type") == "chat":
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(self._stream_agent(data["message"]))
                        loop.close()
                elif opcode == 0x9:
                    self.wfile.write(_encode_ws_frame(payload, 0xA))
                    self.wfile.flush()
        except Exception:
            pass
        with _Handler._ws_lock:
            if self in _Handler._ws_clients:
                _Handler._ws_clients.remove(self)

    def _broadcast(self, data: dict):
        msg = _encode_ws_frame(json.dumps(data).encode())
        with _Handler._ws_lock:
            clients = list(_Handler._ws_clients)
        for client in clients:
            try:
                client.wfile.write(msg)
                client.wfile.flush()
            except Exception:
                pass

    async def _stream_agent(self, message: str):
        from core.providers.base import TextChunk, ToolExecStart, ToolExecEnd
        full = ""
        async for event in _Handler.agent.run(message):
            if isinstance(event, TextChunk):
                full += event.text
                self._broadcast({"type": "chunk", "text": event.text})
            elif isinstance(event, str):
                full = event
                self._broadcast({"type": "chunk", "text": event})
        self._broadcast({"type": "done", "count": len(_Handler.agent.messages) // 2})

    async def _run_agent(self, message: str) -> str | None:
        from core.providers.base import TextChunk, ToolExecStart, ToolExecEnd
        full = ""
        async for event in _Handler.agent.run(message):
            if isinstance(event, TextChunk):
                full += event.text
            elif isinstance(event, str):
                full = event
        return full if full else None


def start_server(agent: Agent, host: str = "127.0.0.1", port: int = 8701):
    _Handler.agent = agent
    server = _ThreadingHTTPServer((host, port), _Handler)
    print(f"Luna server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.server_close()
