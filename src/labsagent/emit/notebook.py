"""`.ipynb` -- a Jupyter/Colab notebook with real outputs already in it.

This emitter is a thin wrapper and that is the point: `package/notebook.py` was
already a pure function of `(spec, outcomes, student)`, already embedded stdout
as `stream` output and figures as base64 `display_data`, and already ran on
every web job. It simply never reached anyone -- the pipeline appended it into
the zip and registered no download for it, and the history view globbed only
`*.docx` and `*.zip`, so past runs could not see it either.

So there was nothing to build here. There was something to expose.
"""

from __future__ import annotations

from pathlib import Path

from labsagent.emit import EmitContext, register
from labsagent.package.notebook import write_notebook


class NotebookEmitter:
    name = "ipynb"
    label = "Notebook"
    kind = "notebook"
    extensions = (".ipynb",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        path = write_notebook(
            ctx.out_dir / f"{ctx.stem()}.ipynb",
            ctx.spec,
            ctx.outcomes,
            student=ctx.profile.as_display(),
            style=ctx.style,
            tagline=ctx.tagline,
            plans=ctx.plans,
        )
        return [path]


register(NotebookEmitter())
