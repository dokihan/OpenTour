from __future__ import annotations

import contextvars
import sys
import threading

_TAG: contextvars.ContextVar[str] = contextvars.ContextVar("request_player", default="")

def tag_of(player_id: str) -> str:
    return f"[{player_id}]"

def set_player(player_id: str | None) -> None:
    _finish_lines()
    _TAG.set(tag_of(player_id) if player_id else "")

def clear_player() -> None:
    _finish_lines()
    _TAG.set("")

def _finish_lines() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TaggedStream):
            stream.finish()

class TaggedStream:

    def __init__(self, stream):
        self.stream = stream
        self._local = threading.local()

    def write(self, text: str) -> int:
        tag = _TAG.get()
        line = getattr(self._local, "line", "")
        if not tag and not line:
            return self.stream.write(text)
        *done, self._local.line = (line + text).split("\n")
        if done:
            self.stream.write("".join(f"{tag} {d}\n" if tag else f"{d}\n" for d in done))
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def finish(self) -> None:
        if getattr(self._local, "line", ""):
            self.write("\n")

    def __getattr__(self, name):
        return getattr(self.stream, name)

def install() -> None:
    for name in ("stdout", "stderr"):
        if not isinstance(getattr(sys, name), TaggedStream):
            setattr(sys, name, TaggedStream(getattr(sys, name)))
