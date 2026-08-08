#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import argparse
import sys
import os
import re
import io

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import FileHistory
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, Float, FloatContainer
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PTKStyle, DynamicStyle
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.widgets import TextArea

from ui.completer import LunaCompleter
from core import paths

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Settings
from core.config_manager import ConfigManager
from core.router import AIRouter
from core.agent import Agent
from core.modes import AgentMode, MODE_INDICATORS
from core.skills import SkillManager
from core.subagents import SubagentManager
from core.mcp import MCPManager, MCPServerConfig
from core.persona import PersonaLoader
from core.project_config import discover as discover_project_cfg, discover_config_path
from core.commands import CommandLoader
from core.references import ReferenceManager
from core.themes import ThemeManager
from tools.formatter import FormatterManager
from tools.custom import register_custom_tools
from core.keybinds import load_keybinds
from core.share import format_session, paste_to_ix
from core.lsp import LSPManager
from core.policies import PolicyEvaluator
from core.memory import MemoryStore
from core.file_watcher import FileWatcher
from tools.memory_tool import create_memory_tools
from tools.git_integration import (
    pr_tool, issue_tool, list_prs_tool, list_issues_tool,
    gh_create_pr, gh_list_prs, gh_create_issue, gh_list_issues,
)
from core.orchestrator import Orchestrator
from tools.orchestrator_tool import create_orchestrator_tool
from core.providers.base import TextChunk, ToolExecStart, ToolExecEnd
from session.manager import SessionManager
from bridge.client import EmmaBridge
from ui.theme import Neon
from ui.banner import make_welcome_panel
from ui.provider_panel import show_provider_panel


console = Console()
settings = Settings()
config_mgr = ConfigManager(settings)
router = AIRouter(config_mgr)
agent = Agent(settings, router)
session_mgr = SessionManager(settings.luna_session_dir)
emma = EmmaBridge(settings.emma_api_url, settings.emma_api_key)

HISTORY_FILE = str(paths.state_home() / "history.txt")
HISTORY_DIR = os.path.dirname(HISTORY_FILE)

AT_MENTION_RE = re.compile(r"@(\w[\w-]*)\s+(.*)")
_ansi_re = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
_output_buffer: list[tuple[str, str]] = []
_app_ref: Application | None = None


class _PTKFile(io.IOBase):
    def __init__(self, buffer_ref, app_ref):
        self.buffer_ref = buffer_ref
        self.app_ref = app_ref

    def write(self, data: str) -> int:
        clean = _ansi_re.sub('', data)
        buf = self.buffer_ref()
        if buf is not None:
            buf.append(("", clean))
        return len(data)

    def flush(self):
        app = self.app_ref()
        if app:
            app.invalidate()

    def isatty(self):
        return False



def _prompt_text():
    info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
    return [
        ("class:luna_prompt_arrow", f"{info['icon']} {info['label']}"),
        ("class:luna_prompt", " ▸ "),
    ]


def _env_warning():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        console.print(f"[{Neon.warning}]⚠ No .env file found[/{Neon.warning}]")
        console.print(f"[{Neon.dim}]  Create from .env.example or run setup.sh[/{Neon.dim}]")


def _project_local_dir(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".luna", name)


def _init_persona():
    search_dirs = paths.search_dirs("persona") + [_project_local_dir("persona")]
    persona = PersonaLoader(*search_dirs)
    prompt = persona.build_system_prompt()
    if prompt:
        agent.set_persona(persona)
        return persona
    return None


def _init_skills():
    skill_dirs = paths.search_dirs("skills") + [_project_local_dir("skills")]
    skill_mgr = SkillManager(*skill_dirs)
    agent.set_skill_manager(skill_mgr)
    return skill_mgr


def _init_subagents():
    search_dirs = paths.search_dirs("subagents") + [_project_local_dir("subagents")]
    sub_mgr = SubagentManager(router, *search_dirs)
    agent.set_subagent_manager(sub_mgr)
    return sub_mgr


async def _start_mcp_servers(mcp: MCPManager):
    if not agent.project_config:
        return
    for name, raw in agent.project_config.mcp_servers.items():
        cfg = MCPServerConfig(
            command=raw.get("command", ""),
            args=raw.get("args", []),
            env=raw.get("env", {}),
        )
        if cfg.command:
            try:
                server = await mcp.add_server(name, cfg)
                mcp.register_tools(agent.tools)
                console.print(f"[{Neon.success}]✓ MCP server '{name}' connected ({len(server.tools)} tools)[/{Neon.success}]")
            except Exception as e:
                console.print(f"[{Neon.error}]✗ MCP server '{name}' failed: {e}[/{Neon.error}]")


def _init_mcp():
    mcp = MCPManager()
    agent.mcp = mcp
    return mcp


def fmt_tool_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if k == "content" and isinstance(v, str) and len(v) > 60:
            parts.append(f"{k}=...({len(v)} chars)")
        elif isinstance(v, str):
            parts.append(f'{k}="{v}"')
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def truncate_result(text: str, max_len: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len] + "…" if len(text) > max_len else text


async def try_load_emma_persona():
    try:
        persona = await emma.get_persona()
        if persona:
            setattr(agent, "_emma_context", persona)
    except Exception as e:
        console.print(f"[{Neon.dim}]Emma persona unavailable: {e}[/{Neon.dim}]")


def print_welcome(persona=None):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    _env_warning()
    pname = router.active_name
    mode_info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
    pname_display = persona.get_persona_name() if persona else "Luna"
    panel = make_welcome_panel(
        provider_name=pname,
        msg_count=len(agent.messages) // 2,
        mode_label=mode_info["label"],
        mode_color=mode_info["color"],
        persona_name=pname_display,
    )
    console.print(panel)
    commands = "/help  /clear  /skill  /subagent  /persona  /model  /provider  /session  /config  /emma  /undo  /theme  /commands  /share  /pr  /issue  /improve  /memory  /exit"
    console.print(f"[{Neon.dim}]Commands: {commands}[/{Neon.dim}]")


def print_help():
    console.print(Panel(
        "\n".join([
            f"[bold {Neon.secondary}]/clear[/bold {Neon.secondary}]       — Clear conversation history",
            f"[bold {Neon.secondary}]/skill[/bold {Neon.secondary}]       — List / load / unload skills",
            f"[bold {Neon.secondary}]/subagent[/bold {Neon.secondary}]    — List / run subagents",
            f"[bold {Neon.secondary}]/persona[/bold {Neon.secondary}]     — Show / reload persona",
            f"[bold {Neon.secondary}]/config[/bold {Neon.secondary}]      — Show project config",
            f"[bold {Neon.secondary}]/model[/bold {Neon.secondary}]        — Show / switch model, variant, or provider (fast)",
            f"[bold {Neon.secondary}]/provider[/bold {Neon.secondary}]    — Configure providers",
            f"[bold {Neon.secondary}]/session[/bold {Neon.secondary}]     — List / switch sessions",
            f"[bold {Neon.secondary}]/emma[/bold {Neon.secondary}]        — Check, message, or sync with Emma",
            f"[bold {Neon.secondary}]/undo[/bold {Neon.secondary}]        — Undo last file edits",
            f"[bold {Neon.secondary}]/theme[/bold {Neon.secondary}]       — List / switch themes",
            f"[bold {Neon.secondary}]/commands[/bold {Neon.secondary}]    — List custom commands",
            f"[bold {Neon.secondary}]/share[/bold {Neon.secondary}]       — Export / paste session",
            f"[bold {Neon.secondary}]/pr[/bold {Neon.secondary}]          — Create / list PRs",
            f"[bold {Neon.secondary}]/issue[/bold {Neon.secondary}]       — Create / list issues",
            f"[bold {Neon.secondary}]/improve[/bold {Neon.secondary}]     — Self-improvement analysis",
            f"[bold {Neon.secondary}]/memory[/bold {Neon.secondary}]      — View / clear memory",
            f"[bold {Neon.secondary}]/exit[/bold {Neon.secondary}]        — Exit Luna",
        ]),
        border_style=Neon.primary,
        title=f"[{Neon.primary}]Help[/{Neon.primary}]",
        title_align="left",
        padding=(0, 1),
    ))
    console.print(f"[{Neon.dim}]  @explore <query> — invoke a subagent directly[/{Neon.dim}]")
    console.print(f"[{Neon.dim}]  luna \"your prompt\" — one-shot mode[/{Neon.dim}]")


async def handle_skill(args: list[str]):
    if not agent.skills:
        console.print(f"[{Neon.warning}]⚠ No skill directory configured[/{Neon.warning}]")
        return

    if not args or args[0] == "list":
        skills = agent.skills.list_skills()
        if not skills:
            console.print(f"[{Neon.dim}]No skills found. Drop .md files in ~/.luna/skills/[/{Neon.dim}]")
            return
        lines = []
        for s in skills:
            loaded = f" [{Neon.success}]✓[/{Neon.success}]" if s.name in agent.active_skills else ""
            lines.append(f"  [{Neon.secondary}]•[/{Neon.secondary}] {s.name} — {s.description}{loaded}")
        console.print(Panel(
            "\n".join(lines),
            border_style=Neon.primary,
            title=f"[{Neon.primary}]Skills[/{Neon.primary}]",
            title_align="left",
            padding=(0, 1),
        ))
        return

    if args[0] == "load":
        if len(args) < 2:
            console.print(f"[{Neon.error}]Usage: /skill load <name>[/{Neon.error}]")
            return
        name = args[1]
        skill = agent.skills.get(name)
        if not skill:
            console.print(f"[{Neon.error}]✗ Skill not found: {name}[/{Neon.error}]")
            return
        if name not in agent.active_skills:
            agent.active_skills.append(name)
        console.print(f"[{Neon.success}]✓ Loaded skill: {name}[/{Neon.success}]")
        console.print(f"[{Neon.dim}]  {skill.description}[/{Neon.dim}]")
        return

    if args[0] == "unload":
        if len(args) < 2:
            console.print(f"[{Neon.error}]Usage: /skill unload <name>[/{Neon.error}]")
            return
        name = args[1]
        if name in agent.active_skills:
            agent.active_skills.remove(name)
            console.print(f"[{Neon.success}]✓ Unloaded skill: {name}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Skill not loaded: {name}[/{Neon.error}]")
        return

    if args[0] == "suggest":
        if not agent.messages:
            console.print(f"[{Neon.dim}]No messages to analyze[/{Neon.dim}]")
            return
        last = agent.messages[-1].get("content", "")
        matches = agent.skills.match(last)
        if matches:
            for s in matches:
                tag = f" [{Neon.success}]✓[/{Neon.success}]" if s.name in agent.active_skills else ""
                console.print(f"[{Neon.secondary}]•[/{Neon.secondary}] {s.name} — {s.description}{tag}")
                console.print(f"[{Neon.dim}]  /skill load {s.name}[/{Neon.dim}]")
        else:
            console.print(f"[{Neon.dim}]No matching skills for your last message[/{Neon.dim}]")
        return


async def handle_subagent(args: list[str]):
    if not agent.subagents:
        console.print(f"[{Neon.warning}]⚠ No subagent manager configured[/{Neon.warning}]")
        return

    if not args or args[0] == "list":
        agents = agent.subagents.list_subagents()
        if not agents:
            console.print(f"[{Neon.dim}]No subagents found[/{Neon.dim}]")
            return
        lines = []
        for a in agents:
            hidden = f" [{Neon.dim}](hidden)[/{Neon.dim}]" if a.hidden else ""
            lines.append(f"  [{Neon.secondary}]•[/{Neon.secondary}] @{a.name} — {a.description}{hidden}")
        console.print(Panel(
            "\n".join(lines),
            border_style=Neon.secondary,
            title=f"[{Neon.secondary}]Subagents[/{Neon.secondary}]",
            title_align="left",
            padding=(0, 1),
        ))
        console.print(f"[{Neon.dim}]Usage: @<name> <prompt> or /subagent run <name> <prompt>[/{Neon.dim}]")
        return

    if args[0] == "run" and len(args) >= 3:
        name = args[1]
        prompt = " ".join(args[2:])
        agent_def = agent.subagents.get(name)
        if not agent_def:
            console.print(f"[{Neon.error}]✗ Unknown subagent: {name}[/{Neon.error}]")
            return
        console.print(f"[{Neon.dim}]→ running @{name}...[/{Neon.dim}]")
        model_overrides = agent.project_config.agent_models if agent.project_config else None
        result = await agent.subagents.run(name, prompt, model_overrides=model_overrides)
        if result:
            console.print(Panel(
                Markdown(result, code_theme="monokai"),
                border_style=agent_def.color,
                title=f"[{agent_def.color}]@{name}[/{agent_def.color}]",
                title_align="left",
                padding=(0, 1),
            ))
        else:
            console.print(f"[{Neon.error}]✗ No output from @{name}[/{Neon.error}]")
        return

    console.print(f"[{Neon.error}]Usage: /subagent list | /subagent run <name> <prompt>[/{Neon.error}]")


async def handle_persona(args: list[str]):
    persona = agent.persona
    if not persona:
        console.print(f"[{Neon.warning}]⚠ No persona loaded[/{Neon.warning}]")
        console.print(f"[{Neon.dim}]Create ~/.luna/persona/core.md to get started[/{Neon.dim}]")
        return

    if not args or args[0] == "status":
        status = persona.status()
        lines = [
            f"[{Neon.secondary}]Name:[/{Neon.secondary}] {persona.get_persona_name()}",
            f"[{Neon.secondary}]Loaded files:[/{Neon.secondary}]",
        ]
        for f in status["loaded"]:
            lines.append(f"  [{Neon.success}]✓[/{Neon.success}] {f}")
        for f in status["missing"]:
            lines.append(f"  [{Neon.dim}]○[/{Neon.dim}] {f} (optional)")
        if status["has_prompt"]:
            prompt = persona.build_system_prompt()
            lines.append("")
            lines.append(f"[{Neon.secondary}]Total prompt size:[/{Neon.secondary}] {len(prompt)} chars")
        console.print(Panel(
            "\n".join(lines),
            border_style=Neon.primary,
            title=f"[{Neon.primary}]Persona[/{Neon.primary}]",
            title_align="left",
            padding=(0, 1),
        ))
        return

    if args[0] == "reload":
        persona.reload()
        prompt = persona.build_system_prompt()
        if prompt:
            console.print(f"[{Neon.success}]✓ Persona reloaded ({len(prompt)} chars)[/{Neon.success}]")
        else:
            console.print(f"[{Neon.warning}]⚠ Persona reloaded but empty[/{Neon.warning}]")
        return


async def handle_config(args: list[str]):
    cfg = agent.project_config
    if not cfg:
        cfg_path = discover_config_path()
        if cfg_path:
            console.print(f"[{Neon.dim}]Project config exists but not loaded. Restart Luna.[/{Neon.dim}]")
            console.print(f"[{Neon.dim}]  Path: {cfg_path}[/{Neon.dim}]")
        else:
            console.print(f"[{Neon.dim}]No project config found (.luna/config.json)[/{Neon.dim}]")
            console.print(f"[{Neon.dim}]Create one in your project root to configure Luna per-project.[/{Neon.dim}]")
        return

    cfg_path = discover_config_path()
    lines = [
        f"[{Neon.secondary}]Config:[/{Neon.secondary}] {cfg_path or '(unknown)'}",
    ]
    if cfg.provider:
        lines.append(f"[{Neon.secondary}]Provider:[/{Neon.secondary}] {cfg.provider}")
    if cfg.model:
        lines.append(f"[{Neon.secondary}]Model:[/{Neon.secondary}] {cfg.model}")
    if cfg.rules:
        lines.append(f"[{Neon.secondary}]Rules:[/{Neon.secondary}] {', '.join(cfg.rules)}")
    if cfg.commands:
        lines.append(f"[{Neon.secondary}]Commands:[/{Neon.secondary}]")
        for name, cmd in cfg.commands.items():
            lines.append(f"  [{Neon.dim}]/run {name}[/{Neon.dim}] → {cmd}")
    if cfg.mcp_servers:
        lines.append(f"[{Neon.secondary}]MCP Servers:[/{Neon.secondary}] {', '.join(cfg.mcp_servers.keys())}")
    console.print(Panel(
        "\n".join(lines),
        border_style=Neon.primary,
        title=f"[{Neon.primary}]Project Config[/{Neon.primary}]",
        title_align="left",
        padding=(0, 1),
    ))


async def handle_session(args: list[str]):
    mem = getattr(agent, "memory", None)

    if not args:
        sessions = session_mgr.list_sessions()
        if not sessions:
            console.print(f"[{Neon.dim}]No saved sessions[/{Neon.dim}]")
            return
        console.print(f"[{Neon.primary}]Sessions:[/{Neon.primary}]")
        for s in sessions[:15]:
            marker = f" [{Neon.primary}]←[/{Neon.primary}]" if s["id"] == session_mgr.current else ""
            preview = (s.get("preview", "") or "")[:50]
            preview_text = f" [{Neon.dim}]— {preview}[/{Neon.dim}]" if preview else ""
            p = s.get("project", {})
            pinfo = ""
            if p.get("name"):
                pinfo = f" [{Neon.secondary}]{p['name']}[/{Neon.secondary}]"
                if p.get("branch"):
                    pinfo += f" [{Neon.dim}]({p['branch']})[/{Neon.dim}]"
            console.print(f"  [{Neon.secondary}]•[/{Neon.secondary}] {s['id'][:19]} [{Neon.dim}]({s['message_count']} msgs)[/{Neon.dim}]{pinfo}{marker}{preview_text}")
        console.print(f"[{Neon.dim}]Usage: /session <id> | /session new | /session delete <id>[/{Neon.dim}]")
        return

    if args[0] == "new":
        if mem and session_mgr.current:
            mem.save_to_file(session_mgr.current)
        session_mgr.new()
        agent.reset()
        if mem:
            mem.clear()
        session_mgr.save(agent.messages)
        console.print(f"[{Neon.success}]✓[/{Neon.success}] New session started")
        return

    if args[0] == "delete" and len(args) >= 2:
        from pathlib import Path
        mpath = Path.home() / ".luna" / "memory" / f"{args[1]}.json"
        if mpath.exists():
            mpath.unlink()
        if session_mgr.delete(args[1]):
            console.print(f"[{Neon.success}]✓ Deleted session {args[1][:19]}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Session not found: {args[1]}[/{Neon.error}]")
        return

    # Switch to session
    if mem and session_mgr.current:
        mem.save_to_file(session_mgr.current)

    data = session_mgr.load(args[0])
    if data is None:
        console.print(f"[{Neon.error}]✗ Session not found: {args[0]}[/{Neon.error}]")
        return

    msgs = data.get("messages", [])
    project = data.get("project", {})

    if mem:
        mem.load_from_file(args[0])

    project_path = project.get("path", "")
    if project_path:
        try:
            os.chdir(project_path)
            cfg = discover_project_cfg()
            if cfg:
                agent.set_project_config(cfg)
            console.print(f"[{Neon.dim}]→ cd {project_path}[/{Neon.dim}]")
        except FileNotFoundError:
            console.print(f"[{Neon.warning}]⚠ Project directory not found: {project_path}[/{Neon.warning}]")
        except Exception as e:
            console.print(f"[{Neon.warning}]⚠ Could not switch to project: {e}[/{Neon.warning}]")

    agent.load_messages(msgs)

    lines = [f"[{Neon.success}]✓ Loaded session [{Neon.secondary}]{args[0][:19]}[/{Neon.secondary}] ({len(msgs)} messages)"]
    if project.get("name"):
        lines.append(f"[{Neon.secondary}]  Project:[/{Neon.secondary}] {project['name']}")
    if project.get("branch"):
        lines.append(f"[{Neon.secondary}]  Branch:[/{Neon.secondary}] {project['branch']}")
    if project.get("repo"):
        lines.append(f"[{Neon.secondary}]  Repo:[/{Neon.secondary}] {project['repo']}")
    if project.get("summary"):
        lines.append(f"[{Neon.dim}]  {project['summary']}[/{Neon.dim}]")
    console.print("\n".join(lines))


async def handle_emma(args: list[str]):
    if args and args[0] == "sync":
        if not agent.messages:
            console.print(f"[{Neon.dim}]Nothing to sync (no messages)[/{Neon.dim}]")
            return
        summary = agent.messages[-1].get("content", "")[:500]
        ok = await emma.save_to_memory("project", summary, tags=["luna-coding"])
        if ok:
            console.print(f"[{Neon.success}]✓ Synced to Emma memory[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Sync failed[/{Neon.error}]")
        return

    connected = await emma.is_connected()
    if not connected:
        console.print(f"[{Neon.warning}]⚠ Emma is not reachable[/{Neon.warning}]")
        console.print(f"[{Neon.dim}]Expected at: {settings.emma_api_url}[/{Neon.dim}]")
        return

    if not args:
        console.print(f"[{Neon.success}]✓ Emma is connected at [{Neon.secondary}]{settings.emma_api_url}[/{Neon.secondary}]")
        return

    msg = " ".join(args)
    console.print(f"[{Neon.dim}]→ sending to Emma...[/{Neon.dim}]")
    resp = await emma.chat(msg)
    if resp:
        console.print(Panel(
            Markdown(resp, code_theme="monokai"),
            border_style=Neon.accent,
            title=f"[{Neon.accent}]Emma[/{Neon.accent}]",
            title_align="left",
            padding=(0, 1),
        ))
    else:
        console.print(f"[{Neon.error}]✗ No response from Emma[/{Neon.error}]")


async def handle_model(args: list[str]):
    """Fast one-liner model/provider/variant switching — the /provider panel
    still exists for deeper config (keys, URLs, testing), but this is the
    quick path: /model, /model <name>, /model use <provider>, /model next."""
    providers = await router.list_providers()
    active = router.active_name

    if not args:
        active_row = next((p for p in providers if p["name"] == active), None)
        console.print(f"[{Neon.secondary}]Provider:[/{Neon.secondary}] {active}   "
                       f"[{Neon.secondary}]Model:[/{Neon.secondary}] {active_row['model'] if active_row else '?'}")
        with console.status(f"[{Neon.dim}]Fetching variants...[/{Neon.dim}]"):
            models = await router.cached_models(active)
        if models:
            lines = []
            for m in models:
                marker = f" [{Neon.success}]←[/{Neon.success}]" if active_row and m == active_row["model"] else ""
                lines.append(f"  [{Neon.secondary}]•[/{Neon.secondary}] {m}{marker}")
            console.print(Panel(
                "\n".join(lines),
                border_style=Neon.primary,
                title=f"[{Neon.primary}]Variants — {active}[/{Neon.primary}]",
                title_align="left",
                padding=(0, 1),
            ))
        other = [p["name"] for p in providers if p["name"] != active]
        if other:
            console.print(f"[{Neon.dim}]Other providers: {', '.join(other)}[/{Neon.dim}]")
        console.print(f"[{Neon.dim}]Usage: /model <variant> | /model use <provider> | /model next | /provider (full panel)[/{Neon.dim}]")
        return

    if args[0] == "next":
        model = await router.cycle_model()
        if model:
            console.print(f"[{Neon.success}]✓ Model switched to {model}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.warning}]⚠ No cached variants to cycle. Run /model first to fetch them.[/{Neon.warning}]")
        return

    if args[0] == "use":
        if len(args) < 2:
            console.print(f"[{Neon.error}]✗ Usage: /model use <provider>[/{Neon.error}]")
            return
        target = args[1]
        names = [p["name"] for p in providers]
        if target in names:
            await router.set_active(target)
            console.print(f"[{Neon.success}]✓ Switched provider to {target}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Unknown provider: {target}. Available: {', '.join(names)}[/{Neon.error}]")
        return

    # bare name: try as a model variant on the active provider first, then as
    # a provider name — matches how people actually type it ("/model 70b",
    # "/model local")
    name = args[0]
    names = [p["name"] for p in providers]
    if name in names:
        await router.set_active(name)
        console.print(f"[{Neon.success}]✓ Switched provider to {name}[/{Neon.success}]")
        return

    await router.reconfigure(active, model=name)
    console.print(f"[{Neon.success}]✓ Model set to {name}[/{Neon.success}]")


async def handle_pr(args: list[str]):
    if not args:
        console.print(f"[{Neon.dim}]Usage: /pr create <title> | /pr list [state][/{Neon.dim}]")
        return
    if args[0] == "create" and len(args) >= 2:
        title = " ".join(args[1:])
        result = await gh_create_pr(title)
        console.print(result)
    elif args[0] == "list":
        state = args[1] if len(args) > 1 else "open"
        result = await gh_list_prs(state)
        console.print(result)
    else:
        console.print(f"[{Neon.error}]Usage: /pr create <title> | /pr list [state][/{Neon.error}]")


async def handle_issue(args: list[str]):
    if not args:
        console.print(f"[{Neon.dim}]Usage: /issue create <title> | /issue list [state][/{Neon.dim}]")
        return
    if args[0] == "create" and len(args) >= 2:
        title = " ".join(args[1:])
        result = await gh_create_issue(title)
        console.print(result)
    elif args[0] == "list":
        state = args[1] if len(args) > 1 else "open"
        result = await gh_list_issues(state)
        console.print(result)
    else:
        console.print(f"[{Neon.error}]Usage: /issue create <title> | /issue list [state][/{Neon.error}]")


async def handle_improve(args: list[str]):
    if not agent.subagents or not agent.subagents.get("improver"):
        console.print(f"[{Neon.warning}]⚠ improver subagent not loaded[/{Neon.warning}]")
        return
    conversation = ""
    for m in agent.messages[-10:]:
        role = m["role"]
        content = (m.get("content", "") or "")[:200]
        if content:
            conversation += f"\n**{role.upper()}**: {content}"
    prompt = f"Review this conversation and suggest improvements:\n{conversation}"
    console.print(f"[{Neon.dim}]→ running @improver...[/{Neon.dim}]")
    result = await agent.subagents.run("improver", prompt)
    if result:
        console.print(Panel(
            Markdown(result, code_theme="monokai"),
            border_style="#00ff88",
            title="[#00ff88]Self-Improvement Suggestions[/#00ff88]",
            title_align="left",
            padding=(0, 1),
        ))


async def handle_memory(args: list[str]):
    mem = getattr(agent, "memory", None)
    if not mem:
        console.print(f"[{Neon.warning}]⚠ Memory not available[/{Neon.warning}]")
        return
    if args and args[0] == "clear":
        mem.clear()
        console.print(f"[{Neon.success}]✓ Memory cleared[/{Neon.success}]")
        return
    summary = mem.summarize()
    if summary:
        console.print(Panel(
            summary,
            border_style=Neon.secondary,
            title=f"[{Neon.secondary}]Memory[/{Neon.secondary}]",
            title_align="left",
            padding=(0, 1),
        ))
    else:
        console.print(f"[{Neon.dim}]No memories stored yet.[/{Neon.dim}]")


async def handle_share(args: list[str]):
    if not agent.messages:
        console.print(f"[{Neon.dim}]Nothing to share (no messages)[/{Neon.dim}]")
        return
    is_paste = any(a in ("--paste", "-p") for a in args)
    title_args = [a for a in args if a not in ("--paste", "-p")]
    title = " ".join(title_args) if title_args else "Luna Session"
    text = format_session(agent.messages, title=title)
    if is_paste:
        url = await paste_to_ix(text)
        if url:
            console.print(f"[{Neon.success}]✓ Pasted to {url}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Paste failed[/{Neon.error}]")
    else:
        console.print(Panel(
            text[:2000],
            border_style=Neon.secondary,
            title=f"[{Neon.secondary}]Session Export[/{Neon.secondary}]",
            title_align="left",
            padding=(0, 1),
        ))
        console.print(f"[{Neon.dim}]Use /share --paste to share via URL[/{Neon.dim}]")


async def handle_undo(args: list[str]):
    restored = agent.undo_last()
    if restored:
        for r in restored:
            console.print(f"[{Neon.success}]✓ Undid edit to {r}[/{Neon.success}]")
    else:
        console.print(f"[{Neon.dim}]Nothing to undo[/{Neon.dim}]")


async def handle_todo(args: list[str]):
    store = getattr(agent, "todos", None)
    if not store:
        console.print(f"[{Neon.warning}]⚠ Todo store not available[/{Neon.warning}]")
        return
    if not args:
        todos = store.list()
        if not todos:
            console.print(f"[{Neon.dim}]No todos[/{Neon.dim}]")
            return
        lines = []
        for t in todos:
            icon = f"[{Neon.success}]✓[/{Neon.success}]" if t["status"] == "done" else f"[{Neon.dim}]○[/{Neon.dim}]"
            lines.append(f"  {icon} {t['content']} [{Neon.dim}]#{t['id']}[/{Neon.dim}]")
        console.print(Panel(
            "\n".join(lines),
            border_style=Neon.primary,
            title=f"[{Neon.primary}]Todos[/{Neon.primary}]",
            title_align="left",
            padding=(0, 1),
        ))
        return
    if args[0] == "add":
        if len(args) < 2:
            console.print(f"[{Neon.error}]Usage: /todo add <content>[/{Neon.error}]")
            return
        item = store.add(" ".join(args[1:]))
        console.print(f"[{Neon.success}]✓ Added todo #{item['id']}[/{Neon.success}]")
    elif args[0] == "done":
        if len(args) < 2:
            console.print(f"[{Neon.error}]Usage: /todo done <id>[/{Neon.error}]")
            return
        if store.done(args[1]):
            console.print(f"[{Neon.success}]✓ Todo #{args[1]} done[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Todo #{args[1]} not found[/{Neon.error}]")
    elif args[0] == "rm":
        if len(args) < 2:
            console.print(f"[{Neon.error}]Usage: /todo rm <id>[/{Neon.error}]")
            return
        if store.remove(args[1]):
            console.print(f"[{Neon.success}]✓ Removed todo #{args[1]}[/{Neon.success}]")
        else:
            console.print(f"[{Neon.error}]✗ Todo #{args[1]} not found[/{Neon.error}]")
    elif args[0] == "clear":
        store.clear()
        console.print(f"[{Neon.success}]✓ Todos cleared[/{Neon.success}]")
    else:
        console.print(f"[{Neon.error}]Unknown subcommand: {args[0]}. Usage: /todo [add|done|rm|clear][/{Neon.error}]")


def auto_suggest_skill():
    if not agent.skills or not agent.messages:
        return
    last = agent.messages[-1].get("content", "")
    if not last:
        return
    matches = agent.skills.match(last)
    for s in matches:
        if s.name not in agent.active_skills:
            console.print(f"[{Neon.dim}]ℹ Skill \"{s.name}\" matches. Use /skill load {s.name}[/{Neon.dim}]")
            break


async def handle_theme(args: list[str], theme_mgr):
    if not theme_mgr:
        console.print(f"[{Neon.warning}]⚠ Theme manager not available[/{Neon.warning}]")
        return
    if not args:
        current = theme_mgr.current
        themes = theme_mgr.list()
        lines = [f"[{Neon.secondary}]Current:[/{Neon.secondary}] [{Neon.primary}]{current}[/{Neon.primary}]"]
        for t in themes:
            marker = f" [{Neon.primary}]←[/{Neon.primary}]" if t == current else ""
            lines.append(f"  [{Neon.secondary}]•[/{Neon.secondary}] {t}{marker}")
        console.print(Panel(
            "\n".join(lines),
            border_style=Neon.primary,
            title=f"[{Neon.primary}]Themes[/{Neon.primary}]",
            title_align="left",
            padding=(0, 1),
        ))
        console.print(f"[{Neon.dim}]Usage: /theme <name> to switch[/{Neon.dim}]")
        return
    name = args[0]
    if theme_mgr.activate(name, Neon):
        console.print(f"[{Neon.success}]✓ Theme switched to [{Neon.primary}]{name}[/{Neon.primary}]")
    else:
        console.print(f"[{Neon.error}]✗ Unknown theme: {name}[/{Neon.error}]")


async def handle_list_commands(command_loader):
    if not command_loader:
        console.print(f"[{Neon.dim}]No custom commands loaded[/{Neon.dim}]")
        return
    cmds = command_loader.list_commands()
    if not cmds:
        console.print(f"[{Neon.dim}]No custom commands found. Create .md files in ~/.luna/commands/[/{Neon.dim}]")
        return
    lines = []
    for c in cmds:
        lines.append(f"  [{Neon.secondary}]•[/{Neon.secondary}] /{c.name} — {c.description}")
    console.print(Panel(
        "\n".join(lines),
        border_style=Neon.secondary,
        title=f"[{Neon.secondary}]Custom Commands[/{Neon.secondary}]",
        title_align="left",
        padding=(0, 1),
    ))


async def handle_slash(command: str, command_loader=None, theme_mgr=None) -> bool:
    parts = command.strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    cmd_args = parts[1:]

    if cmd == "/help":
        print_help()
    elif cmd == "/clear":
        agent.reset()
        console.print(f"[{Neon.success}]✓[/{Neon.success}] History cleared")
    elif cmd == "/skill":
        await handle_skill(cmd_args)
    elif cmd == "/subagent":
        await handle_subagent(cmd_args)
    elif cmd == "/persona":
        await handle_persona(cmd_args)
    elif cmd == "/config":
        await handle_config(cmd_args)
    elif cmd == "/undo":
        await handle_undo(cmd_args)
    elif cmd == "/todo":
        await handle_todo(cmd_args)
    elif cmd == "/provider":
        await show_provider_panel(router, console, PromptSession(history=FileHistory(HISTORY_FILE)))
    elif cmd in ("/model", "/models"):
        await handle_model(cmd_args)
    elif cmd in ("/session", "/sessions"):
        await handle_session(cmd_args)
    elif cmd == "/emma":
        await handle_emma(cmd_args)
    elif cmd == "/theme":
        await handle_theme(cmd_args, theme_mgr)
    elif cmd in ("/commands", "/cmd"):
        await handle_list_commands(command_loader)
    elif cmd == "/share":
        await handle_share(cmd_args)
    elif cmd in ("/pr",):
        await handle_pr(cmd_args)
    elif cmd in ("/issue",):
        await handle_issue(cmd_args)
    elif cmd == "/improve":
        await handle_improve(cmd_args)
    elif cmd == "/memory":
        await handle_memory(cmd_args)
    elif cmd == "/exit":
        sys.exit(0)
    else:
        if command_loader and command_loader.get(cmd[1:]):
            custom = command_loader.get(cmd[1:])
            expanded = custom.expand(cmd_args)
            if expanded:
                console.print(f"[{Neon.dim}]→ running /{custom.name}...[/{Neon.dim}]")
                await process_message(expanded)
            return True
        return False
    return True


async def process_message(line: str) -> str | None:
    session_mgr.save(agent.messages)

    buf = _output_buffer
    app = _app_ref
    mode_info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
    mc = mode_info["color"]

    buf.append((f"bold {mc}", f"◆ {line}\n"))
    if app:
        app.invalidate()

    full_text = ""
    in_response = False
    async for event in agent.run(line):
        if isinstance(event, TextChunk):
            if not in_response:
                buf.append((f"bold fg:{Neon.secondary}", "\u2503 "))
                in_response = True
            full_text += event.text
            buf.append(("", event.text))
            if app:
                app.invalidate()

        elif isinstance(event, ToolExecStart):
            args_str = fmt_tool_args(event.arguments)
            buf.append((f"fg:{Neon.warning}", f"\n  \u26a1 "))
            buf.append((f"fg:{Neon.secondary}", event.name))
            buf.append((f"fg:{Neon.dim}", f"({args_str})"))
            if app:
                app.invalidate()

        elif isinstance(event, ToolExecEnd):
            preview = truncate_result(event.result)
            buf.append((f"fg:{Neon.dim}", f"\n  \u2192 {preview}"))
            if app:
                app.invalidate()

        elif isinstance(event, str):
            full_text = event

    if full_text:
        buf.append(("", "\n"))

    auto_suggest_skill()
    mem = getattr(agent, "memory", None)
    if mem:
        for fact in mem.extract_facts(line):
            mem.add_fact(fact, source="auto-extract")
    return full_text if full_text else None


async def repl(persona=None, command_loader=None, theme_mgr=None, ref_mgr=None, keybinds=None):
    from session.context import count_messages_tokens, get_context_limit

    global _output_buffer, _app_ref

    await try_load_emma_persona()

    asyncio.create_task(router.cached_models())

    if agent.mcp and agent.project_config and agent.project_config.mcp_servers:
        asyncio.create_task(_start_mcp_servers(agent.mcp))

    cmd_list = command_loader.list_commands() if command_loader else []

    output_buf: list[tuple[str, str]] = []
    _output_buffer = output_buf

    output_buf.append((Neon.primary, "  \u2726 Luna — your coder\n"))
    if cmd_list:
        names = [c.name for c in cmd_list[:6]]
        output_buf.append((Neon.dim, f"  Commands: /{'  /'.join(names)}\n"))
    output_buf.append(("", "\n"))

    sidebar_visible = True

    # ── Header ──
    def _header_text():
        project = os.path.basename(os.getcwd())
        sid = session_mgr.current
        info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
        mc = info["color"]
        tokens = count_messages_tokens(agent.messages)
        limit = get_context_limit(router.active_model)
        model_name = router.active_name or "?"
        parts = [
            ("bold " + Neon.primary, " \u2726 Luna "),
            (f"bold {mc}", f"{info['icon']} {info['label']} "),
            (Neon.secondary, f"{model_name} "),
            (Neon.bright, f"{project}"),
        ]
        if sid:
            parts.append((Neon.dim, f" [{sid[:8]}]"))
        pct = f"{tokens}/{limit}" if limit else str(tokens)
        parts.append((Neon.dim, f" {pct}"))
        return parts

    header_win = Window(
        content=FormattedTextControl(_header_text),
        height=1,
        style="bg:#0d0d1a",
    )

    def _sep_top_style():
        info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
        return f"fg:{info['color']} bg:#0d0d1a"

    sep_top = Window(height=1, char="\u2500", style=_sep_top_style)

    # ── Sidebar ──
    def _sidebar_text():
        project = os.path.basename(os.getcwd())
        sid = session_mgr.current
        tokens = count_messages_tokens(agent.messages)
        limit = get_context_limit(router.active_model)
        ts = getattr(agent, "todos", None)
        todos = ts.list() if ts else []
        branch = ""
        if sid:
            sess = session_mgr.load(sid)
            if sess:
                branch = sess.get("project", {}).get("branch", "") or ""
        from ui.sidebar import build_sidebar_text
        return build_sidebar_text(project, sid, tokens, limit, todos, branch)

    sidebar_win = Window(
        content=FormattedTextControl(_sidebar_text),
        width=26,
        style="bg:#1a1a2e",
        wrap_lines=False,
    )

    def _sep_vert_style():
        info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
        return f"fg:{info['color']} bg:#0d0d1a"

    sep_vert = Window(
        width=1,
        char="\u2502",
        style=_sep_vert_style,
    )

    sidebar_group = ConditionalContainer(
        VSplit([sep_vert, sidebar_win]),
        filter=Condition(lambda: sidebar_visible),
    )

    out_ctrl = FormattedTextControl(lambda: _output_buffer)
    out_win = Window(content=out_ctrl, wrap_lines=True)

    sep_bot = Window(height=1, char="\u2500", style="fg:#444466 bg:#0d0d1a")

    completer = ThreadedCompleter(LunaCompleter(
        subagent_manager=agent.subagents, commands=cmd_list,
        theme_mgr=theme_mgr, ref_mgr=ref_mgr, router=router,
    ))

    input_field = TextArea(
        height=1,
        prompt=_prompt_text,
        completer=completer,
        complete_while_typing=True,
        multiline=False,
    )

    _accept_lock = asyncio.Lock()

    def _accept(buf: Buffer) -> bool:
        text = buf.text.strip()
        if not text:
            return True
        get_app().create_background_task(_accept_impl(text))
        return False

    async def _accept_impl(text: str):
        async with _accept_lock:
            try:
                at_match = AT_MENTION_RE.match(text)
                if at_match:
                    aname = at_match.group(1)
                    sub_prompt = at_match.group(2)
                    if agent.subagents and (agent_def := agent.subagents.get(aname)):
                        output_buf.append((f"fg:{Neon.dim}", f"\u2192 @{aname}...\n"))
                        if _app_ref:
                            _app_ref.invalidate()
                        try:
                            mo = agent.project_config.agent_models if agent.project_config else None
                            result = await agent.subagents.run(aname, sub_prompt, model_overrides=mo)
                            if result:
                                output_buf.append(("", f"\n{result}\n"))
                        except Exception as e:
                            output_buf.append((f"fg:{Neon.error}", f"\n\u2717 @{aname} error: {e}\n"))
                        if _app_ref:
                            _app_ref.invalidate()
                        _save_all()
                        return

                if text.strip().lower() in ("/debug",):
                    await run_debug_scan()
                    return

                if text.startswith("/"):
                    handled = await handle_slash(text, command_loader=command_loader, theme_mgr=theme_mgr)
                    if not handled:
                        output_buf.append((f"fg:{Neon.error}", f"Unknown command: {text}\n"))
                        if _app_ref:
                            _app_ref.invalidate()
                    _save_all()
                    return

                await process_message(text)
            except Exception as e:
                output_buf.append((f"fg:{Neon.error}", f"\u2717 Error: {e}\n"))
                if _app_ref:
                    _app_ref.invalidate()
                _save_all()
                return

            _save_all()

    input_field.accept_handler = _accept

    # ── Debug scan ──
    async def run_debug_scan():
        async with _accept_lock:
            await _run_debug_scan_impl()

    async def _run_debug_scan_impl():
        buf = _output_buffer
        app = _app_ref
        buf.append((f"bold {Neon.warning}", "\u25c6 Starting multi-agent debug scan...\n"))
        if app: app.invalidate()

        prompt = (
            "You are in debug mode. Your task:\n"
            "1. Explore the project structure to understand the language, framework, and layout.\n"
            "2. Create specialized bug-scanning sub-agents using the `create_subagent` tool "
            "for each major area of the codebase (e.g. frontend, backend, database, config).\n"
            "3. Run each sub-agent using the `task` tool to scan for bugs, glitches, "
            "and broken things.\n"
            "4. Collect all findings and use the `todowrite` tool to create a consolidated "
            "todo list.\n"
            "5. Fix each bug using the `edit` and `write` tools.\n\n"
            "Show progress at each step."
        )

        async for event in agent.run(prompt):
            if isinstance(event, TextChunk):
                buf.append(("", event.text))
            elif isinstance(event, ToolExecStart):
                buf.append((f"fg:{Neon.warning}", f"\n  \u26a1 "))
                buf.append((f"fg:{Neon.secondary}", event.name))
                args_str = fmt_tool_args(event.arguments)
                buf.append((f"fg:{Neon.dim}", f"({args_str})"))
            elif isinstance(event, ToolExecEnd):
                preview = truncate_result(event.result)
                buf.append((f"fg:{Neon.dim}", f"\n  \u2192 {preview}"))
            elif isinstance(event, str):
                buf.append(("", event))
            if app: app.invalidate()

        _save_all()
        buf.append((f"bold {Neon.success}", f"\n\u25c6 Debug scan complete!\n"))
        if app: app.invalidate()

    # ── Status bar ──
    def _status_text():
        model_name = router.active_name or "?"
        msg_count = len(agent.messages) // 2
        tokens = count_messages_tokens(agent.messages)
        limit = get_context_limit(router.active_model)
        pct = f"t:{tokens}/{limit}" if limit else f"t:{tokens}"
        return [
            (Neon.dim, f" {model_name}"),
            (Neon.dim, f" \u00b7 {msg_count} msgs"),
            (Neon.dim, f" \u00b7 {pct}"),
            ("", " "),
            (Neon.dim, " C-b:bar  C-d:debug  Tab:mode  Esc+m:model  Enter:pick  Alt+C:copy  "),
        ]

    status_win = Window(
        content=FormattedTextControl(_status_text),
        height=1,
        style="bg:#0d0d1a",
    )

    body = VSplit([out_win, sidebar_group])
    root_container = FloatContainer(
        content=HSplit([
            header_win,
            sep_top,
            body,
            sep_bot,
            input_field,
            status_win,
        ]),
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                attach_to_window=input_field.window,
                content=CompletionsMenu(max_height=12, scroll_offset=1),
            ),
        ],
    )
    layout = Layout(root_container, focused_element=input_field.window)

    # ── Key bindings ──
    app_binds = KeyBindings()

    from core.clipboard import PtkClipboard
    ptk_clipboard = PtkClipboard()

    def _copy_text(what: str, text: str):
        clip = ptk_clipboard.backend
        if clip is not None:
            clip.copy(text)
        backend = "OS clipboard" if clip is not None else "memory-only"
        output_buf.append((Neon.dim, f"  [clip] {what}: {len(text)} chars → {backend}\n"))
        if _app_ref:
            _app_ref.invalidate()

    def _output_plain() -> str:
        return "".join(frag for _, frag in _output_buffer)

    def _last_reply() -> str:
        for m in reversed(agent.messages):
            if m.get("role") == "assistant":
                return m.get("content", "") or ""
        return ""

    @app_binds.add('alt+c')
    def _copy_last_reply(event):
        text = _last_reply() or _output_plain()
        _copy_text("last reply", text)

    @app_binds.add('alt+shift+c')
    def _copy_all_output(event):
        _copy_text("output", _output_plain())

    @app_binds.add('alt+w')
    def _copy_selection(event):
        b = event.current_buffer
        if b.selection_state:
            _copy_text("selection", b.copy_selection().text)

    @app_binds.add('enter')
    @app_binds.add('c-m')
    @app_binds.add('c-j')
    def _enter_or_complete(event):
        b = event.current_buffer
        if b.complete_state is not None:
            st = b.complete_state
            if st.current_completion is None and st.completions:
                st.go_to_index(0)
            if st.current_completion is not None:
                b.apply_completion(b.complete_state.current_completion)
                return
            b.complete_state = None
        b.validate_and_handle()

    @app_binds.add('c-b')
    def _toggle_sidebar(event):
        nonlocal sidebar_visible
        sidebar_visible = not sidebar_visible
        event.app.invalidate()

    @app_binds.add('c-d')
    def _debug_scan(event):
        event.app.create_background_task(run_debug_scan())

    final_binds = merge_key_bindings([app_binds, keybinds]) if keybinds is not None else app_binds

    def _save_all():
        session_mgr.save(agent.messages)
        mem = getattr(agent, "memory", None)
        if mem and session_mgr.current:
            mem.save_to_file(session_mgr.current)

    _ptk_file = _PTKFile(lambda: _output_buffer, lambda: _app_ref)
    _orig_file = console.file
    console.file = _ptk_file

    def _app_style() -> PTKStyle:
        info = MODE_INDICATORS.get(agent.mode, MODE_INDICATORS[AgentMode.BUILD])
        mc = info["color"]
        return PTKStyle([
            ("text-area", "bg:#0d0d1a fg:#e0e0e0"),
            ("luna_prompt_arrow", f"bold {info['color']}"),
            ("luna_prompt", f"bold {Neon.primary}"),
            ("completion-menu", "bg:#1a1a2e"),
            ("completion-menu.completion", f"bg:#222244 {Neon.bright}"),
            ("completion-menu.completion.current", f"bg:{mc} #000000"),
            ("completion-menu.meta", f"bg:#1a1a2e {Neon.dim}"),
            ("completion-menu.meta.current", f"bg:{mc} #000000"),
            ("scrollbar", "bg:#222244"),
            ("scrollbar.button", f"bg:{mc}"),
        ])

    app = Application(
        layout=layout,
        key_bindings=final_binds,
        full_screen=True,
        mouse_support=False,
        clipboard=ptk_clipboard,
        style=DynamicStyle(_app_style),
    )

    _app_ref = app

    try:
        await app.run_async()
    finally:
        _save_all()
        _app_ref = None
        _output_buffer = []
        console.file = _orig_file


async def one_shot(prompt: str):
    full_text = ""
    async for event in agent.run(prompt):
        if isinstance(event, TextChunk):
            full_text += event.text
        elif isinstance(event, ToolExecStart):
            args_str = fmt_tool_args(event.arguments)
            console.print(f"  [{Neon.warning}]⚡[/{Neon.warning}] [{Neon.secondary}]{event.name}[/{Neon.secondary}]({args_str})")
        elif isinstance(event, str):
            full_text = event

    if full_text:
        console.print(Panel(
            Markdown(full_text, code_theme="monokai"),
            border_style=Neon.primary,
            title=f"[{Neon.primary}]Luna[/{Neon.primary}]",
            title_align="left",
            padding=(0, 1),
        ))


async def _async_main():
    persona = _init_persona()
    _init_skills()

    cfg = discover_project_cfg()
    if cfg:
        agent.set_project_config(cfg)

    _init_subagents()

    _init_mcp()

    # Phase 5: Custom commands
    cmd_dirs = paths.search_dirs("commands") + [_project_local_dir("commands")]
    command_loader = CommandLoader(*cmd_dirs)

    # Phase 5: Custom tools
    tool_dirs = paths.search_dirs("tools") + [_project_local_dir("tools")]
    register_custom_tools(agent.tools, *tool_dirs)

    # Phase 5: References
    ref_mgr = ReferenceManager()
    ref_mgr.discover()
    agent.references = ref_mgr
    from tools.reference_tool import create_reference_tool
    ref_tool = create_reference_tool(ref_mgr)
    agent.tools.register(ref_tool)

    # Phase 5: Formatters
    formatter_mgr = FormatterManager(enabled=True)
    agent.tools.formatter = formatter_mgr.format

    # Phase 5: Themes
    theme_mgr = ThemeManager()
    theme_mgr.activate("neon", Neon)

    # Tier 2: Keybinds
    keybinds = load_keybinds()

    @keybinds.add('tab')
    def _toggle_mode(event):
        new = AgentMode.PLAN if agent.mode == AgentMode.BUILD else AgentMode.BUILD
        agent.set_mode(new)
        event.app.invalidate()

    @keybinds.add('escape', 'm')
    async def _cycle_model(event):
        model = await router.cycle_model()
        if model:
            console.print(f"[{Neon.dim}]→ model: {model}[/{Neon.dim}]")
            event.app.renderer.clear()
            event.app.invalidate()

    # Tier 2: LSP
    lsp_mgr = LSPManager(enabled=True)
    agent.lsp = lsp_mgr

    # Tier 3: Policies
    policy_eval = PolicyEvaluator()
    if cfg and cfg.policies:
        policy_eval.load(cfg.policies)

    # Tier 3: Memory
    memory = MemoryStore()
    agent.memory = memory

    # Load most recent session's memory on startup
    recent = session_mgr.list_sessions()
    if recent:
        latest = recent[0]["id"]
        session_mgr._current = latest
        memory.load_from_file(latest)

    for mt in create_memory_tools(memory):
        agent.tools.register(mt)

    # Tier 3: Git integration tools
    agent.tools.register(pr_tool)
    agent.tools.register(issue_tool)
    agent.tools.register(list_prs_tool)
    agent.tools.register(list_issues_tool)

    # Leapfrog: Todo store
    from core.todos import TodoStore
    from tools.todo_tool import create_todo_tools
    todo_store = TodoStore()
    agent.todos = todo_store
    for tt in create_todo_tools(todo_store):
        agent.tools.register(tt)

    # Question tool
    from tools.question_tool import create_question_tool
    agent.tools.register(create_question_tool(lambda: _app_ref))

    # Sub-agent creation tool
    from tools.subagent_tool import create_subagent_tool
    if agent.subagents:
        agent.tools.register(create_subagent_tool(agent.subagents))

    # Leapfrog: File watcher
    async def _on_file_change(changes: list[str]):
        for p in changes:
            console.print(f"[{Neon.dim}]ℹ File changed: {p}[/{Neon.dim}]")

    watcher = FileWatcher(callback=_on_file_change)
    agent.watcher = watcher
    if cfg:
        root_dir = discover_config_path()
        asyncio.create_task(watcher.start(str(root_dir.parent) if root_dir else "."))

    # Leapfrog: Multi-agent orchestrator
    if agent.subagents:
        orch = Orchestrator(agent.subagents)
        agent.orch = orch
        agent.tools.register(create_orchestrator_tool(orch))

    async def lsp_callback(file_path: str) -> list[dict]:
        server = lsp_mgr.start_for_file(file_path)
        if server:
            return await server.get_diagnostics(file_path)
        return []

    agent.tools.lsp_diagnostics = lsp_callback

    parser = argparse.ArgumentParser(description="Luna — your coder")
    parser.add_argument("prompt", nargs="?", help="One-shot prompt")
    parser.add_argument("--model", "-m", help="Force a specific provider")
    parser.add_argument("--serve", action="store_true", help="Run as server (for Emma)")
    parser.add_argument("--port", type=int, default=8701, help="Server port (default: 8701)")
    args = parser.parse_args()

    if args.model:
        await agent.set_provider(args.model)

    if args.serve:
        from bridge.server import start_server
        start_server(agent, port=args.port, bridge_token=settings.emma_api_key)
        return
    elif args.prompt:
        await one_shot(args.prompt)
    else:
        try:
            await repl(persona, command_loader=command_loader, theme_mgr=theme_mgr, ref_mgr=ref_mgr, keybinds=keybinds)
        except KeyboardInterrupt:
            console.print(f"\n[{Neon.dim}]goodbye[/{Neon.dim}]")


def main():
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        console.print(f"\n[{Neon.dim}]goodbye[/{Neon.dim}]")


if __name__ == "__main__":
    main()
