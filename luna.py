#!/usr/bin/env python3
"""Luna — your coder. Entry point using decomposed architecture."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rich.console import Console

from config import Settings
from core import paths
from core.agent_core import AgentConfig, AgentCore
from core.command_dispatcher import CommandDispatcher
from core.commands import CommandLoader
from core.config_manager import ConfigManager
from core.luna_app import LunaApp, LunaAppConfig
from core.modes import AgentMode
from core.persona import load_luna_persona
from core.permissions import PermissionEvaluator
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


async def main_async():
    parser = argparse.ArgumentParser(description="Luna — your coder")
    parser.add_argument("prompt", nargs="*", help="One-shot prompt")
    parser.add_argument("--model", help="Set model (e.g. 'llama3.1:8b' or 'meta/llama-3.1-8b-instruct')")
    parser.add_argument("--serve", action="store_true", help="Run Luna bridge server")
    parser.add_argument("--port", type=int, default=8701, help="Bridge server port")
    parser.add_argument("--theme", help="Theme name (neon, tokyonight, nord, catppuccin, matrix)")
    parser.add_argument("--session", help="Session ID to load")
    parser.add_argument("--persona", help="Persona file path")
    args = parser.parse_args()

    # Settings & core services
    settings = Settings()
    config_mgr = ConfigManager(settings)
    router = AIRouter(config_mgr)
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
            router.set_model_override(name, model)

    # Create AgentCore
    agent_config = AgentConfig(
        max_iterations=15,
        max_history_tokens=100,
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
        session_dir=settings.luna_session_dir,
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
        session_dir=settings.luna_session_dir,
        router=router,
        agent_core=agent_core,
        subagents=subagent_engine.manager,
        skills=skill_engine.manager,
        memory=None,  # Will be set if MemoryStore is available
    )

    luna_app = LunaApp(app_config)
    luna_app.session_controller = session_controller
    luna_app.command_dispatcher = command_dispatcher

    # Set up subagents for command dispatcher
    command_dispatcher.subagents = subagent_engine.manager

    # Parse prompt if provided
    if args.prompt:
        prompt = " ".join(args.prompt)
        if args.model:
            await agent_core.set_provider(model=args.model)
        async for event in agent_core.run(prompt):
            if hasattr(event, 'text'):
                print(event.text, end="", flush=True)
            elif isinstance(event, str):
                print(event, end="", flush=True)
        print()
        return

    if args.serve:
        from bridge.server import start_server
        await start_server(agent=agent_core, port=args.port, bridge_token=settings.emma_api_key)
        return

    # Run REPL
    await luna_app.run()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print(f"\n[{Neon.dim}]goodbye[/{Neon.dim}]")


if __name__ == "__main__":
    main()
