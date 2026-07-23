#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import argparse
import sys
import os

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Settings
from core.agent import Agent
from core.providers.base import TextChunk
from core.router import AIRouter
from session.manager import SessionManager
from bridge.client import EmmaBridge


console = Console()
settings = Settings()
router = AIRouter(settings)
agent = Agent(settings, router)
session_mgr = SessionManager(settings.luna_session_dir)
emma = EmmaBridge(settings.emma_api_url, settings.emma_api_key)

HISTORY_FILE = os.path.expanduser("~/.luna/history.txt")


async def print_welcome():
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    pname = await agent.provider_name()
    console.print(Panel.fit(
        "[bold magenta]Luna[/bold magenta] — your coder\n"
        f"[dim]Provider: {pname}[/dim]",
        border_style="magenta",
    ))
    console.print("[dim]Commands: /help  /clear  /model  /session  /emma  /exit[/dim]")


def print_help():
    console.print(Panel.fit(
        "[bold]/help[/bold]     — Show this help\n"
        "[bold]/clear[/bold]    — Clear conversation history\n"
        "[bold]/model[/bold]    — Switch AI provider (local / nvidia)\n"
        "[bold]/session[/bold]  — List / switch sessions\n"
        "[bold]/emma[/bold]     — Check Emma connection or send a message\n"
        "[bold]/exit[/bold]     — Exit Luna\n"
        "\n[yellow]Tip:[/yellow] Run [bold]luna \"your prompt\"[/bold] for one-shot mode",
        title="Help",
        border_style="blue",
    ))


async def handle_model(args: list[str]):
    if not args:
        pname = await agent.provider_name()
        console.print(f"[dim]Current provider: {pname}[/dim]")
        console.print("[dim]Available:[/dim]")
        for p in await router.list_providers():
            console.print(f"  • {p}")
        console.print("[dim]Usage: /model <local|nvidia>[/dim]")
        return
    name = args[0].lower()
    if name in ("local", "nvidia"):
        await agent.set_provider(name)
        pname = await agent.provider_name()
        console.print(f"[green]Switched to {pname}[/green]")
    else:
        console.print(f"[red]Unknown provider: {name}. Use local or nvidia.[/red]")


async def handle_session(args: list[str]):
    if not args:
        sessions = session_mgr.list_sessions()
        if not sessions:
            console.print("[dim]No saved sessions[/dim]")
            return
        console.print("[bold]Sessions:[/bold]")
        for s in sessions[:10]:
            marker = " ← current" if s["id"] == session_mgr.current else ""
            console.print(f"  • {s['id'][:19]} ({s['message_count']} msgs){marker}")
        console.print("[dim]Usage: /session <session_id> or /session new[/dim]")
        return

    if args[0] == "new":
        session_mgr.new()
        agent.reset()
        console.print("[green]New session started[/green]")
        return

    msgs = session_mgr.load(args[0])
    if msgs is None:
        console.print(f"[red]Session not found: {args[0]}[/red]")
        return
    agent.load_messages(msgs)
    console.print(f"[green]Loaded session {args[0][:19]} ({len(msgs)} messages)[/green]")


async def handle_emma(args: list[str]):
    connected = await emma.is_connected()
    if not connected:
        console.print("[yellow]Emma is not reachable[/yellow]")
        console.print(f"[dim]Expected at: {settings.emma_api_url}[/dim]")
        return

    if not args:
        console.print("[green]Emma is connected[/green]")
        return

    msg = " ".join(args)
    console.print("[dim]Sending to Emma...[/dim]")
    resp = await emma.chat(msg)
    if resp:
        console.print(Markdown(resp))
    else:
        console.print("[red]No response from Emma[/red]")


async def handle_slash(command: str) -> bool:
    parts = command.strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "/help":
        print_help()
    elif cmd == "/clear":
        agent.reset()
        console.print("[green]History cleared[/green]")
    elif cmd == "/model":
        await handle_model(args)
    elif cmd == "/session":
        await handle_session(args)
    elif cmd == "/emma":
        await handle_emma(args)
    elif cmd == "/exit":
        sys.exit(0)
    else:
        return False
    return True


async def repl():
    await print_welcome()

    prompt_session = PromptSession(
        history=FileHistory(HISTORY_FILE),
        auto_suggest=AutoSuggestFromHistory(),
        vi_mode=True,
    )

    while True:
        try:
            user_input = await prompt_session.prompt_async(
                "[bold magenta]▲[/bold magenta] ",
            )
        except (EOFError, KeyboardInterrupt):
            break

        line = user_input.strip()
        if not line:
            continue

        if line.startswith("/"):
            try:
                await handle_slash(line)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
            continue

        session_mgr.save(agent.messages)

        with console.status("[bold magenta]Luna is thinking...[/bold magenta]"):
            try:
                full_text = ""
                async for event in agent.run(line):
                    if isinstance(event, TextChunk):
                        full_text += event.text
                    elif isinstance(event, str):
                        full_text = event

                if full_text:
                    console.print()
                    console.print(Markdown(full_text))
                    console.print()

            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]")

        session_mgr.save(agent.messages)


async def one_shot(prompt: str):
    full_text = ""
    async for event in agent.run(prompt):
        if isinstance(event, TextChunk):
            full_text += event.text
        elif isinstance(event, str):
            full_text = event
    if full_text:
        console.print(Markdown(full_text))


def main():
    parser = argparse.ArgumentParser(description="Luna — your coder")
    parser.add_argument("prompt", nargs="?", help="One-shot prompt")
    parser.add_argument("--model", "-m", help="Force a specific provider")
    args = parser.parse_args()

    if args.model:
        asyncio.run(agent.set_provider(args.model))

    if args.prompt:
        asyncio.run(one_shot(args.prompt))
    else:
        try:
            asyncio.run(repl())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
