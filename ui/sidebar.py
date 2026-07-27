from __future__ import annotations

from ui.theme import Neon


def build_sidebar_text(
    project: str,
    session_id: str | None,
    token_count: int,
    token_limit: int,
    todos: list[dict],
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []

    def section(title: str):
        result.append(("bold " + Neon.secondary, f"── {title} ──\n"))

    def line(label: str, value: str, style: str = ""):
        s = style or Neon.dim
        result.append((Neon.dim, f" {label} "))
        result.append((s, f"{value}\n"))

    result.append(("bold " + Neon.primary, "  ✦ Luna\n"))
    result.append((Neon.dim, "  ─────────────\n"))

    section("Project")
    disp_project = project[:26] + "…" if len(project) > 27 else project
    line("", disp_project, "bold " + Neon.bright)

    section("Session")
    sid = session_id[:11] + "…" if session_id and len(session_id) > 12 else (session_id or "—")
    line("", sid, Neon.bright)

    section("Tokens")
    pct = f"{token_count}/{token_limit}" if token_limit else str(token_count)
    line("", pct, Neon.primary)

    section("Todos")
    if todos:
        for t in todos[:6]:
            icon = "✓" if t["status"] == "done" else "○"
            s = Neon.success if t["status"] == "done" else Neon.dim
            text = t["content"][:28]
            if len(t["content"]) > 28:
                text += "…"
            result.append((s, f"  {icon} {text}\n"))
        if len(todos) > 6:
            result.append((Neon.dim, f"  … +{len(todos) - 6} more\n"))
    else:
        result.append((Neon.dim, "  (none)\n"))

    result.append((Neon.dim, "  ─────────────\n"))
    result.append((Neon.dim, " Tab  toggle mode\n"))
    result.append((Neon.dim, " Esc+m cycle model\n"))
    result.append((Neon.dim, "  ─────────────\n"))

    return result
