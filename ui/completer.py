from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from prompt_toolkit.completion import Completer, Completion

if TYPE_CHECKING:
    from core.subagents import SubagentManager
    from core.themes import ThemeManager
    from core.references import ReferenceManager
    from core.commands import CustomCommand
    from core.router import AIRouter

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show help"),
    ("/clear", "Clear conversation history"),
    ("/skill", "List skills"),
    ("/skill load ", "Load a skill by name"),
    ("/skill unload ", "Unload a skill by name"),
    ("/skill suggest", "Suggest matching skills for context"),
    ("/subagent", "List subagents"),
    ("/subagent run ", "Run a subagent by name"),
    ("/persona", "Show persona status"),
    ("/persona reload", "Hot-reload persona files"),
    ("/model", "Show current model + list variants"),
    ("/model next", "Cycle to the next cached model variant"),
    ("/model use ", "Switch active provider (e.g. local, nvidia)"),
    ("/config", "Show project config"),
    ("/undo", "Undo last file edit"),
    ("/provider", "Open full provider configuration panel"),
    ("/session", "List saved sessions"),
    ("/session new", "Start a new session"),
    ("/emma", "Check Emma connection status"),
    ("/emma sync", "Sync last response to Emma memory"),
    ("/theme", "List / switch themes"),
    ("/commands", "List custom commands"),
    ("/share", "Export or paste session"),
    ("/share --paste", "Share session via paste URL"),
    ("/pr create ", "Create a GitHub PR"),
    ("/pr list", "List GitHub PRs"),
    ("/issue create ", "Create a GitHub Issue"),
    ("/issue list", "List GitHub Issues"),
    ("/improve", "Run self-improvement analysis"),
    ("/memory", "View stored memories"),
    ("/memory clear", "Clear all memories"),
    ("/todo", "List todos"),
    ("/todo add ", "Add a todo"),
    ("/todo done ", "Mark a todo done"),
    ("/todo rm ", "Remove a todo"),
    ("/todo clear", "Clear all todos"),
    ("/sessions", "Alias for /session"),
    ("/exit", "Exit Luna"),
]


class LunaCompleter(Completer):
    def __init__(self, subagent_manager=None, commands=None, theme_mgr=None, ref_mgr=None, router=None):
        self.subagent_manager: SubagentManager | None = subagent_manager
        self.commands: list[CustomCommand] = commands or []
        self.theme_mgr: ThemeManager | None = theme_mgr
        self.ref_mgr: ReferenceManager | None = ref_mgr
        self.router: AIRouter | None = router

    def get_completions(self, document, complete_event):
        try:
            yield from self._get_completions(document, complete_event)
        except Exception:
            return

    def _get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if text.startswith("/model use ") or text.startswith("/models use "):
            prefix = text.split(" ", 2)[-1]
            if self.router:
                for name in self.router.provider_names_sync():
                    if name.startswith(prefix):
                        yield Completion(
                            name,
                            start_position=-len(prefix),
                            display=name,
                            display_meta="provider",
                            style="fg:#00ffff",
                        )
            return

        if text.startswith("/model ") or text.startswith("/models "):
            prefix = text.split(" ", 1)[-1]
            if self.router:
                active = self.router.active_name
                for name in self.router.cached_models_sync():
                    if name.startswith(prefix):
                        marker = " (current)" if name == self.router.active_model else ""
                        yield Completion(
                            name,
                            start_position=-len(prefix),
                            display=name,
                            display_meta=f"variant · {active}{marker}",
                            style="fg:#ff00ff",
                        )

        if text.startswith("/"):
            for cmd, desc in SLASH_COMMANDS:
                if cmd.startswith(text) and cmd != text:
                    display_text = cmd.rstrip()
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=display_text,
                        display_meta=desc,
                        style="fg:#ff00ff",
                    )
            for c in self.commands:
                full = f"/{c.name}"
                if full.startswith(text) and full != text:
                    desc = c.description or "custom command"
                    yield Completion(
                        f"/{c.name} ",
                        start_position=-len(text),
                        display=f"/{c.name}",
                        display_meta=desc,
                        style="fg:#ff00ff",
                    )

        elif text.startswith("@"):
            prefix = text[1:]
            if self.subagent_manager:
                for agent in self.subagent_manager.list_subagents():
                    if agent.hidden:
                        continue
                    if agent.name.startswith(prefix):
                        full = f"@{agent.name} "
                        yield Completion(
                            full,
                            start_position=-len(text),
                            display=f"@{agent.name}",
                            display_meta=agent.description,
                            style="fg:#00ffff",
                        )
            if self.ref_mgr:
                for ref in self.ref_mgr.list_refs():
                    if ref.hidden:
                        continue
                    if ref.alias.startswith(prefix):
                        full = f"@{ref.alias}/"
                        yield Completion(
                            full,
                            start_position=-len(text),
                            display=f"@{ref.alias}",
                            display_meta=ref.description or "reference",
                            style="fg:#ffaa00",
                        )

        elif text and not text.startswith(("/", "@")):
            pass
