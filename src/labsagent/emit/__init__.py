"""The output seam.

WHY A SEAM AND NOT A FUNCTION. Before this package, the output format was
chosen by an `import` and an f-string inside `web/server/pipeline.py`, and the
same sequence was written a second time elsewhere. The two had already drifted.
Any new format had to be added twice and could be asked for by nobody, because
no user input reached that code at all.

This is the same shape as the three seams the codebase already has --
`Sandbox`, `ScreenshotBackend`, `EventConsumer` -- and it was the conspicuous
omission from that list.

EVERY EMITTER IS A PURE FUNCTION OF THE IR. `TaskOutcome` was always
format-free; its own docstring says everything downstream of ingest consumes it
and never raw DOCX. So an emitter needs nothing but the spec, the outcomes and
somewhere to write. Two emitters need slightly more -- the in-place DOCX
annotator needs the source document and its anchors, the archive needs to know
what the other emitters produced -- and `EmitContext` carries those without
widening anyone else's signature.

FAILURE IS ISOLATED. `emit_all` runs each emitter in its own try/except. A
rendering bug used to cost a fully-paid run: a bad anchor raised `IndexError`
after every task had been solved, the catch-all in `pipeline.py` turned it into
a job error, and not one artifact was registered even though `manifest.json` on
disk held every result. Now the others still land and the caller is told
exactly which one failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from labsagent.models import LabSpec, TaskOutcome
from labsagent.profile import StudentProfile


@dataclass
class EmitContext:
    """Everything any emitter might need, so none of them need a bespoke call.

    `manual_path` and `anchors` are populated only when the upload was a .docx;
    the in-place annotator is the sole consumer. `produced` accumulates as
    emitters run, which is how the archive can package what came before it.
    """

    spec: LabSpec
    outcomes: list[TaskOutcome]
    out_dir: Path
    profile: StudentProfile = field(default_factory=StudentProfile)
    cover: object | None = None
    manual_path: Path | None = None
    anchors: dict[str, int] = field(default_factory=dict)
    produced: list[Path] = field(default_factory=list)
    #: How each task is laid out -- see `labsagent.present`. `classic` is the
    #: old fixed order, and the default for every caller that does not choose.
    style: str = "classic"
    #: One line about this lab, for headers that have room for it.
    tagline: str = ""

    def stem(self) -> str:
        """`Lab03_22F-1234` -- the shared basename for everything but the report."""
        return f"Lab{self.spec.lab_number}_{self.profile.slug()}"


@runtime_checkable
class Emitter(Protocol):
    name: str
    label: str
    kind: str

    def emit(self, ctx: EmitContext) -> list[Path]:
        """Write this format and return every file written."""


REGISTRY: dict[str, Emitter] = {}

#: What a run produces when nobody asked for anything in particular. This is
#: the only place the old hardcoded behaviour still lives, and now it is a
#: default rather than a law.
#:
#: The notebook is in the default set because it always was: the old pipeline
#: built one on every run and appended it into the zip. It simply had no
#: download of its own, and the history view globbed only *.docx and *.zip, so
#: nobody could reach it. Keeping it here preserves the archive's contents
#: exactly while finally surfacing the file.
DEFAULT_ARTIFACTS = ("docx", "ipynb", "zip")

#: Emitters that must run after the ones whose files they consume.
_LAST = ("zip",)


def register(emitter: Emitter) -> Emitter:
    REGISTRY[emitter.name] = emitter
    return emitter


def order(names: list[str] | tuple[str, ...]) -> list[str]:
    """Known emitters, de-duplicated, with archive-like ones moved to the end."""
    seen: list[str] = []
    for n in names:
        if n in REGISTRY and n not in seen:
            seen.append(n)
    return [n for n in seen if n not in _LAST] + [n for n in seen if n in _LAST]


def emit_all(
    ctx: EmitContext,
    names: list[str] | tuple[str, ...],
    on_file: Callable[[Emitter, Path], None] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> list[Path]:
    """Run each requested emitter in isolation. One failing never stops another.

    `on_file` is called per written file so the caller can register a download
    as soon as it exists; `on_error` receives the emitter name and the
    exception, so the failure can be said out loud instead of swallowed.
    """
    for name in order(names):
        emitter = REGISTRY[name]
        try:
            written = emitter.emit(ctx)
        except Exception as exc:  # noqa: BLE001 -- one bad format must not sink the rest
            if on_error is not None:
                on_error(name, exc)
            continue
        for path in written:
            ctx.produced.append(path)
            if on_file is not None:
                on_file(emitter, path)
    return list(ctx.produced)


# Registration happens on import, so the registry is populated by the act of
# importing this package. Kept at the bottom: each module imports `register`
# and `EmitContext` from here, and they must exist first.
from labsagent.emit import archive, docx, markdown, notebook, script  # noqa: E402,F401
