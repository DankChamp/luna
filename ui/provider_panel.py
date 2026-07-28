from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PTKStyle

from core.router import AIRouter
from ui.theme import Neon

PT_STYLE = PTKStyle([
    ("prompt", f"bold {Neon.secondary}"),
])


def _render_panel(active: str, providers: list[dict], models: list[str] | None = None) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style=Neon.dim, justify="right")
    t.add_column()

    if not providers:
        return Panel("No providers configured", border_style=Neon.primary)
    active_row = next((p for p in providers if p["name"] == active), providers[0])
    t.add_row("Active", f"[{Neon.secondary}]{active_row['name']}[/{Neon.secondary}]")
    t.add_row("Model", f"[{Neon.primary}]{active_row['model']}[/{Neon.primary}]")

    if models:
        lines = []
        for i, m in enumerate(models):
            marker = " [←]" if m == active_row["model"] else ""
            lines.append(f"{m}{marker}")
        display = ", ".join(lines[:8])
        if len(models) > 8:
            display += f" [{Neon.dim}]+{len(models)-8} more[/{Neon.dim}]"
        t.add_row("Variants", f"[{Neon.dim}]{display}[/{Neon.dim}]")

    t.add_row("Key", active_row["key"])
    t.add_row("URL", f"[{Neon.dim}]{active_row['url']}[/{Neon.dim}]")
    t.add_row("")

    all_providers = "  ".join(
        f"[{Neon.secondary}]●[/{Neon.secondary}] {p['name']}" + (" [←]" if p["active"] else "")
        for p in providers
    )
    t.add_row("Available", all_providers)

    t.add_row("")
    t.add_row("", f"[{Neon.secondary}][1][/{Neon.secondary}] Switch active provider")
    t.add_row("", f"[{Neon.secondary}][2][/{Neon.secondary}] Change model — enter a name")
    t.add_row("", f"[{Neon.secondary}][3][/{Neon.secondary}] Change API key")
    t.add_row("", f"[{Neon.secondary}][4][/{Neon.secondary}] Change base URL")
    t.add_row("", f"[{Neon.secondary}][5][/{Neon.secondary}] 🔍 List models from API")
    t.add_row("", f"[{Neon.secondary}][6][/{Neon.secondary}] ✓ Test connection")
    t.add_row("", f"[{Neon.secondary}][7][/{Neon.secondary}] Done")

    return Panel(
        t,
        border_style=Neon.primary,
        title=f"[{Neon.primary}]== Provider Configuration ==[/{Neon.primary}]",
        title_align="left",
        padding=(0, 1),
    )


def _render_models(models: list[str], title: str = "Available models") -> Panel:
    max_show = 30
    shown = models[:max_show]
    lines = "\n".join(f"  [{Neon.secondary}]{m}[/{Neon.secondary}]" for m in shown)
    if len(models) > max_show:
        lines += f"\n  [{Neon.dim}]... and {len(models) - max_show} more[/{Neon.dim}]"
    return Panel(
        lines,
        border_style=Neon.secondary,
        title=f"[{Neon.secondary}]{title}[/{Neon.secondary}]",
        title_align="left",
        padding=(0, 1),
    )


async def show_provider_panel(router: AIRouter, console: Console, session: PromptSession):
    while True:
        active = router.active_name
        providers = await router.list_providers()
        models = await router.cached_models(active)

        console.clear()
        console.print(_render_panel(active, providers, models))
        console.print()

        choice = await session.prompt_async(
            [("class:prompt", "> ")],
            style=PT_STYLE,
        )
        choice = choice.strip()

        if choice == "1":
            names = [p["name"] for p in providers]
            nav = "\n".join(f"  [{Neon.secondary}]{n}[/{Neon.secondary}]" for n in names)
            console.print(Panel(
                nav,
                border_style=Neon.primary,
                title=f"[{Neon.primary}]Switch provider[/{Neon.primary}]",
                title_align="left",
                padding=(0, 1),
            ))
            target = await session.prompt_async(
                [("class:prompt", "switch to > ")],
                style=PT_STYLE,
            )
            target = target.strip()
            if target in names:
                await router.set_active(target)
                console.print(f"[{Neon.success}]✓ Switched to {target}[/{Neon.success}]")
            else:
                console.print(f"[{Neon.error}]✗ Unknown provider: {target}[/{Neon.error}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "2":
            console.print(_render_models.__wrapped__ if False else "")
            model_name = await session.prompt_async(
                [("class:prompt", "model > ")],
                style=PT_STYLE,
            )
            model_name = model_name.strip()
            if model_name:
                await router.reconfigure(active, model=model_name)
                console.print(f"[{Neon.success}]✓ Model set to {model_name}[/{Neon.success}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "3":
            new_key = await session.prompt_async(
                [("class:prompt", "API key > ")],
                style=PT_STYLE,
                is_password=True,
            )
            new_key = new_key.strip()
            if new_key:
                await router.reconfigure(active, api_key=new_key)
                console.print(f"[{Neon.success}]✓ API key updated[/{Neon.success}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "4":
            current = next((p for p in providers if p["name"] == active), None)
            default = current["url"] if current else ""
            console.print(f"[{Neon.dim}]Current: {default}[/{Neon.dim}]")
            new_url = await session.prompt_async(
                [("class:prompt", "URL > ")],
                style=PT_STYLE,
                default=default,
            )
            new_url = new_url.strip()
            if new_url and new_url != default:
                await router.reconfigure(active, base_url=new_url)
                console.print(f"[{Neon.success}]✓ URL updated[/{Neon.success}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "5":
            with console.status(f"[{Neon.dim}]Fetching models...[/{Neon.dim}]"):
                models = await router.list_models(active)
            if models:
                console.print(_render_models(models, f"Models for {active}"))
                model_choice = await session.prompt_async(
                    [("class:prompt", "select model (or Enter to skip) > ")],
                    style=PT_STYLE,
                )
                model_choice = model_choice.strip()
                if model_choice in models:
                    await router.reconfigure(active, model=model_choice)
                    console.print(f"[{Neon.success}]✓ Model set to {model_choice}[/{Neon.success}]")
                elif model_choice:
                    console.print(f"[{Neon.error}]✗ Not in list, use option 2 to set custom[/{Neon.error}]")
            else:
                console.print(f"[{Neon.error}]✗ No models returned. Check your API key and URL.[/{Neon.error}]")
                console.print(f"[{Neon.dim}]Tip: Use option 3/4 to update credentials first.[/{Neon.dim}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "6":
            with console.status(f"[{Neon.dim}]Testing {active}...[/{Neon.dim}]"):
                ok, msg = await router.test_connection(active)
            if ok:
                console.print(f"[{Neon.success}]✓ {msg}[/{Neon.success}]")
            else:
                console.print(f"[{Neon.error}]✗ {msg}[/{Neon.error}]")
            await session.prompt_async("Press Enter to continue...")

        elif choice == "7":
            console.clear()
            console.print(f"[{Neon.success}]✓ Configuration saved[/{Neon.success}]")
            break

        else:
            console.print(f"[{Neon.dim}]Choose 1-7[/{Neon.dim}]")
            await session.prompt_async("Press Enter to continue...")
