"""A filesystem backend that actually stays inside its root.

Two problems, both worth remembering.

1. SECURITY. deepagents' FilesystemBackend(root_dir=...) confines RELATIVE
   paths but honours ABSOLUTE ones, so an agent that reads "C:/Users/.../.env"
   gets it. With a live API key in the project .env, that is credential
   disclosure one tool call deep. The general rule: a root_dir parameter is a
   convenience, not a security boundary, until you have tested it with a hostile
   path. Sandboxes, template loaders, archive extractors and static file servers
   all share this failure mode -- path traversal wearing a different hat.

2. NAMESPACES. deepagents shows the model a virtual filesystem rooted at "/",
   so the model naturally writes "/task1.py". That is not a host absolute path.
   A guard on a namespace boundary must MAP, not merely validate -- which is
   what chroot does. Validating without mapping denies legitimate work and sends
   the agent hunting for its own workspace, burning turns and tokens.

   The specific trap: `Path(root) / "/task1.py"` silently discards `root`,
   exactly like `os.path.join("/a", "/b") == "/b"`.

Implementation note: methods are declared EXPLICITLY rather than proxied through
__getattr__, because deepagents detects backend capabilities at class level
(`type(backend).delete is not BackendProtocol.delete`). __getattr__ is an
instance-level fallback and is invisible to that check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

DENIED = "access denied: path resolves outside the workspace"
_ROOT_ALIASES = ("/", "\\", "", ".")


class ConfinedBackend:
    """Delegates to `inner`, mapping agent paths into `root` and refusing escapes."""

    def __init__(self, inner: Any, root: str | Path) -> None:
        self._inner = inner
        self._root = Path(root).resolve()

    # --- the boundary ----------------------------------------------------

    def to_host(self, path: str | None) -> Path | None:
        """Map an agent-namespace path to a host path, or None if it escapes."""
        if path is None:
            return None
        if str(path) in _ROOT_ALIASES:
            return self._root

        candidate = Path(path)
        if candidate.drive:
            # Drive-qualified (C:\...) means the model named a real host path.
            # Allowed only if it genuinely lands inside the workspace.
            resolved = candidate.resolve()
        else:
            # Strip a leading separator first: "/task1.py" is the VIRTUAL root,
            # and joining it raw would discard self._root entirely.
            resolved = (self._root / str(path).lstrip("/\\")).resolve()

        inside = resolved == self._root or self._root in resolved.parents
        return resolved if inside else None

    def allows(self, path: str | None) -> bool:
        return path is None or self.to_host(path) is not None

    def _mapped(self, path: str | None, default_root: bool = False) -> str | None:
        if path is None:
            return str(self._root) if default_root else None
        host = self.to_host(path)
        return None if host is None else str(host)

    # --- guarded operations ----------------------------------------------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        target = self._mapped(file_path)
        if target is None:
            return ReadResult(error=DENIED, file_data=None)
        return self._inner.read(target, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        target = self._mapped(file_path)
        if target is None:
            return WriteResult(error=DENIED)
        return self._inner.write(target, content)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        target = self._mapped(file_path)
        if target is None:
            return EditResult(error=DENIED)
        return self._inner.edit(target, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        target = self._mapped(file_path)
        if target is None:
            return DeleteResult(error=DENIED)
        return self._inner.delete(target)

    def ls(self, path: str) -> LsResult:
        target = self._mapped(path)
        if target is None:
            return LsResult(error=DENIED, entries=[])
        return self._inner.ls(target)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        target = self._mapped(path, default_root=True)
        if target is None:
            return GlobResult(error=DENIED, matches=[])
        return self._inner.glob(pattern, target)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        target = self._mapped(path, default_root=True)
        if target is None:
            return GrepResult(error=DENIED, matches=[])
        return self._inner.grep(
            pattern, target, glob, max_count=max_count, context_lines=context_lines
        )

    # --- async mirrors -----------------------------------------------------

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        target = self._mapped(file_path)
        if target is None:
            return ReadResult(error=DENIED, file_data=None)
        return await self._inner.aread(target, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        target = self._mapped(file_path)
        if target is None:
            return WriteResult(error=DENIED)
        return await self._inner.awrite(target, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        target = self._mapped(file_path)
        if target is None:
            return EditResult(error=DENIED)
        return await self._inner.aedit(target, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> DeleteResult:
        target = self._mapped(file_path)
        if target is None:
            return DeleteResult(error=DENIED)
        return await self._inner.adelete(target)

    async def als(self, path: str) -> LsResult:
        target = self._mapped(path)
        if target is None:
            return LsResult(error=DENIED, entries=[])
        return await self._inner.als(target)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        target = self._mapped(path, default_root=True)
        if target is None:
            return GlobResult(error=DENIED, matches=[])
        return await self._inner.aglob(pattern, target)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        target = self._mapped(path, default_root=True)
        if target is None:
            return GrepResult(error=DENIED, matches=[])
        return await self._inner.agrep(
            pattern, target, glob, max_count=max_count, context_lines=context_lines
        )

    # --- pass-through (no path argument to guard) --------------------------

    def upload_files(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.upload_files(*args, **kwargs)

    def download_files(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.download_files(*args, **kwargs)

    async def aupload_files(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.aupload_files(*args, **kwargs)

    async def adownload_files(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.adownload_files(*args, **kwargs)
