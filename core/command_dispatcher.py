from __future__ import annotations
import asyncio
import shlex
from typing import Optional, Callable, Awaitable, Any
from dataclasses import dataclass

from core.commands import CommandLoader, CustomCommand
from core.subagents import SubagentManager
from core.references import ReferenceManager
from core.skills import SkillManager
from core.modes import AgentMode


@dataclass
class CommandContext:
    """Context passed to command handlers."""
    agent: Any
    session_controller: Any
    subagents: SubagentManager | None
    ref_mgr: ReferenceManager | None
    skills: SkillManager | None
    theme_mgr: Any
    router: Any
    output_buffer: list


class CommandDispatcher:
    """
    Handles slash commands, @mentions, and custom commands.
    
    Separated from LunaApp for cleaner architecture.
    """
    
    def __init__(
        self,
        command_loader: CommandLoader | None,
        subagents: SubagentManager | None,
        ref_mgr: ReferenceManager | None,
        skills: SkillManager | None,
        theme_mgr: Any,
        router: Any,
        agent: Any,
        session_controller: Any,
        output_buffer: list,
    ):
        self.command_loader = command_loader
        self.subagents = subagents
        self.ref_mgr = ref_mgr
        self.skills = skills
        self.theme_mgr = theme_mgr
        self.router = router
        self.agent = agent
        self.session_controller = session_controller
        self.output_buffer = output_buffer
        
        # Command registry
        self._builtin_commands = {
            "help": self._cmd_help,
            "h": self._cmd_help,
            "clear": self._cmd_clear,
            "exit": self._cmd_exit,
            "mode": self._cmd_mode,
            "model": self._cmd_model,
            "session": self._cmd_session,
            "skill": self._cmd_skill,
            "subagent": self._cmd_subagent,
            "reference": self._cmd_reference,
            "provider": self._cmd_provider,
            "todo": self._cmd_todo,
            "undo": self._cmd_undo,
            "redo": self._cmd_redo,
            "memory": self._cmd_memory,
        }
    
    async def dispatch(self, text: str) -> bool:
        """
        Dispatch a command or @mention.
        
        Returns True if handled, False if not a command/mention.
        """
        text = text.strip()
        if not text:
            return True
        
        # @mentions (subagents, references)
        if text.startswith("@"):
            return await self._handle_mention(text)
        
        # Slash commands
        if text.startswith("/"):
            return await self._handle_slash(text)
        
        return False
    
    async def _handle_mention(self, text: str) -> bool:
        """Handle @subagent or @reference mentions."""
        import re
        match = re.match(r"@(\w[\w-]*)\s*(.*)", text)
        if not match:
            return False
        
        name = match.group(1)
        rest = match.group(2).strip()
        
        # Subagent mention
        if self.subagents and self.subagents.get(name):
            await self._run_subagent(name, rest or "Continue")
            return True
        
        # Reference mention
        if self.ref_mgr and self.ref_mgr.get(name):
            content = self.ref_mgr.read(name, rest or "")
            if content:
                self.output_buffer.append(("", f"\n{content}\n"))
            return True
        
        self.output_buffer.append((f"fg:#f43f5e", f"\nUnknown mention: @{name}\n"))
        return True
    
    async def _handle_slash(self, text: str) -> bool:
        """Handle /command [args]."""
        parts = text[1:].split(" ", 1)
        cmd_name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        
        # Built-in commands
        if cmd_name in self._builtin_commands:
            await self._builtin_commands[cmd_name](cmd_args)
            return True
        
        # Custom commands
        if self.command_loader:
            custom = self.command_loader.get(cmd_name)
            if custom:
                expanded = custom.expand(cmd_args)
                if expanded:
                    self.output_buffer.append((f"fg:#6b7280", f"\n→ running /{custom.name}...\n"))
                    # Recursively dispatch the expanded command
                    await self.dispatch(expanded)
                return True
        
        self.output_buffer.append((f"fg:#f43f5e", f"\nUnknown command: /{cmd_name}\n"))
        return True
    
    async def _run_subagent(self, name: str, prompt: str):
        """Run a subagent."""
        if not self.subagents:
            self.output_buffer.append((f"fg:#f43f5e", f"\nNo subagents available\n"))
            return
        
        subagent = self.subagents.get(name)
        if not subagent:
            self.output_buffer.append((f"fg:#f43f5e", f"\nSubagent '{name}' not found\n"))
            return
        
        self.output_buffer.append((f"fg:#8b5cf6", f"\n���� Running subagent @{name}...\n"))
        
        try:
            from core.providers.base import AIProvider
            provider = await self.router.get_provider()
            
            system_prompt = subagent.build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            
            tool_defs = subagent.get_tools()
            
            full = ""
            async for event in provider.complete(messages, tool_defs):
                if hasattr(event, 'text'):
                    full += event.text
                    self.output_buffer.append(("", event.text))
                elif isinstance(event, str):
                    full = event
            
            self.output_buffer.append(("", "\n"))
            self.output_buffer.append((f"fg:#8b5cf6", f"Subagent @{name} completed.\n"))
            
        except Exception as e:
            self.output_buffer.append((f"fg:#f43f5e", f"\nSubagent error: {e}\n"))
    
    # Built-in command handlers
    
    async def _cmd_help(self, args: str):
        self.output_buffer.append((f"fg:#6b7280", "\nBuilt-in commands:\n"))
        for name in sorted(self._builtin_commands.keys()):
            self.output_buffer.append((f"fg:#8b5cf6", f"  /{name}\n"))
        
        if self.command_loader:
            self.output_buffer.append((f"fg:#6b7280", "\nCustom commands:\n"))
            for cmd in self.command_loader.list_commands():
                self.output_buffer.append((f"fg:#8b5cf6", f"  /{cmd.name} — {cmd.description}\n"))
    
    async def _cmd_clear(self, args: str):
        self.agent.reset()
        self.output_buffer.clear()
        self.output_buffer.append((f"fg:#6b7280", "\n[cleared]\n"))
    
    async def _cmd_exit(self, args: str):
        import sys
        self.output_buffer.append((f"fg:#6b7280", "\nGoodbye!\n"))
        await asyncio.sleep(0.1)
        sys.exit(0)
    
    async def _cmd_mode(self, args: str):
        from core.modes import AgentMode, MODE_INDICATORS
        
        args = args.strip().lower()
        if not args:
            current = self.agent.mode.value
            self.output_buffer.append((f"fg:#6b7280", f"\nCurrent mode: {current}\n"))
            self.output_buffer.append((f"fg:#6b7280", f"Available: build, plan\n"))
            return
        
        if args in ("build", "b"):
            self.agent.set_mode(AgentMode.BUILD)
            self.output_buffer.append((f"fg:#22c55e", "\nMode: BUILD\n"))
        elif args in ("plan", "p"):
            self.agent.set_mode(AgentMode.PLAN)
            self.output_buffer.append((f"fg:#3b82f6", "\nMode: PLAN\n"))
        else:
            self.output_buffer.append((f"fg:#f43f5e", f"\nUnknown mode: {args}. Use 'build' or 'plan'.\n"))
    
    async def _cmd_model(self, args: str):
        args = args.strip()
        if not args:
            self.output_buffer.append((f"fg:#6b7280", f"\nCurrent model: {self.agent.provider_name}\n"))
            return
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "list":
            models = await self.router.cached_models()
            for provider, provider_models in models.items():
                self.output_buffer.append((f"fg:#8b5cf6", f"\n{provider}:\n"))
                for m in provider_models[:10]:
                    self.output_buffer.append((f"fg:#6b7280", f"  {m}\n"))
                if len(provider_models) > 10:
                    self.output_buffer.append((f"fg:#6b7280", f"  ... and {len(provider_models) - 10} more\n"))
        
        elif subcmd == "next":
            await self.agent.router.cycle_model()
            self.output_buffer.append((f"fg:#22c55e", f"\nModel: {self.agent.router.active_name}\n"))
        
        elif subcmd == "use" and len(parts) > 1:
            model = parts[1]
            await self.agent.set_provider(model=model)
            self.output_buffer.append((f"fg:#22c55e", f"\nModel: {self.agent.router.active_name}\n"))
        
        else:
            self.output_buffer.append((f"fg:#f43f5e", "\nUsage: /model [list|next|use <model>]\n"))
    
    async def _cmd_session(self, args: str):
        args = args.strip()
        if not args:
            sessions = self.session_controller.list_sessions()
            if sessions:
                self.output_buffer.append((f"fg:#6b7280", "\nSessions:\n"))
                for s in sessions[:10]:
                    current = " →" if s["id"] == self.session_controller.current_session_id else ""
                    self.output_buffer.append((f"fg:#6b7280", f"  {s['id'][:8]}  {s['message_count']} msgs  {s['preview']}{current}\n"))
            else:
                self.output_buffer.append((f"fg:#6b7280", "\nNo sessions\n"))
            return
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "new":
            await self.session_controller.new_session()
            self.output_buffer.append((f"fg:#22c55e", "\nNew session started\n"))
        
        elif subcmd in ("load", "switch") and len(parts) > 1:
            session_id = parts[1]
            if await self.session_controller.load_session(session_id):
                self.output_buffer.append((f"fg:#22c55e", f"\nLoaded session {session_id[:8]}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nSession not found: {session_id}\n"))
        
        elif subcmd == "delete" and len(parts) > 1:
            session_id = parts[1]
            if self.session_controller.delete(session_id):
                self.output_buffer.append((f"fg:#22c55e", f"\nDeleted session {session_id[:8]}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nSession not found: {session_id}\n"))
        
        elif subcmd == "save":
            await self.session_controller.flush()
            self.output_buffer.append((f"fg:#22c55e", "\nSession saved\n"))
        
        else:
            self.output_buffer.append((f"fg:#f43f5e", "\nUsage: /session [new|load|switch|delete|save] [id]\n"))
    
    async def _cmd_skill(self, args: str):
        if not self.skills:
            self.output_buffer.append((f"fg:#f43f5e", "\nSkill engine not available\n"))
            return
        
        args = args.strip()
        if not args:
            available = self.skills.load_skills()
            active = self.skills.get_active()
            self.output_buffer.append((f"fg:#6b7280", "\nAvailable skills:\n"))
            for s in available:
                marker = " ●" if s in active else ""
                self.output_buffer.append((f"fg:#8b5cf6", f"  {s}{marker}\n"))
            return
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "load" and len(parts) > 1:
            skill_name = parts[1]
            if self.skills.activate(skill_name):
                self.output_buffer.append((f"fg:#22c55e", f"\nLoaded skill: {skill_name}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nSkill not found: {skill_name}\n"))
        
        elif subcmd == "unload" and len(parts) > 1:
            skill_name = parts[1]
            if self.skills.deactivate(skill_name):
                self.output_buffer.append((f"fg:#22c55e", f"\nUnloaded skill: {skill_name}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nSkill not active: {skill_name}\n"))
        
        elif subcmd == "list":
            available = self.skills.load_skills()
            for s in available:
                self.output_buffer.append((f"fg:#8b5cf6", f"  {s}\n"))
        
        else:
            self.output_buffer.append((f"fg:#f43f5e", "\nUsage: /skill [load|unload|list] [name]\n"))
    
    async def _cmd_subagent(self, args: str):
        if not self.subagents:
            self.output_buffer.append((f"fg:#f43f5e", "\nSubagents not available\n"))
            return
        
        args = args.strip()
        if not args:
            subagents = self.subagents.list_subagents()
            self.output_buffer.append((f"fg:#6b7280", "\nAvailable subagents:\n"))
            for s in subagents:
                self.output_buffer.append((f"fg:#8b5cf6", f"  @{s}\n"))
            return
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "run" and len(parts) > 1:
            name = parts[1]
            prompt = " ".join(parts[2:]) if len(parts) > 2 else "Continue"
            await self._run_subagent(name, prompt)
        
        elif subcmd == "list":
            for s in self.subagents.list_subagents():
                self.output_buffer.append((f"fg:#8b5cf6", f"  @{s}\n"))
        
        else:
            self.output_buffer.append((f"fg:#f43f5e", "\nUsage: /subagent [run|list] [name] [prompt]\n"))
    
    async def _cmd_reference(self, args: str):
        if not self.ref_mgr:
            self.output_buffer.append((f"fg:#f43f5e", "\nReferences not available\n"))
            return
        
        args = args.strip()
        if not args:
            refs = self.ref_mgr.list()
            self.output_buffer.append((f"fg:#6b7280", "\nReferences:\n"))
            for r in refs:
                self.output_buffer.append((f"fg:#8b5cf6", f"  @{r}\n"))
            return
        
        parts = args.split()
        subcmd = parts[0].lower()
        
        if subcmd == "add" and len(parts) >= 3:
            name = parts[1]
            path = " ".join(parts[2:])
            if self.ref_mgr.add(name, path):
                self.output_buffer.append((f"fg:#22c55e", f"\nAdded reference @{name} → {path}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nFailed to add reference\n"))
        
        elif subcmd == "remove" and len(parts) > 1:
            name = parts[1]
            if self.ref_mgr.remove(name):
                self.output_buffer.append((f"fg:#22c55e", f"\nRemoved reference @{name}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nReference not found\n"))
        
        elif subcmd == "read" and len(parts) >= 2:
            name = parts[1]
            query = " ".join(parts[2:]) if len(parts) > 2 else ""
            content = self.ref_mgr.read(name, query)
            if content:
                self.output_buffer.append((f"", f"\n{content}\n"))
            else:
                self.output_buffer.append((f"fg:#f43f5e", f"\nReference not found or empty\n"))
        
        else:
            self.output_buffer.append((f"fg:#f43f5e", "\nUsage: /reference [add|remove|read|list] [name] [path/query]\n"))
    
    async def _cmd_provider(self, args: str):
        # This is handled by the GUI's provider panel
        self.output_buffer.append((f"fg:#6b7280", "\nUse /model for model selection, or press F2 for provider panel\n"))
    
    async def _cmd_todo(self, args: str):
        # Todo commands handled by agent's todo system
        self.output_buffer.append((f"fg:#6b7280", "\nUse /todo in agent context, or check sidebar\n"))
    
    async def _cmd_undo(self, args: str):
        undone = self.agent.undo_last()
        for u in undone:
            self.output_buffer.append((f"fg:#f59e0b", f"\n��� {u}\n"))
    
    async def _cmd_redo(self, args: str):
        redone = self.agent.redo_last()
        for r in redone:
            self.output_buffer.append((f"fg:#22c55e", f"\n��� {r}\n"))
    
    async def _cmd_memory(self, args: str):
        self.output_buffer.append((f"fg:#6b7280", "\nMemory commands not yet implemented in dispatcher\n"))