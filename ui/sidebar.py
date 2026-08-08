from __future__ import annotations

from ui.theme import Neon


def build_sidebar_text(
    project: str,
    session_id: str | None,
    token_count: int,
    token_limit: int,
    todos: list[dict],
    branch: str = "",
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []

    def section(title: str):
        result.append(("bold " + Neon.secondary, f"\u2500\u2500 {title} \u2500\u2500\n"))

    def short(s: str, maxlen: int) -> str:
        return s if len(s) <= maxlen else s[: maxlen - 1] + "\u2026"

    section("Project")
    if branch:
        combined = f"{project} ({branch})"
        combined = short(combined, 22)
        result.append((Neon.bright, f"  {combined}\n"))
    else:
        pname = short(project, 22)
        result.append((Neon.bright, f"  {pname}\n"))

    section("Session")
    sdisp = short(session_id or "\u2014", 8)
    result.append((Neon.dim, f"  {sdisp}\n"))

    section("Tokens")
    if token_limit:
        pct = round(token_count / token_limit * 100)
        pct = min(pct, 100)
        result.append((Neon.primary, f"  {pct}%\n"))
    else:
        result.append((Neon.primary, f"  {token_count}\n"))

    section("Todos")
    if todos:
        for t in todos[:5]:
            icon = "\u2713" if t["status"] == "done" else "\u25cb"
            s = Neon.success if t["status"] == "done" else Neon.dim
            text = short(t["content"], 20)
            result.append((s, f"  {icon} {text}\n"))
        remaining = len(todos) - 5
        if remaining > 0:
            result.append((Neon.dim, f"  +{remaining} more\n"))
    else:
        result.append((Neon.dim, "  (none)\n"))

    result.append((Neon.dim, "  " + "\u2500" * 22 + "\n"))
    result.append((Neon.dim, "  Tab  toggle mode\n"))
    result.append((Neon.dim, "  Esc+m  cycle model\n"))
    result.append((Neon.dim, "  C-b  toggle bar\n"))
    result.append((Neon.dim, "  C-d  debug scan\n"))
    result.append((Neon.dim, "  Alt+C  copy reply\n"))

    return result
