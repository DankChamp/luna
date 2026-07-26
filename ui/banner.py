from rich.panel import Panel

from .theme import Neon


BANNER_ART = """\
[bold #ff00ff]   ██╗     ██╗   ██╗███╗   ██╗ █████╗ [/bold #ff00ff]
[bold #ff00ff]   ██║     ██║   ██║████╗  ██║██╔══██╗[/bold #ff00ff]
[bold #ff00ff]   ██║     ██║   ██║██╔██╗ ██║███████║[/bold #ff00ff]
[bold #ff00ff]   ██║     ██║   ██║██║╚██╗██║██╔══██║[/bold #ff00ff]
[bold #ff00ff]   ███████╗╚██████╔╝██║ ╚████║██║  ██║[/bold #ff00ff]
[bold #ff00ff]   ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝[/bold #ff00ff]"""


def make_welcome_panel(
    provider_name: str,
    msg_count: int = 0,
    mode_label: str = "build",
    mode_color: str = Neon.primary,
    persona_name: str = "Luna",
) -> Panel:
    return Panel(
        BANNER_ART + f"\n\n[#ff00ff]  {persona_name} — your coder[/#ff00ff]",
        border_style=Neon.primary,
        padding=(1, 2),
        title="[#ff00ff]✦[/#ff00ff]",
        subtitle=(
            f"[{mode_color}]◈ {mode_label}[/{mode_color}]  "
            f"[#00ffff]{provider_name}[/#00ffff]  "
            f"[#666666]●[/#666666]  "
            f"[#666666]{msg_count} msgs[/#666666]"
        ),
        subtitle_align="right",
    )
