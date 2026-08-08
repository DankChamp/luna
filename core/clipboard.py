"""X11 clipboard backend for Luna — pure Python, no external binaries.

Implements the CLIPBOARD and PRIMARY selections via python-xlib. A small
daemon thread owns the selections and answers SelectionRequest events from
any app that wants to paste; copy() hands the full control flow over to the
daemon's event loop, so no blocking of the PTK event loop occurs here.
"""
from __future__ import annotations

import threading
import time

try:
    from Xlib import X, Xatom, display
    from Xlib.protocol import event as xevent

    _XLIB_OK = True
except Exception:  # pragma: no cover - non-X11 environments
    _XLIB_OK = False

_DISPLAY: "X11Clipboard | None" = None
_DISPLAY_LOCK = threading.Lock()


class X11Clipboard:
    """Owns a tiny X window that serves the CLIPBOARD/PRIMARY selections."""

    def __init__(self, display_name: str | None = None):
        if not _XLIB_OK:
            raise RuntimeError("python-xlib not available")
        if display_name:
            self._d = display.Display(display_name)
        else:
            self._d = display.Display()
        self._root = self._d.screen().root
        self._win = self._root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)

        self._atom_clip = self._d.intern_atom("CLIPBOARD")
        self._atom_primary = self._d.intern_atom("PRIMARY")
        self._atom_targets = self._d.intern_atom("TARGETS")
        self._atom_timestamp = self._d.intern_atom("TIMESTAMP")
        self._atom_utf8 = self._d.intern_atom("UTF8_STRING")
        self._atom_string = self._d.intern_atom("STRING")
        self._atom_text = self._d.intern_atom("TEXT")

        self._data = b""
        self._owns_any = False
        self._lock = threading.Lock()

        # Paste handshake: set while waiting for a SelectionNotify result.
        self._paste_ev: xevent.SelectionNotify | None = None
        self._paste_ready = threading.Event()

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # ── Public API ────────────────────────────────────────────────────

    def copy(self, text: str) -> bool:
        """Copy text to CLIPBOARD (and PRIMARY for middle-click paste)."""
        data = text.encode("utf-8")
        with self._lock:
            self._data = data
            self._owns_any = True
        self._win.set_selection_owner(self._atom_clip, X.CurrentTime)
        self._win.set_selection_owner(self._atom_primary, X.CurrentTime)
        self._d.flush()
        return True

    def paste(self, timeout: float = 1.0) -> str:
        """Fetch whatever is in CLIPBOARD right now (blocking, up to timeout)."""
        with self._lock:
            if self._owns_any:
                return self._data.decode("utf-8", errors="replace")
        prop = self._d.intern_atom("LUNA_PASTE_PROP")
        self._paste_evt = None
        self._paste_ready.clear()
        self._win.convert_selection(
            self._atom_clip, self._atom_utf8, prop, X.CurrentTime
        )
        self._d.flush()
        if not self._paste_ready.wait(timeout):
            return ""
        ev = self._paste_evt
        if ev is None:
            return ""
        props = self._win.get_full_property(prop, X.AnyPropertyType)
        if props is None:
            return ""
        value = props.value
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8", errors="replace")
        return ""

    def owns(self) -> bool:
        with self._lock:
            return self._owns_any

    def shutdown(self) -> None:
        self._stop.set()

    # ── Internals ─────────────────────────────────────────────────────

    def _handle(self, e: object) -> None:
        if isinstance(e, xevent.SelectionRequest):
            self._handle_request(e)
        elif isinstance(e, xevent.SelectionNotify):
            self._paste_evt = e
            self._paste_ready.set()
        elif isinstance(e, xevent.SelectionClear):
            with self._lock:
                self._owns_any = False

    def _handle_request(self, req: xevent.SelectionRequest) -> None:
        target = req.target
        prop = req.property
        requestor = req.requestor

        if target == self._atom_targets:
            atoms = [
                self._atom_targets,
                self._atom_utf8,
                self._atom_string,
                self._atom_text,
                self._atom_timestamp,
            ]
            requestor.change_property(prop, Xatom.ATOM, 32, atoms)
        elif target == self._atom_timestamp:
            requestor.change_property(prop, Xatom.INTEGER, 32, [0])
        elif target in (self._atom_utf8, self._atom_string, self._atom_text):
            with self._lock:
                data = self._data
            requestor.change_property(prop, target, 8, data)
        else:
            prop = X.NONE

        notify = xevent.SelectionNotify(
            time=req.time,
            requestor=requestor,
            selection=req.selection,
            target=target,
            property=prop,
        )
        requestor.send_event(notify, propagate=0)
        self._d.flush()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._d.pending_events():
                    time.sleep(0.02)
                    continue
                e = self._d.next_event()
                self._handle(e)
            except Exception:
                time.sleep(0.02)


def clipboard_backend() -> "X11Clipboard | None":
    """Get a shared clipboard backend, or None when no X display exists."""
    global _DISPLAY
    if not _XLIB_OK:
        return None
    with _DISPLAY_LOCK:
        if _DISPLAY is None:
            try:
                _DISPLAY = X11Clipboard()
            except Exception:
                _DISPLAY = None
        return _DISPLAY


class PtkClipboard:
    """prompt_toolkit Clipboard adapter — routes app copy/paste into the real
    OS clipboard so Ctrl+Y / Alt+W in the input box work across apps too.
    Falls back to an in-memory kill ring when no OS backend is available."""

    def __init__(self) -> None:
        from prompt_toolkit.clipboard.in_memory import InMemoryClipboard

        self.backend = clipboard_backend()
        self._memory = InMemoryClipboard()

    def _target(self):
        return self.backend if self.backend is not None else self._memory

    def set_data(self, data) -> None:
        if self.backend is not None:
            self.backend.copy(data.text)
        else:
            self._memory.set_data(data)

    def get_data(self):
        if self.backend is not None:
            from prompt_toolkit.clipboard.base import ClipboardData

            return ClipboardData(self.backend.paste())
        return self._memory.get_data()

    def rotate(self) -> None:
        self._memory.rotate()

    def set_text(self, text: str) -> None:
        from prompt_toolkit.clipboard.base import ClipboardData

        self.set_data(ClipboardData(text))