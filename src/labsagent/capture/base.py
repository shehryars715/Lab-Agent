"""The ScreenshotBackend protocol -- the second load-bearing interface.

RenderedBackend (now) draws a Transcript as a terminal-styled PNG.
LiveWindowBackend (Phase 8) drives a real window and grabs the desktop.
Both satisfy this protocol, so nothing upstream changes when you swap them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from labsagent.models import Transcript


@runtime_checkable
class ScreenshotBackend(Protocol):
    def render(self, transcript: Transcript, out_path: Path) -> list[Path]:
        """Render `transcript` to one or more PNGs.

        Returns a list because long output paginates rather than producing a
        single unreadably tall image.
        """
        ...
