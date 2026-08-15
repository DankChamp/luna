from __future__ import annotations
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.clipboard.pyperclip import PyperclipClipboard
from prompt_toolkit.history import FileHistory
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, Float, FloatContainer
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style as PTKStyle, DynamicStyle
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.filters import Condition, has_selection
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.widgets import TextArea

from ui.completer import LunaCompleter
from ui.theme import Neon
from ui.banner import make_welcome_panel
from ui.provider_panel import show_provider_panel
from core import paths
from core.agent_core import AgentCore
from core.tool_executor import ToolExecutor
from core.skill_engine import SkillEngine
from core.subagent_engine import SubagentEngine
from session.manager import SessionManager
from bridge.client import EmmaBridge
from core.persona import load_luna_persona


console = Console()


@dataclass
class LunaAppConfig:
    """Configuration for LunaApp."""
    persona: Optional[str] = None
    command_loader: Optional[object] = None
    theme_mgr: Optional[object] = None
    ref_mgr: Optional[object] = None
    keybinds: Optional[dict] = None
    session_dir: str = "~/.luna/sessions"
    router: Optional[object] = None
    agent_core: Optional[AgentCore] = None
    subagents: Optional[object] = None
    skills: Optional[object] = None
    memory: Optional[object] = None


class LunaApp:
    """
    Luna's REPL application - UI layout, keybinds, rendering.
    
    Extracted from luna.py to separate UI concerns from agent logic.
    """
    
    def __init__(self, config: LunaAppConfig):
        self.config = config
        self.session_mgr = SessionManager(config.session_dir)
        self.agent_core = config.agent_core
        
        # UI state
        self._output_buffer: list[tuple[str, str]] = []
        self._app: Application | None = None
        self.sidebar_visible = True
        self._sidebar_cache = {"branch": "", "sid": None, "tokens": 0}
        
        # Command handling
        self.command_loader = config.command_loader
        self.subagents = config.subagents
        self.skills = config.skills
        self.memory = config.memory
        self.AT_MENTION_RE = re.compile(r"@(\w[\w-]*)\s+(.*)")
        self._ansi_re = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
        
        # Mode indicators
        from core.modes import AgentMode, MODE_INDICATORS
        self.MODE_INDICATORS = MODE_INDICATORS
        self.AgentMode = AgentMode
        
        # Key bindings
        self._keybinds = config.keybinds or {}
        self._accept_lock = asyncio.Lock()
        
        # Delegation handling
        self._delegation_mode = False
        self._delegation_buffer: list[str] = []
    
    def _prompt_text(self) -> list[tuple[str, str]]:
        """Generate prompt with mode indicator."""
        info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        mc = info["color"]
        return [(f"bold {mc}", f"{info['icon']} "), (mc, "��� ")]
    
    def _header_text(self) -> list[tuple[str, str]]:
        """Generate header text."""
        from session.context import count_messages_tokens, get_context_limit
        
        project = os.path.basename(os.getcwd())
        sid = self.session_mgr.current
        info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        mc = info["color"]
        tokens = count_messages_tokens(self.agent_core.messages)
        limit = get_context_limit(self.agent_core._get_provider_name())
        model_name = self.agent_core.router.active_name or "?"
        
        parts = [
            ("bold " + Neon.primary, " �� Luna "),
            (f"bold {mc}", f"{info['icon']} {info['label']} "),
            (Neon.secondary, f"{model_name} "),
            (Neon.bright, f"{project}"),
        ]
        if sid:
            parts.append((Neon.dim, f" [{sid[:8]}]"))
        pct = f"{tokens}/{limit}" if limit else str(tokens)
        parts.append((Neon.dim, f" {pct}"))
        return parts
    
    def _sep_top_style(self) -> str:
        info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        return f"fg:{info['color']} bg:#0d0d1a"
    
    def _sidebar_text(self) -> str:
        """Generate sidebar text with caching."""
        from ui.sidebar import build_sidebar_text
        from session.context import count_messages_tokens, get_context_limit
        
        project = os.path.basename(os.getcwd())
        sid = self.session_mgr.current
        tokens = count_messages_tokens(self.agent_core.messages)
        limit = get_context_limit(self.agent_core._get_provider_name())
        
        # Get todos
        ts = getattr(self.agent_core, 'todos', None)
        todos = ts.list() if ts else []
        
        # Update branch cache
        if self._sidebar_cache["sid"] != sid or abs(self._sidebar_cache["tokens"] - tokens) > 100:
            self._sidebar_cache["sid"] = sid
            self._sidebar_cache["tokens"] = tokens
            if sid:
                sess = self.session_mgr.load(sid)
                if sess:
                    self._sidebar_cache["branch"] = sess.get("project", {}).get("branch", "") or ""
            else:
                self._sidebar_cache["branch"] = ""
        
        branch = self._sidebar_cache["branch"]
        return build_sidebar_text(project, sid, tokens, limit, todos, branch)
    
    def _sep_vert_style(self) -> str:
        info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        return f"fg:{info['color']} bg:#0d0d1a"
    
    def _build_layout(self):
        """Build the prompt-toolkit layout."""
        from prompt_toolkit.layout import Window, HSplit, VSplit
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.containers import ConditionalContainer
        from prompt_toolkit.filters import Condition
        
        # Header
        header_win = Window(
            content=FormattedTextControl(self._header_text),
            height=1,
            style="bg:#0d0d1a",
        )
        
        sep_top = Window(height=1, char="\u2500", style=self._sep_top_style)
        
        # Sidebar
        sidebar_win = Window(
            content=FormattedTextControl(self._sidebar_text),
            width=26,
            style="bg:#1a1a2e",
            wrap_lines=False,
        )
        
        sep_vert = Window(
            width=1,
            char="\u2502",
            style=self._sep_vert_style,
        )
        
        sidebar_group = ConditionalContainer(
            VSplit([sep_vert, sidebar_win]),
            filter=Condition(lambda: self.sidebar_visible),
        )
        
        # Output area
        out_ctrl = FormattedTextControl(lambda: self._output_buffer)
        out_win = Window(content=out_ctrl, wrap_lines=True)
        
        sep_bot = Window(height=1, char="\u2500", style="fg:#444466 bg:#0d0d1a")
        
        # Input area
        completer = ThreadedCompleter(LunaCompleter(
            subagent_manager=self.subagents,
            commands=self.config.command_loader.list_commands() if self.config.command_loader else [],
            theme_mgr=self.config.theme_mgr,
            ref_mgr=self.config.ref_mgr,
            router=self.config.router,
        ))
        
        input_field = TextArea(
            height=1,
            prompt=self._prompt_text,
            completer=completer,
            complete_while_typing=True,
            multiline=False,
        )
        
        # Build layout
        body = HSplit([
            header_win,
            sep_top,
            VSplit([
                out_win,
                sidebar_group,
            ]),
            sep_bot,
            Window(content=FormattedTextControl(lambda: [("fg:#666", " ")]), height=1),
            input_field,
        ])
        
        # Float for completions menu
        float_container = FloatContainer(
            content=body,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=16, scroll_offset=1),
                ),
            ],
        )
        
        return Layout(float_container)
    
    def _build_keybindings(self) -> KeyBindings:
        """Build key bindings."""
        kb = KeyBindings()
        
        # Basic keys
        @kb.add("c-c")
        def _(event):
            event.app.exit()
        
        @kb.add("c-l")
        def _(event):
            event.app.invalidate()
        
        # Clipboard: Ctrl+Shift+C to copy, Ctrl+Shift+V to paste
        @kb.add("c-c", filter=has_selection)
        def _(event):
            event.app.current_buffer.copy_to_clipboard()
        
        @kb.add("c-v")
        def _(event):
            event.app.current_buffer.paste_from_clipboard()
        
        @kb.add("c-insert")
        def _(event):
            event.app.current_buffer.copy_to_clipboard()
        
        @kb.add("s-insert")
        def _(event):
            event.app.current_buffer.paste_from_clipboard()
        
        # Sidebar toggle
        @kb.add("f1")
        def _(event):
            self.sidebar_visible = not self.sidebar_visible
            event.app.invalidate()
        
        # Provider panel
        @kb.add("f2")
        def _(event):
            if self.config.theme_mgr and self.config.router:
                asyncio.create_task(show_provider_panel(
                    self.config.theme_mgr, self.config.router, self.config.provider_panel_callback
                ))
        
        # Accept handler
        @kb.add("enter")
        def _(event):
            buf = event.app.current_buffer
            text = buf.text.strip()
            if not text:
                return
            
            # Check for slash command or @mention
            if text.startswith("/") or text.startswith("@"):
                event.app.create_background_task(self._accept_impl(text))
                buf.reset()
                return
            
            # Regular message
            event.app.create_background_task(self._accept_impl(text))
            buf.reset()
        
        return kb
    
    def _build_style(self) -> DynamicStyle:
        """Build dynamic style based on mode."""
        from prompt_toolkit.styles import Style as PTKStyle
        
        info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        mc = info["color"]
        
        style_dict = {
            "completion-menu.completion": "bg:#1a1a2e fg:#e0e0e0",
            "completion-menu.completion.current": f"bg:{mc} fg:#0d0d1a",
            "completion-menu.meta.completion": "fg:#666666",
            "completion-menu.meta.completion.current": f"fg:{mc}",
            "scrollbar.background": "bg:#1a1a2e",
            "scrollbar.button": f"bg:{mc}",
            "text-area.focused": f"bg:#1a1a2e",
        }
        
        return DynamicStyle(lambda: PTKStyle.from_dict(style_dict))
    
    async def _accept_impl(self, text: str):
        """Process user input (slash commands, @mentions, regular messages)."""
        async with self._accept_lock:
            try:
                # Handle @mentions (subagents, references)
                at_match = self.AT_MENTION_RE.match(text)
                if at_match:
                    aname = at_match.group(1)
                    rest = at_match.group(2)
                    
                    # Check subagent
                    if self.config.subagents and self.config.subagents.get(aname):
                        await self._run_subagent(aname, rest or "Continue")
                        return
                    
                    # Check reference
                    if self.config.ref_mgr and self.config.ref_mgr.get(aname):
                        content = self.config.ref_mgr.read(aname, rest or "")
                        if content:
                            self._output_buffer.append(("", f"\n{content}\n"))
                            if self._app:
                                self._app.invalidate()
                        return
                
                # Handle slash commands
                if text.startswith("/"):
                    await self._handle_slash_command(text)
                    return
                
                # Regular message - process through agent
                await self._process_message(text)
                
            except Exception as e:
                self._output_buffer.append((f"fg:{Neon.error}", f"\nError: {e}\n"))
                if self._app:
                    self._app.invalidate()
    
    async def _handle_slash_command(self, cmd: str) -> bool:
        """Handle slash commands."""
        parts = cmd[1:].split(" ", 1)
        cmd_name = parts[0]
        cmd_args = parts[1] if len(parts) > 1 else ""
        
        if cmd_name in ("help", "h"):
            await self._show_help()
            return True
        elif cmd_name == "clear":
            self.agent_core.reset()
            self._output_buffer.clear()
            self._output_buffer.append((Neon.dim, "\n[cleared]\n"))
            return True
        elif cmd_name == "exit":
            if self._app:
                self._app.exit()
            return True
        elif cmd_name == "mode":
            await self._handle_mode_command(cmd_args)
            return True
        elif cmd_name == "model":
            await self._handle_model_command(cmd_args)
            return True
        elif cmd_name == "session":
            await self._handle_session_command(cmd_args)
            return True
        elif cmd_name == "skill":
            await self._handle_skill_command(cmd_args)
            return True
        elif cmd_name == "subagent":
            await self._handle_subagent_command(cmd_args)
            return True
        elif cmd_name == "reference":
            await self._handle_reference_command(cmd_args)
            return True
        elif cmd_name == "provider":
            if self.config.theme_mgr and self.config.router:
                await show_provider_panel(self.config.theme_mgr, self.config.router, self.config.provider_panel_callback)
            return True
        elif cmd_name == "todo":
            await self._handle_todo_command(cmd_args)
            return True
        elif cmd_name == "undo":
            undone = self.agent_core.undo_last()
            for u in undone:
                self._output_buffer.append((f"fg:{Neon.warning}", f"\n��� {u}"))
            return True
        elif cmd_name == "redo":
            redone = self.agent_core.redo_last()
            for r in redone:
                self._output_buffer.append((f"fg:{Neon.ok}", f"\n��� {r}"))
            return True
        elif cmd_name == "memory":
            await self._handle_memory(cmd_args)
            return True
        
        # Check custom commands
        if self.config.command_loader and self.config.command_loader.get(cmd_name):
            custom = self.config.command_loader.get(cmd_name)
            expanded = custom.expand(cmd_args)
            if expanded:
                self._output_buffer.append((f"fg:{Neon.dim}", f"\n→ running /{custom.name}...\n"))
                await self._process_message(expanded)
            return True
        
        # Unknown command
        self._output_buffer.append((f"fg:{Neon.error}", f"\nUnknown command: /{cmd_name}\n"))
        return True
    
    async def _process_message(self, line: str) -> str | None:
        """Process a regular message through the agent."""
        # Mark session dirty for debounced save
        self.session_mgr.mark_dirty()
        
        buf = self._output_buffer
        app = self._app
        mode_info = self.MODE_INDICATORS.get(self.agent_core.config.mode, self.MODE_INDICATORS[self.AgentMode.BUILD])
        mc = mode_info["color"]
        
        buf.append((f"bold {mc}", f"◆ {line}\n"))
        if app:
            app.invalidate()
        
        full_text = ""
        in_response = False
        async for event in self.agent_core.run(line):
            if hasattr(event, 'text') and event.text:
                if not in_response:
                    buf.append((f"bold fg:{Neon.secondary}", "\u2503 "))
                    in_response = True
                full_text += event.text
                buf.append(("", event.text))
                if app:
                    app.invalidate()
            
            elif hasattr(event, 'name'):  # ToolExecStart
                args_str = self._fmt_tool_args(event.arguments)
                buf.append((f"fg:{Neon.warning}", f"\n  \u26a1 "))
                buf.append((f"fg:{Neon.secondary}", event.name))
                buf.append((f"fg:{Neon.dim}", f"({args_str})"))
                if app:
                    app.invalidate()
            
            elif isinstance(event, str):
                full_text = event
        
        if full_text:
            buf.append(("", "\n"))
        
        # Flush session save
        self.session_mgr.flush(self.agent_core.messages)
        
        # Auto-suggest skills
        if self.skills:
            self._auto_suggest_skill()
        
        # Extract facts
        if self.memory:
            for fact in self.memory.extract_facts(line):
                self.memory.add_fact(fact, source="auto-extract")
        
        return full_text if full_text else None
    
    def _fmt_tool_args(self, args: dict) -> str:
        """Format tool arguments for display."""
        if not args:
            return ""
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 40:
                s = s[:40] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts)
    
    async def run(self):
        """Run the Luna application."""
        # Initialize UI
        layout = self._build_layout()
        kb = self._build_keybindings()
        style = self._build_style()
        
        self._app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=True,
            clipboard=PyperclipClipboard(),
        )
        
        # Show welcome
        self._output_buffer.append((Neon.primary, "  \u2726 Luna — your coder\n"))
        if self.config.command_loader:
            cmd_list = self.config.command_loader.list_commands()
            if cmd_list:
                names = [c.name for c in cmd_list[:6]]
                self._output_buffer.append((Neon.dim, f"  Commands: /{'  /'.join(names)}\n"))
        self._output_buffer.append(("", "\n"))
        
        # Run
        await self._app.run_async()
    
    async def _show_help(self):
        self._output_buffer.append((Neon.dim, "\nAvailable commands:\n"))
        self._output_buffer.append((Neon.dim, "  /help, /h       - Show this help\n"))
        self._output_buffer.append((Neon.dim, "  /clear          - Clear conversation history\n"))
        self._output_buffer.append((Neon.dim, "  /exit           - Exit Luna\n"))
        self._output_buffer.append((Neon.dim, "  /mode [mode]    - Switch mode (build/plan)\n"))
        self._output_buffer.append((Neon.dim, "  /model [model]  - Switch model\n"))
        self._output_buffer.append((Neon.dim, "  /session [cmd]  - Session management\n"))
        self._output_buffer.append((Neon.dim, "  /skill [cmd]    - Skill management\n"))
        self._output_buffer.append((Neon.dim, "  /subagent [cmd] - Subagent management\n"))
        self._output_buffer.append((Neon.dim, "  /reference [cmd]- Reference management\n"))
        self._output_buffer.append((Neon.dim, "  /provider       - Show provider panel\n"))
        self._output_buffer.append((Neon.dim, "  /todo [cmd]     - Todo management\n"))
        self._output_buffer.append((Neon.dim, "  /undo           - Undo last edit\n"))
        self._output_buffer.append((Neon.dim, "  /redo           - Redo last edit\n"))
        self._output_buffer.append((Neon.dim, "  /memory [cmd]   - Memory management\n"))
        if self.config.command_loader:
            custom = self.config.command_loader.list_commands()
            if custom:
                self._output_buffer.append((Neon.dim, "\nCustom commands:\n"))
                for c in custom:
                    self._output_buffer.append((Neon.dim, f"  /{c.name} - {c.description[:60]}\n"))
        self._output_buffer.append(("", "\n"))
        if self._app:
            self._app.invalidate()
    
    async def _handle_mode_command(self, args: str):
        from core.modes import AgentMode
        if not args:
            self._output_buffer.append((Neon.dim, f"\nCurrent mode: {self.agent_core.config.mode.value}\n"))
            self._output_buffer.append((Neon.dim, "Usage: /mode [build|plan]\n"))
            return
        try:
            mode = AgentMode(args.lower())
            self.agent_core.set_mode(mode)
            self._output_buffer.append((Neon.ok, f"\nMode switched to: {mode.value}\n"))
        except ValueError:
            self._output_buffer.append((Neon.error, f"\nInvalid mode: {args}. Use 'build' or 'plan'\n"))
        if self._app:
            self._app.invalidate()
    
    async def _handle_model_command(self, args: str):
        if not args:
            current = self.agent_core.router.active_model
            self._output_buffer.append((Neon.dim, f"\nCurrent model: {current or 'default'}\n"))
            models = await self.agent_core.router.cached_models()
            if models:
                self._output_buffer.append((Neon.dim, "Available models:\n"))
                for m in models[:20]:
                    self._output_buffer.append((Neon.dim, f"  {m}\n"))
            return
        
        if args == "cycle":
            model = await self.agent_core.router.cycle_model()
            if model:
                self._output_buffer.append((Neon.ok, f"\nSwitched to model: {model}\n"))
            else:
                self._output_buffer.append((Neon.error, "\nNo models available to cycle\n"))
        else:
            try:
                await self.agent_core.router.reconfigure(self.agent_core.router.active_name, model=args)
                self._output_buffer.append((Neon.ok, f"\nModel set to: {args}\n"))
            except Exception as e:
                self._output_buffer.append((Neon.error, f"\nFailed to set model: {e}\n"))
        if self._app:
            self._app.invalidate()
    
    async def _handle_session_command(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""
        
        if subcmd in ("list", "ls"):
            sessions = self.session_mgr.list_sessions()
            if not sessions:
                self._output_buffer.append((Neon.dim, "\nNo sessions found\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nSessions:\n"))
                for s in sessions[:20]:
                    current = " *" if s["id"] == self.session_mgr.current else ""
                    self._output_buffer.append((Neon.dim, f"  {s['id'][:8]} {s['updated'][:19]} {s['message_count']} msgs {s['preview']}{current}\n"))
        
        elif subcmd in ("new", "n"):
            await self.session_mgr.new()
            self.agent_core.reset()
            self._output_buffer.append((Neon.ok, "\nNew session created\n"))
        
        elif subcmd in ("load", "l"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /session load <id>\n"))
                return
            ok = await self.session_mgr.load(subargs)
            if ok:
                self._output_buffer.append((Neon.ok, f"\nLoaded session: {subargs}\n"))
            else:
                self._output_buffer.append((Neon.error, f"\nSession not found: {subargs}\n"))
        
        elif subcmd in ("delete", "rm"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /session delete <id>\n"))
                return
            ok = self.session_mgr.delete(subargs)
            if ok:
                self._output_buffer.append((Neon.ok, f"\nDeleted session: {subargs}\n"))
            else:
                self._output_buffer.append((Neon.error, f"\nFailed to delete: {subargs}\n"))
        
        elif subcmd in ("save", "s"):
            sid = self.session_mgr.save(self.agent_core.messages, debounce=False)
            self._output_buffer.append((Neon.ok, f"\nSession saved: {sid}\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown session command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _handle_skill_command(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""
        
        if not self.skills:
            self._output_buffer.append((Neon.error, "\nSkills not available\n"))
            return
        
        if subcmd in ("list", "ls"):
            skills = self.skills.list_skills()
            if not skills:
                self._output_buffer.append((Neon.dim, "\nNo skills loaded\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nAvailable skills:\n"))
                for s in skills:
                    active = " *" if s in self.skills.get_active() else ""
                    skill_obj = self.skills.get(s)
                    desc = skill_obj.description[:60] if skill_obj else ""
                    self._output_buffer.append((Neon.dim, f"  {s}{active} - {desc}\n"))
        
        elif subcmd in ("activate", "on", "enable"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /skill activate <name>\n"))
                return
            if self.skills.activate(subargs):
                self._output_buffer.append((Neon.ok, f"\nSkill activated: {subargs}\n"))
            else:
                self._output_buffer.append((Neon.error, f"\nSkill not found: {subargs}\n"))
        
        elif subcmd in ("deactivate", "off", "disable"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /skill deactivate <name>\n"))
                return
            self.skills.deactivate(subargs)
            self._output_buffer.append((Neon.ok, f"\nSkill deactivated: {subargs}\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown skill command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _handle_subagent_command(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""
        
        if not self.subagents:
            self._output_buffer.append((Neon.error, "\nSubagents not available\n"))
            return
        
        if subcmd in ("list", "ls"):
            agents = self.subagents.list_subagents()
            if not agents:
                self._output_buffer.append((Neon.dim, "\nNo subagents loaded\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nAvailable subagents:\n"))
                for a in agents:
                    sa = self.subagents.get(a)
                    desc = sa.description[:60] if sa else ""
                    self._output_buffer.append((Neon.dim, f"  @{a} - {desc}\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown subagent command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _handle_reference_command(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""
        
        if not self.config.ref_mgr:
            self._output_buffer.append((Neon.error, "\nReferences not available\n"))
            return
        
        if subcmd in ("list", "ls"):
            refs = self.config.ref_mgr.list()
            if not refs:
                self._output_buffer.append((Neon.dim, "\nNo references loaded\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nAvailable references:\n"))
                for r in refs:
                    self._output_buffer.append((Neon.dim, f"  @{r}\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown reference command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _handle_todo_command(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "list"
        subargs = parts[1] if len(parts) > 1 else ""
        
        todos = getattr(self.agent_core, 'todos', None)
        if not todos:
            self._output_buffer.append((Neon.error, "\nTodos not available\n"))
            return
        
        if subcmd in ("list", "ls", ""):
            items = todos.list()
            if not items:
                self._output_buffer.append((Neon.dim, "\nNo todos\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nTodos:\n"))
                for i, t in enumerate(items):
                    status = "✓" if t.get("done") else "○"
                    self._output_buffer.append((Neon.dim, f"  {i+1}. [{status}] {t['content']}\n"))
        
        elif subcmd in ("add", "a"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /todo add <content>\n"))
                return
            todos.add(subargs)
            self._output_buffer.append((Neon.ok, f"\nTodo added\n"))
        
        elif subcmd in ("done", "d"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /todo done <index>\n"))
                return
            try:
                idx = int(subargs) - 1
                todos.done(idx)
                self._output_buffer.append((Neon.ok, f"\nTodo marked done\n"))
            except (ValueError, IndexError):
                self._output_buffer.append((Neon.error, "\nInvalid index\n"))
        
        elif subcmd in ("remove", "rm"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /todo remove <index>\n"))
                return
            try:
                idx = int(subargs) - 1
                todos.remove(idx)
                self._output_buffer.append((Neon.ok, f"\nTodo removed\n"))
            except (ValueError, IndexError):
                self._output_buffer.append((Neon.error, "\nInvalid index\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown todo command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _handle_memory(self, args: str):
        parts = args.split(" ", 1)
        subcmd = parts[0] if parts else "show"
        subargs = parts[1] if len(parts) > 1 else ""
        
        if not self.memory:
            self._output_buffer.append((Neon.error, "\nMemory not available\n"))
            return
        
        if subcmd in ("show", "s", ""):
            summary = self.memory.summarize()
            if summary:
                self._output_buffer.append((Neon.dim, f"\n{summary}\n"))
            else:
                self._output_buffer.append((Neon.dim, "\nMemory is empty\n"))
        
        elif subcmd in ("add", "a"):
            if not subargs:
                self._output_buffer.append((Neon.error, "\nUsage: /memory add <fact>\n"))
                return
            self.memory.add_fact(subargs, source="manual")
            self._output_buffer.append((Neon.ok, "\nFact added to memory\n"))
        
        elif subcmd in ("clear",):
            self.memory.clear()
            self._output_buffer.append((Neon.ok, "\nMemory cleared\n"))
        
        else:
            self._output_buffer.append((Neon.error, f"\nUnknown memory command: {subcmd}\n"))
        
        if self._app:
            self._app.invalidate()
    
    async def _run_subagent(self, name: str, prompt: str):
        if not self.subagents:
            self._output_buffer.append((Neon.error, "\nSubagents not available\n"))
            return
        
        subagent = self.subagents.get(name)
        if not subagent:
            self._output_buffer.append((Neon.error, f"\nSubagent not found: {name}\n"))
            return
        
        self._output_buffer.append((Neon.dim, f"\nDelegating to @{name}...\n"))
        if self._app:
            self._app.invalidate()
        
        try:
            provider = await self.agent_core.router.get_provider()
            from core.subagent_engine import SubagentEngine
            engine = SubagentEngine(search_dirs=[])
            engine.manager = self.subagents
            result = await engine.run(name, prompt, provider)
            if result.success:
                self._output_buffer.append((Neon.ok, f"\n{result.output}\n"))
            else:
                self._output_buffer.append((Neon.error, f"\nSubagent failed: {result.error}\n"))
        except Exception as e:
            self._output_buffer.append((Neon.error, f"\nSubagent error: {e}\n"))
        
        if self._app:
            self._app.invalidate()
    
    def _auto_suggest_skill(self):
        if not self.skills:
            return
        # This could be enhanced to suggest skills based on recent messages
        pass