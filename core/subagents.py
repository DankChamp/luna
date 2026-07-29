from __future__ import annotations
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.providers.base import TextChunk, ToolCall, ToolCallBatch

if TYPE_CHECKING:
    from core.router import AIRouter


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    meta: dict = {}
    key = ""
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            meta[key] = val
        elif key:
            meta[key] += " " + line.strip()
    return meta, body.strip()


def _parse_list(raw: str) -> list[str]:
    if not raw:
        return []
    if raw.startswith("["):
        raw = raw.strip("[]")
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


@dataclass
class AgentDef:
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    model: str | None = None
    mode: str = "subagent"
    hidden: bool = False
    color: str = "#ff00ff"
    permissions: dict | None = None


class SubagentManager:
    def __init__(self, router: AIRouter, *search_dirs: str | Path):
        self._router = router
        self.search_dirs = [Path(d).expanduser().resolve() for d in search_dirs]
        self._agents: dict[str, AgentDef] = {}
        self._load_all()

    def _load_all(self):
        for d in self.search_dirs:
            if not d.exists():
                continue
            for f in sorted(d.iterdir()):
                name = f.stem
                if f.suffix == ".md" and f.is_file():
                    self._load_markdown(f, name)
                elif f.suffix == ".py" and f.is_file() and name != "__init__":
                    self._load_python(f)

    def _load_markdown(self, path: Path, default_name: str):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", default_name)
        tools_raw = meta.get("tools", "")
        model_val = meta.get("model") or None
        self._agents[name] = AgentDef(
            name=name,
            description=meta.get("description", ""),
            prompt=body,
            tools=_parse_list(tools_raw) if tools_raw else None,
            model=model_val,
            mode=meta.get("mode", "subagent"),
            hidden=meta.get("hidden", "").lower() == "true",
            color=meta.get("color", "#ff00ff"),
            permissions=meta.get("permissions"),
        )

    def _load_python(self, path: Path):
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for attr in dir(mod):
                val = getattr(mod, attr)
                if isinstance(val, AgentDef):
                    self._agents[val.name] = val
        except Exception:
            pass

    def register(self, agent_def: AgentDef) -> None:
        self._agents[agent_def.name] = agent_def

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    def get(self, name: str) -> AgentDef | None:
        return self._agents.get(name)

    def list_subagents(self) -> list[AgentDef]:
        return list(self._agents.values())

    def match(self, query: str) -> list[AgentDef]:
        q = query.lower()
        matched = []
        for agent in self._agents.values():
            if q in agent.name.lower():
                matched.append(agent)
            elif q in agent.description.lower():
                matched.append(agent)
            elif any(w in agent.description.lower() for w in q.split()):
                matched.append(agent)
        return matched

    def task_description(self) -> str:
        available = [a for a in self._agents.values() if not a.hidden]
        if not available:
            return "No subagents available."
        lines = ["Available subagents for task execution:"]
        for a in available:
            lines.append(f"  - {a.name}: {a.description}")
        return "\n".join(lines)

    async def run(self, name: str, prompt: str, model_overrides: dict[str, str] | None = None) -> str:
        agent_def = self.get(name)
        if agent_def is None:
            return f"Error: unknown subagent '{name}'"

        from tools import create_default_registry
        tools = create_default_registry()
        if agent_def.tools is not None:
            blocked = set()
            for t_name, t_def in list(tools._tools.items()):
                if t_name not in agent_def.tools:
                    blocked.add(t_name)
            tools.set_blocked(blocked)

        model = agent_def.model
        if model is None and model_overrides:
            model = model_overrides.get(name)
        provider = await self._router.get_provider()
        if model:
            provider.model = model

        messages: list[dict] = [
            {"role": "system", "content": agent_def.prompt},
            {"role": "user", "content": prompt},
        ]

        full_text = ""
        max_iterations = 15

        for iteration in range(max_iterations):
            collected = ""
            tool_calls_batch = None

            async for event in provider.complete(messages, tools.definitions):
                if isinstance(event, TextChunk):
                    collected += event.text
                elif isinstance(event, ToolCallBatch):
                    tool_calls_batch = event.calls

            if tool_calls_batch is None:
                full_text = collected
                break

            tc_list = []
            for tc in tool_calls_batch:
                tc_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": str(tc.arguments)},
                })
            messages.append({
                "role": "assistant",
                "content": collected,
                "tool_calls": tc_list,
            })

            for tc in tool_calls_batch:
                result = await tools.execute(tc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return full_text if full_text else "(no output)"
