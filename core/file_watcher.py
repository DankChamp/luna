from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Callable, Coroutine, Optional, Union


AsyncCallback = Callable[[list[str]], Coroutine[None, None, None]]


class FileWatcher:
    def __init__(self, callback: Optional[Union[Callable[[list[str]], None], AsyncCallback]] = None):
        self._callback = callback
        self._task: asyncio.Task | None = None
        self._known: dict[str, float] = {}
        self._running = False

    async def start(self, directory: str | Path, interval: float = 2.0):
        self._running = True
        self._task = asyncio.create_task(self._poll(Path(directory), interval))

    async def _poll(self, directory: Path, interval: float):
        try:
            while self._running:
                changes = self._scan(directory)
                if changes and self._callback:
                    r = self._callback(changes)
                    if r is not None:
                        await r
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            import sys
            print(f"[file_watcher] error: {e}", file=sys.stderr)

    def _scan(self, directory: Path) -> list[str]:
        changes = []
        if not directory.exists():
            return changes
        for root, dirs, files in os.walk(directory):
            if ".git" in dirs:
                dirs.remove(".git")
            if "__pycache__" in dirs:
                dirs.remove("__pycache__")
            for f in files:
                path = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(path)
                    prev = self._known.get(path)
                    if prev is not None and mtime > prev:
                        changes.append(path)
                    self._known[path] = mtime
                except OSError:
                    pass
        return changes

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
