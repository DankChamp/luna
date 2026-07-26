from __future__ import annotations
import json
from pathlib import Path

from prompt_toolkit.key_binding import KeyBindings


def load_keybinds(path: str | Path | None = None) -> KeyBindings:
    kb = KeyBindings()

    cfg_path = Path(path or Path.home() / ".luna" / "keybinds.json").expanduser()
    if not cfg_path.exists():
        return kb

    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return kb

    bindings = raw if isinstance(raw, list) else raw.get("bindings", [])
    for entry in bindings:
        keys = entry.get("keys", "")
        action = entry.get("action", "")
        if not keys or not action:
            continue

        def _make_handler(action_name: str):
            if action_name == "exit":
                return lambda e: e.app.exit()

            if action_name == "cancel":
                return lambda e: e.app.current_buffer.reset()

            return None

        handler = _make_handler(action)
        if handler is None:
            handler = lambda e: None

        try:
            kb.add(keys, filter=None)(handler)
        except Exception:
            pass

    return kb
