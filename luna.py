#!/usr/bin/env python3
"""Luna — your coder. Entry point using decomposed architecture."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from rich.console import Console

from config import Settings
from core import paths
from core.agent_core import AgentConfig, AgentCore
from core.command_dispatcher import CommandDispatcher
from core.commands import CommandLoader
from core.config_manager import ConfigManager
from core.logging import setup_logging
from core.luna_app import LunaApp, LunaAppConfig
from core.modes import AgentMode
from core.permissions import PermissionEvaluator
from core.persona import load_luna_persona
from core.project_config import discover as discover_project_cfg
from core.references import ReferenceManager
from core.router import AIRouter
from core.session_controller import SessionController, SessionControllerConfig
from core.skill_engine import SkillEngine
from core.subagent_engine import SubagentEngine
from core.themes import ThemeManager
from core.tool_executor import ToolExecutor
from tools import create_default_registry
from ui.theme import Neon

console = Console()

# Initialize structured logging
setup_logging(level="INFO", json_output=False)


async def shutdown_handler(session_controller: SessionController, bridge_server=None):
    """Graceful shutdown handler."""
    console.print(f"\n[{Neon.dim}]shutting down...[/{Neon.dim}]")
    if session_controller:
        await session_controller.stop()
    if bridge_server:
        await bridge_server.stop()
    console.print(f"[{Neon.dim}]goodbye[/{Neon.dim}]")
    os._exit(0)


async def run_one_shot(agent_core: AgentCore, prompt: str, model: str | None = None):
    """Run a single prompt and exit."""
    if model:
        await agent_core.set_provider(model=model)
    async for event in agent_core.run(prompt):
        if hasattr(event, 'text') or isinstance(event, str):
            pass


async def run_serve(agent_core: AgentCore, port: int, bridge_token: str):
    """Run the bridge server."""
    from bridge.server import start_server
    await start_server(agent=agent_core, port=port, bridge_token=bridge_token)


async def cmd_agent(args, config_manager: ConfigManager):
    """Manage agents."""
    if not args.action:
        return 1

    if args.action == "list":
        agents = config_manager.list_agents()
        for a in agents:
            active = " * " if a["name"] == config_manager.active_agent else "   "
            print(f"{active}{a['name']}: {a['description']}")
        return 0

    elif args.action == "switch":
        if not args.name:
            return 1
        name = args.name
        if name not in config_manager.get_all_agents():
            return 1
        config_manager.active_agent = name
        return 0

    elif args.action == "info":
        name = args.name or config_manager.active_agent
        agent = config_manager.get_agent(name)
        if not agent:
            return 1
        return 0

    else:
        return 1


async def cmd_session(args, session_controller: SessionController):
    """Manage sessions."""
    if not args.action:
        args.action = "list"

    if args.action in ("list", "ls"):
        sessions = await session_controller.list_sessions()
        if not sessions:
            print("No sessions found")
        else:
            for s in sessions:
                current = " *" if s["id"] == session_controller.current_session_id else ""
                print(f"  {s['id'][:8]}  {s['message_count']} msgs  {s['preview']}{current}")
        return 0

    elif args.action in ("new", "n"):
        await session_controller.new_session()
        return 0

    elif args.action in ("load", "l"):
        if not args.id:
            return 1
        await session_controller.load_session(args.id)
        return 0

    elif args.action in ("delete", "rm"):
        if not args.id:
            return 1
        await session_controller.delete_session(args.id)
        return 0

    elif args.action in ("fork", "f"):
        if not args.id:
            return 1
        return 0

    else:
        return 1


async def cmd_config(args, config_manager: ConfigManager):
    """Manage configuration."""
    if not args.action:
        return 1

    if args.action == "get":
        if not args.key:
            print("Usage: luna config get <key>")
            return 1
        # Get config value - not fully implemented
        print("Config get not yet implemented")
        return 0

    elif args.action == "set":
        if not args.key or not args.value:
            print("Usage: luna config set <key> <value>")
            return 1
        print("Config set not yet implemented")
        return 0

    elif args.action == "edit":
        import subprocess
        editor = os.environ.get("EDITOR", "vim")
        subprocess.run([editor, str(config_manager.config_path)])
        return 0

    elif args.action == "show":
        import yaml
        print(yaml.dump(config_manager._config_to_dict(config_manager.config)))
        return 0

    else:
        return 1


async def cmd_model(args, router: AIRouter):
    """Manage models."""
    active_name = router.active_name

    if not args.action:
        provider = await router.get_provider()
        models = await router.list_models(active_name)
        print(f"Provider: {provider.name}")
        print(f"Current: {router.active_model}")
        print("Available:")
        for m in models:
            marker = " * " if m == router.active_model else "   "
            print(f"{marker}{m}")
        return 0

    if args.action == "list":
        models = await router.list_models(active_name)
        for m in models:
            print(m)
        return 0

    elif args.action == "switch":
        if not args.model:
            return 1
        await router.switch_model(model=args.model)
        return 0

    elif args.action == "next":
        await router.cycle_model()
        return 0

    else:
        return 1


async def cmd_plugin(args, plugin_manager):
    """Manage plugins."""
    if not args.action or args.action == "list":
        plugins = plugin_manager.list_plugins()
        print("Loaded plugins:")
        for p in plugins:
            print(f"  {p}")
        print("\nAvailable plugins:")
        for p in plugin_manager.discover_plugins():
            status = "loaded" if p in plugin_manager._plugins else "available"
            print(f"  {p} ({status})")
        return 0

    if args.action == "load":
        if not args.name:
            return 1
        plugin_manager.load_plugin(args.name)
        return 0

    elif args.action == "unload":
        if not args.name:
            return 1
        plugin_manager.unload_plugin(args.name)
        return 0

    else:
        return 1


async def initialize_core(settings: Settings, args):
    """Initialize all core components."""
    config_mgr = ConfigManager(settings)
    router = AIRouter(settings)
    theme_mgr = ThemeManager()

    if args.theme:
        theme_mgr.set_theme(args.theme)

    # Load persona
    persona_text = load_luna_persona(args.persona)

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
    subagent_engine = SubagentEngine(router, search_dirs=paths.search_dirs("subagents"))
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
            provider_settings = getattr(settings, name, None)
            if provider_settings:
                provider_settings.model = model

    # Create AgentCore
    agent_config = AgentConfig(
        max_iterations=15,
        max_history_tokens=settings.max_history_tokens,
        mode=AgentMode.BUILD,
        system_prompt=persona_text,
    )

    async def provider_getter():
        return await router.get_provider()

    def provider_name_getter():
        return router.active_name

    agent_core = AgentCore(
        router=router,
        tools=tools,
        config=agent_config,
        provider_getter=provider_getter,
        get_provider_name=provider_name_getter,
    )

    # Set mode from project config (if available)
    if project_cfg and hasattr(project_cfg, 'mode') and project_cfg.mode:
        agent_core.set_mode(AgentMode(project_cfg.mode))

    # Create ToolExecutor
    tool_executor = ToolExecutor(
        tools=tools,
        permissions=permissions,
    )
    agent_core.tools.set_executor(tool_executor)

    # Create SessionController
    session_config = SessionControllerConfig(
        session_dir=settings.session_dir,
        auto_save_interval=5.0,
    )
    session_controller = SessionController(
        agent_core=agent_core,
        config=session_config,
    )

    # Load session if specified
    if args.session:
        await session_controller.load_session(args.session)

    # Start session controller
    await session_controller.start()

    # Create PluginManager
    from core.plugins import PluginManager
    plugin_manager = PluginManager(paths.search_dirs("plugins"))
    plugin_manager.load_all_plugins()
    plugin_manager.initialize_all(tools)

    # Load plugins
    from tools.custom import register_custom_tools
    register_custom_tools(tools, *paths.search_dirs("plugins"))

    # Create CommandDispatcher
    output_buffer: list[tuple[str, str]] = []

    command_dispatcher = CommandDispatcher(
        command_loader=command_loader,
        subagents=subagent_engine.manager,
        ref_mgr=ref_mgr,
        skills=skill_engine.manager,
        theme_mgr=theme_mgr,
        router=router,
        agent=agent_core,
        session_controller=session_controller,
        output_buffer=output_buffer,
    )

    # Create LunaApp
    app_config = LunaAppConfig(
        persona=persona_text,
        command_loader=command_loader,
        theme_mgr=theme_mgr,
        ref_mgr=ref_mgr,
        keybinds={},
        session_dir=settings.session_dir,
        router=router,
        agent_core=agent_core,
        subagents=subagent_engine.manager,
        skills=skill_engine.manager,
        memory=None,
        minimal=args.minimal,
    )

    luna_app = LunaApp(app_config)
    luna_app.session_controller = session_controller
    luna_app.command_dispatcher = command_dispatcher

    # Set up subagents for command dispatcher
    command_dispatcher.subagents = subagent_engine.manager

    return {
        "settings": settings,
        "config_mgr": config_mgr,
        "router": router,
        "theme_mgr": theme_mgr,
        "persona_text": persona_text,
        "project_cfg": project_cfg,
        "command_loader": command_loader,
        "ref_mgr": ref_mgr,
        "skill_engine": skill_engine,
        "subagent_engine": subagent_engine,
        "tools": tools,
        "permissions": permissions,
        "agent_core": agent_core,
        "tool_executor": tool_executor,
        "session_controller": session_controller,
        "plugin_manager": plugin_manager,
        "command_dispatcher": command_dispatcher,
        "luna_app": luna_app,
        "output_buffer": output_buffer,
    }


async def run_interactive(core: dict):
    """Run interactive TUI."""
    import sys
    # Allow minimal mode to work without TTY for testing
    if not core["luna_app"].minimal and not sys.stdin.isatty():
        console.print(f"[{Neon.error}]Error: Interactive mode requires a TTY.[/{Neon.error}]")
        console.print(f"[{Neon.dim}]Run with a prompt: luna run \"your prompt\"[/{Neon.dim}]")
        console.print(f"[{Neon.dim}]Or use a proper terminal.[/{Neon.dim}]")
        return 1
    await core["luna_app"].run()
    return 0


async def main_async():
    parser = argparse.ArgumentParser(description="Luna — your coder", prog="luna")
    subparsers = parser.add_subparsers(dest="cmd", help="Commands")

    # Global options
    parser.add_argument("--model", help="Set model (e.g. 'llama3.1:8b' or 'meta/llama-3.1-8b-instruct')")
    parser.add_argument("--theme", help="Theme name (neon, tokyonight, nord, catppuccin, matrix)")
    parser.add_argument("--session", help="Session ID to load")
    parser.add_argument("--persona", help="Persona file path")
    parser.add_argument("--minimal", action="store_true", help="Minimal UI mode (no sidebar, no Rich formatting)")

    # luna run [prompt...]
    run_parser = subparsers.add_parser("run", help="Run with a prompt (non-interactive)")
    run_parser.add_argument("prompt", nargs="*", help="Prompt to send")

    # luna serve
    serve_parser = subparsers.add_parser("serve", help="Run Luna bridge server")
    serve_parser.add_argument("--port", type=int, default=8701, help="Bridge server port")

    # luna agent <list|switch|info> [name]
    agent_parser = subparsers.add_parser("agent", help="Manage agents")
    agent_parser.add_argument("action", choices=["list", "switch", "info"])
    agent_parser.add_argument("name", nargs="?")

    # luna session <list|new|load|delete|fork> [id]
    session_parser = subparsers.add_parser("session", help="Manage sessions")
    session_parser.add_argument("action", choices=["list", "ls", "new", "n", "load", "l", "delete", "rm", "fork", "f"], nargs="?", default="list")
    session_parser.add_argument("id", nargs="?")

    # luna config <get|set|edit|show> [key] [value]
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_parser.add_argument("action", choices=["get", "set", "edit", "show"])
    config_parser.add_argument("key", nargs="?")
    config_parser.add_argument("value", nargs="?")

    # luna model <list|switch|next> [model]
    model_parser = subparsers.add_parser("model", help="Manage models")
    model_parser.add_argument("action", choices=["list", "switch", "next"], nargs="?", default="list")
    model_parser.add_argument("model", nargs="?")

    # luna plugin <list|load|unload> [name]
    plugin_parser = subparsers.add_parser("plugin", help="Manage plugins")
    plugin_parser.add_argument("action", choices=["list", "load", "unload"], nargs="?", default="list")
    plugin_parser.add_argument("name", nargs="?")

    # luna (no args) = interactive
    args = parser.parse_args()

    # Initialize settings
    settings = Settings()

    # Initialize core components
    core = await initialize_core(settings, args)

    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(
            shutdown_handler(core["session_controller"])
        ))

    # Route to command handlers
    if args.cmd == "run":
        if not args.prompt:
            return await run_interactive(core)
        prompt = " ".join(args.prompt)
        await run_one_shot(core["agent_core"], prompt, args.model)
        return 0

    elif args.cmd == "serve":
        await run_serve(core["agent_core"], args.port, settings.bridge_token)
        return 0

    elif args.cmd == "agent":
        return await cmd_agent(args, core["config_mgr"])

    elif args.cmd == "session":
        return await cmd_session(args, core["session_controller"])

    elif args.cmd == "config":
        return await cmd_config(args, core["config_mgr"])

    elif args.cmd == "model":
        return await cmd_model(args, core["router"])

    elif args.cmd == "plugin":
        return await cmd_plugin(args, core["plugin_manager"])

    else:
        # No command = interactive mode
        return await run_interactive(core)


def main():
    try:
        exit_code = asyncio.run(main_async())
        sys.exit(exit_code or 0)
    except KeyboardInterrupt:
        console.print(f"\n[{Neon.dim}]goodbye[/{Neon.dim}]")


if __name__ == "__main__":
    main()
