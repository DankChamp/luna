#!/usr/bin/env python3
"""Luna — your coder. Entry point using decomposed architecture."""
from __future__ import annotations
import asyncio
import argparse
import os
import sys

from rich.console import Console

from config import Settings
from core.config_manager import ConfigManager
from core.router import AIRouter
from core.agent_core import AgentCore, AgentConfig
from core.tool_executor import ToolExecutor
from core.skill_engine import SkillEngine
from core.subagent_engine import SubagentEngine
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
from tools.registry import create_default_registry
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
from session.manager import SessionManager
from bridge.client import EmmaBridge
from ui.theme import Neon
from ui.banner import make_welcome_panel
from ui.provider_panel import show_provider_panel
from core import paths

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
    command_loader = CommandLoader()
    command_loader.discover(paths.search_dirs("commands"))
    
    # Load references
    ref_mgr = ReferenceManager()
    ref_mgr.discover(paths.search_dirs("references"))
    
    # Load skills
    skill_engine = SkillEngine(search_dirs=paths.search_dirs("skills"))
    skill_engine.load_skills()
    
    # Load subagents
    subagent_engine = SubagentEngine(search_dirs=paths.search_dirs("subagents"))
    subagent_engine.load_subagents()
    
    # Create tool registry
    tools = create_default_registry()
    
    # Apply project permissions
    permissions = PolicyEvaluator()
    if project_cfg and project_cfg.permissions:
        permissions = PolicyEvaluator(project_cfg.permissions)
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
    
    # Set mode from project config
    if project_cfg and project_cfg.mode:
        agent_core.set_mode(AgentMode(project_cfg.mode))
    
    # Create ToolExecutor
    tool_executor = ToolExecutor(
        registry=tools,
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
    command_loader = CommandLoader()
    command_loader.discover(paths.search_dirs("commands"))
    
    command_dispatcher = CommandDispatcher(
        command_loader=command_loader,
        subagents=subagent_engine.manager,
        ref_mgr=ReferenceManager(),  # Will be populated
        skills=skill_engine.manager,
        theme_mgr=theme_mgr,
        router=router,
        agent=agent_core,
        session_controller=session_controller,
        output_buffer=output_buffer,
    )
    
    # Discover references for command dispatcher
    ref_mgr_dispatch = ReferenceManager()
    ref_mgr_dispatch.discover(paths.search_dirs("references"))
    command_dispatcher.ref_mgr = ref_mgr_dispatch
    
    # Discover skills for command dispatcher
    skill_engine_dispatch = SkillEngine(search_dirs=paths.search_dirs("skills"))
    skill_engine_dispatch.load_skills()
    command_dispatcher.skills = skill_engine_dispatch.manager
    
    # Create LunaApp
    app_config = LunaAppConfig(
        persona=persona_text,
        command_loader=command_loader,
        theme_mgr=theme_mgr,
        ref_mgr=ref_mgr_dispatch,
        keybinds={},
        session_dir=settings.luna_session_dir,
        router=router,
        agent_core=agent_core,
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
        result = await agent_core.run(prompt)
        async for event in result:
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