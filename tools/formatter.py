from __future__ import annotations
import asyncio
from pathlib import Path

EXTENSION_FORMATTERS: dict[str, list[str]] = {
    ".py": ["ruff", "format"],
    ".pyi": ["ruff", "format"],
    ".js": ["npx", "prettier", "--write"],
    ".jsx": ["npx", "prettier", "--write"],
    ".ts": ["npx", "prettier", "--write"],
    ".tsx": ["npx", "prettier", "--write"],
    ".css": ["npx", "prettier", "--write"],
    ".html": ["npx", "prettier", "--write"],
    ".json": ["npx", "prettier", "--write"],
    ".md": ["npx", "prettier", "--write"],
    ".yaml": ["npx", "prettier", "--write"],
    ".yml": ["npx", "prettier", "--write"],
    ".go": ["gofmt"],
    ".rs": ["rustfmt"],
    ".rb": ["rubocop", "-a"],
    ".kt": ["ktlint", "-F"],
    ".kts": ["ktlint", "-F"],
    ".dart": ["dart", "format"],
    ".sh": ["shfmt", "-w"],
}


class FormatterManager:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    async def format(self, path: str) -> str:
        if not self.enabled:
            return ""
        p = Path(path)
        ext = p.suffix.lower()
        cmd = EXTENSION_FORMATTERS.get(ext)
        if not cmd:
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, str(p),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                return f"format timed out for {p.name}"
            if proc.returncode == 0:
                return f"formatted {p.name}"
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                return f"format error: {err}"
            return ""
        except FileNotFoundError:
            return f"formatter not found for {ext}"
        except Exception:
            return ""
