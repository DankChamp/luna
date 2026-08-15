from __future__ import annotations
import asyncio
import sys
from typing import Any, Optional
from pathlib import Path

import anyio
from rich.console import Console

from core.config_manager import ConfigManager
from core.router import AIRouter
from core.agent_core import AgentCore, AgentConfig
from core.session_db import get_session_database
from session.manager import SessionManager
from tools import create_default_registry
from core.permissions import PermissionEvaluator
from core.persona import load_luna_persona
from core.providers.manager import ProviderManager
from core.subagents import SubagentManager
from core.general_subagent import GeneralSubagentManager
from core.skill_engine import SkillEngine
from core.subagent_engine import SubagentEngine
from tools.registry import ToolRegistry
from core.plugins import PluginManager
from tools import create_default_registry as _create_default_registry
from tools.subagent_tool import create_task_tool, create_subagent_tool
from tools.plan_tool import create_plan_tools, create_plan_manage_tool, create_plan_step_tool
from tools.question_tool import create_question_tool
from tools.bash import bash_tool
from tools.read import read_tool
from tools.write import write_tool
from tools.edit import edit_tool
from tools.glob import glob_tool
from tools.grep import grep_tool
from tools.web import web_fetch_tool, web_search_tool
from tools.git import (
    git_status_tool,
    git_diff_tool,
    git_log_tool,
    git_commit_tool,
    git_push_tool,
)
from tools.todo_tool import create_todo_tools
from core.tool_executor import ToolExecutor
from core.session_controller import SessionController, SessionControllerConfig
from core.command_dispatcher import CommandDispatcher
from core.luna_app import LunaApp, LunaAppConfig
from core.modes import AgentMode
from core.themes import ThemeManager
from core.persona import load_luna_persona
from core.project_config import discover as discover_project_cfg
from core.commands import CommandLoader
from core.references import ReferenceManager
from core.themes import ThemeManager as ThemeManagerCore
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
from core.providers.base import AIProvider, StreamEvent, TextChunk, ToolExecStart, ToolExecEnd
from core.observability import get_tracer, get_logger, trace_span
from core import paths

console = Console()


class LunaCLI:
    """Main CLI entry point with OpenCode-style commands."""

    def __init__(self):
        self.settings = None
        self.config_manager = None
        self.router = None
        self.provider_manager = None
        self.agent_core = None
        self.session_controller = None
        self.luna_app = None
        self.plugin_manager = None

    async def initialize(self, env_settings):
        """Initialize all core components."""
        self.settings = env_settings
        self.config_manager = ConfigManager(env_settings)
        self.router = AIRouter(self.config_manager)
        self.provider_manager = ProviderManager(self.config_manager)

        # Initialize provider
        await self.provider_manager.get_provider()

        # Load persona
        persona_text = load_luna_persona(None)

        # Discover project config
        project_cfg = discover_project_cfg()

        # Load commands
        command_loader = CommandLoader(*paths.search_dirs("commands"))

        # Load references
        ref_mgr = ReferenceManager()
        for d in paths.search_dirs("references"):
            ref_mgr.discover(Path(d))

        # Load skills
        skill_engine = SkillEngine(search_dirs=paths.search_dirs("skills"))
        skill_engine.load_skills()

        # Load subagents
        subagent_engine = SubagentEngine(
            self.router,
            search_dirs=paths.search_dirs("subagents")
        )
        subagent_engine.load_subagents()

        # Create tool registry
        tools = create_default_registry()

        # Apply project permissions
        permissions = PermissionEvaluator()
        if project_cfg and project_cfg.permissions:
            permissions = PermissionEvaluator(project_cfg.permissions)
        tools.set_permissions(permissions)

        # Apply project model overrides
        if project_cfg:
            for name, model in project_cfg.agent_models.items():
                self.router.provider_manager.switch_model(None, model)

        # Create AgentCore
        agent_config = AgentConfig(
            max_iterations=15,
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt=persona_text,
        )

        async def provider_getter():
            return await self.router.get_provider()

        def provider_name_getter():
            return self.router.active_name

        self.agent_core = AgentCore(
            router=self.router,
            tools=tools,
            config=agent_config,
            provider_getter=provider_getter,
            get_provider_name=provider_name_getter,
        )

        # Set mode from project config
        if project_cfg and hasattr(project_cfg, 'mode') and project_cfg.mode:
            self.agent_core.set_mode(AgentMode(project_cfg.mode))

        # Create ToolExecutor
        tool_executor = ToolExecutor(
            tools=tools,
            permissions=permissions,
        )
        self.agent_core.tools.set_executor(tool_executor)

        # Create SessionController
        session_config = SessionControllerConfig(
            session_dir=self.settings.luna_session_dir,
            auto_save_interval=5.0,
        )
        self.session_controller = SessionController(
            agent_core=self.agent_core,
            config=session_config,
        )

        # Create PluginManager
        self.plugin_manager = PluginManager(paths.search_dirs("plugins"))
        self.plugin_manager.load_all_plugins()
        self.plugin_manager.initialize_all(tools)

        # Load plugins
        register_custom_tools(tools, *paths.search_dirs("plugins"))

        # Create SessionController
        session_config = SessionControllerConfig(
            session_dir=self.settings.luna_session_dir,
            auto_save_interval=5.0,
        )
        self.session_controller = SessionController(
            agent_core=self.agent_core,
            config=session_config,
        )

        # Load session if specified
        # ... (session loading logic)

        # Start session controller
        await self.session_controller.start()

        # Create CommandDispatcher
        output_buffer: list[tuple[str, str]] = []

        command_dispatcher = CommandDispatcher(
            command_loader=command_loader,
            subagents=subagent_engine.manager,
            ref_mgr=ref_mgr,
            skills=skill_engine.manager,
            theme_mgr=ThemeManager(),
            router=self.router,
            agent=self.agent_core,
            session_controller=self.session_controller,
            output_buffer=output_buffer,
        )

        # Create LunaApp
        app_config = LunaAppConfig(
            persona=persona_text,
            command_loader=command_loader,
            theme_mgr=ThemeManager(),
            ref_mgr=ref_mgr,
            keybinds={},
            session_dir=self.settings.luna_session_dir,
            router=self.router,
            agent_core=self.agent_core,
            subagents=subagent_engine.manager,
            skills=skill_engine.manager,
            memory=None,
        )

        self.luna_app = LunaApp(app_config)
        self.luna_app.session_controller = self.session_controller
        self.luna_app.command_dispatcher = command_dispatcher

    async def run_command(self, args: list[str]) -> int:
        """Run a CLI command."""
        # Handle help flags
        if args and args[0] in ("--help", "-h", "help"):
            return await self.cmd_help([])

        if not args:
            return await self.run_interactive()

        command = args[0]
        cmd_args = args[1:]

        # Built-in commands
        if command == "run":
            return await self.cmd_run(cmd_args)
        elif command == "agent":
            return await self.cmd_agent(cmd_args)
        elif command == "session":
            return await self.cmd_session(cmd_args)
        elif command == "config":
            return await self.cmd_config(cmd_args)
        elif command == "models":
            return await self.cmd_models(cmd_args)
        elif command == "mcp":
            return await self.cmd_mcp(cmd_args)
        elif command == "pr":
            return await self.cmd_pr(cmd_args)
        elif command == "github":
            return await self.cmd_github(cmd_args)
        elif command == "config":
            return await self.cmd_config(cmd_args)
        elif command == "upgrade":
            return await self.cmd_upgrade(cmd_args)
        elif command == "debug":
            return await self.cmd_debug(cmd_args)
        elif command == "stats":
            return await self.cmd_stats(cmd_args)
        elif command == "serve":
            return await self.cmd_serve(cmd_args)
        elif command == "attach":
            return await self.cmd_attach(cmd_args)
        elif command == "share":
            return await self.cmd_share(cmd_args)
        elif command == "export":
            return await self.cmd_export(cmd_args)
        elif command == "import":
            return await self.cmd_import(cmd_args)
        elif command == "plugins":
            return await self.cmd_plugins(cmd_args)
        elif command == "debug":
            return await self.cmd_debug(cmd_args)
        elif command == "help":
            return await self.cmd_help(cmd_args)
        else:
            # Treat as prompt
            return await self.cmd_run(args)

    async def cmd_run(self, args: list[str]) -> int:
        """Run with a prompt (non-interactive or interactive)."""
        if not args:
            return await self.run_interactive()

        prompt = " ".join(args)
        print(f"Running: {prompt}")
        
        async for event in self.agent_core.run(prompt):
            if hasattr(event, 'text') and event.text:
                print(event.text, end="", flush=True)
            elif isinstance(event, str):
                print(event, end="", flush=True)
        print()
        return 0

    async def run_interactive(self) -> int:
        """Run interactive TUI."""
        await self.luna_app.run()
        return 0

    async def cmd_agent(self, args: list[str]) -> int:
        """Manage agents."""
        if not args:
            print("Usage: luna agent <list|switch|create|info>")
            return 1

        action = args[0]
        if action == "list":
            agents = self.config_manager.list_agents()
            for a in agents:
                active = " * " if a["name"] == self.config_manager.active_agent else "   "
                print(f"{active}{a['name']}: {a['description']}")
            return 0

        elif action == "switch":
            if len(args) < 2:
                print("Usage: luna agent switch <name>")
                return 1
            name = args[1]
            if name not in self.config_manager.get_all_agents():
                print(f"Unknown agent: {name}")
                return 1
            self.config_manager.active_agent = name
            print(f"Switched to agent: {name}")
            return 0

        elif action == "info":
            name = args[1] if len(args) > 1 else self.config_manager.active_agent
            agent = self.config_manager.get_agent(name)
            if not agent:
                print(f"Unknown agent: {name}")
                return 1
            print(f"Agent: {name}")
            print(f"  Model: {agent.model or 'default'}")
            print(f"  Mode: {agent.mode}")
            print(f"  Native: {agent.native}")
            print(f"  Permissions: {agent.permissions}")
            return 0

        else:
            print(f"Unknown agent action: {action}")
            return 1

    async def cmd_session(self, args: list[str]) -> int:
        """Manage sessions."""
        db = await get_session_database()

        action = args[0] if args else "list"
        if action in ("list", "ls"):
            sessions = await db.list_sessions()
            for s in sessions:
                current = " * " if s.id == self.session_controller.current_session_id else "   "
                updated = s.updated_at.isoformat()[:19] if hasattr(s.updated_at, "isoformat") else str(s.updated_at)[:19]
                print(f"{current}{s.id[:8]} {updated} {s.message_count} msgs")
            return 0

        if action == "new":
            await self.session_controller.new_session()
            print("New session created")
            return 0

        elif action == "load":
            if len(args) < 2:
                print("Usage: luna session load <id>")
                return 1
            ok = await self.session_controller.load_session(args[1])
            print(f"Session loaded" if ok else "Session not found")
            return 0

        elif action == "delete":
            if len(args) < 2:
                print("Usage: luna session delete <id>")
                return 1
            ok = await db.delete_session(args[1])
            print("Deleted" if ok else "Not found")
            return 0

        elif action == "fork":
            if len(args) < 2:
                print("Usage: luna session fork <id>")
                return 1
            # Fork implementation
            print("Fork not yet implemented")
            return 0

        elif action == "export":
            if len(args) < 2:
                print("Usage: luna session export <id> [file]")
                return 1
            # Export implementation
            print("Export not yet implemented")
            return 0

        elif action == "import":
            if len(args) < 2:
                print("Usage: luna session import <file>")
                return 1
            # Import implementation
            print("Import not yet implemented")
            return 0

        else:
            print(f"Unknown session action: {action}")
            return 1

    async def cmd_config(self, args: list[str]) -> int:
        """Manage configuration."""
        if not args:
            print("Usage: luna config <get|set|edit|show|migrate>")
            return 1

        action = args[0]
        if action == "get":
            if len(args) < 2:
                print("Usage: luna config get <key>")
                return 1
            # Get config value
            return 0

        elif action == "set":
            if len(args) < 3:
                print("Usage: luna config set <key> <value>")
                return 1
            # Set config value
            return 0

        elif action == "edit":
            # Open config in editor
            import subprocess
            subprocess.run(["${EDITOR:-vim}", str(self.config_manager.config_path)])
            return 0

        elif action == "show":
            # Show current config
            import yaml
            print(yaml.dump(self.config_manager._config_to_dict(self.config_manager.config)))
            return 0

        elif action == "migrate":
            # Force migration
            print("Migration already handled automatically")
            return 0

        else:
            print(f"Unknown config action: {action}")
            return 1

    async def cmd_models(self, args: list[str]) -> int:
        """Manage models."""
        if not args:
            provider = await self.router.get_provider()
            models = await self.router.list_models()
            print(f"Provider: {provider.name}")
            print(f"Current: {self.router.active_model}")
            print("Available:")
            for m in models:
                marker = " * " if m == self.router.active_model else "   "
                print(f"{marker}{m}")
            return 0

        action = args[0]
        if action == "list":
            models = await self.router.list_models()
            for m in models:
                print(m)
            return 0

        elif action == "switch":
            if len(args) < 2:
                print("Usage: luna models switch <model>")
                return 1
            await self.router.switch_model(model=args[1])
            print(f"Switched to model: {args[1]}")
            return 0

        elif action == "next":
            model = await self.router.cycle_model()
            print(f"Switched to: {model}")
            return 0

        else:
            print(f"Unknown models action: {action}")
            return 1

    async def cmd_mcp(self, args: list[str]) -> int:
        """Manage MCP servers."""
        print("MCP not yet implemented")
        return 0

    async def cmd_pr(self, args: list[str]) -> int:
        """GitHub PR management."""
        print("PR management not yet implemented")
        return 0

    async def cmd_github(self, args: list[str]) -> int:
        """GitHub integration."""
        print("GitHub integration not yet implemented")
        return 0

    async def cmd_upgrade(self, args: list[str]) -> int:
        """Upgrade Luna."""
        print("Upgrade not yet implemented")
        return 0

    async def cmd_debug(self, args: list[str]) -> int:
        """Debug commands."""
        print("Debug not yet implemented")
        return 0

    async def cmd_stats(self, args: list[str]) -> int:
        """Show usage statistics."""
        print("Stats not yet implemented")
        return 0

    async def cmd_serve(self, args: list[str]) -> int:
        """Run HTTP server."""
        from bridge.server import start_server
        port = int(args[0]) if args else 8701
        print(f"Starting server on port {port}")
        await start_server(
            agent=self.agent_core,
            port=port,
            bridge_token=self.settings.emma_api_key
        )
        return 0

    async def cmd_attach(self, args: list[str]) -> int:
        """Attach to running server."""
        print("Attach not yet implemented")
        return 0

    async def cmd_share(self, args: list[str]) -> int:
        """Share session."""
        print("Share not yet implemented")
        return 0

    async def cmd_export(self, args: list[str]) -> int:
        """Export session."""
        print("Export not yet implemented")
        return 0

    async def cmd_import(self, args: list[str]) -> int:
        """Import session."""
        print("Import not yet implemented")
        return 0

    async def cmd_plugins(self, args: list[str]) -> int:
        """Manage plugins."""
        if not args:
            plugins = self.plugin_manager.list_plugins()
            print("Loaded plugins:")
            for p in plugins:
                print(f"  {p}")
            print("\nAvailable plugins:")
            for p in self.plugin_manager.discover_plugins():
                status = "loaded" if p in self.plugin_manager._plugins else "available"
                print(f"  {p} ({status})")
            return 0

        action = args[0]
        if action == "load":
            if len(args) < 2:
                print("Usage: luna plugins load <name>")
                return 1
            self.plugin_manager.load_plugin(args[1])
            print(f"Loaded plugin: {args[1]}")
            return 0

        elif action == "unload":
            if len(args) < 2:
                print("Usage: luna plugins unload <name>")
                return 1
            self.plugin_manager.unload_plugin(args[1])
            print(f"Unloaded plugin: {args[1]}")
            return 0

        else:
            print(f"Unknown plugin action: {action}")
            return 1

    async def cmd_help(self, args: list[str]) -> int:
        """Show help."""
        print("""
Luna - Your Coder

Usage: luna [command] [args...]

Commands:
  run [message]     Run with a prompt (non-interactive)
  agent             Manage agents
  session           Manage sessions
  config            Manage configuration
  models            Manage models
  mcp               Manage MCP servers
  pr                GitHub PR management
  github            GitHub integration
  config            Manage configuration
  upgrade           Upgrade Luna
  debug             Debug commands
  stats             Show statistics
  serve             Run HTTP server
  attach            Attach to running server
  share             Share session
  export            Export session
  import            Import session
  plugins           Manage plugins
  help              Show this help

Interactive mode: Run 'luna' without arguments to start the TUI.

Examples:
  luna "create a REST API in Python"
  luna agent switch plan
  luna session new
  luna models switch gpt-4
  luna serve --port 8701
""")
        return 0


async def main():
    from config import Settings
    env_settings = Settings()
    cli = LunaCLI()
    await cli.initialize(env_settings)
    return await cli.run_command(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))