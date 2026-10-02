"""`.zip` -- the submission archive, now opt-in and general.

It used to be unavoidable: every run produced one whether or not it made sense,
so asking for a single `.py` handed you a zip containing a single `.py`. It is
now an emitter like any other, and it runs last so it can package whatever the
others produced rather than a hardcoded list of two filenames.

The layout is unchanged -- deliverables at the root, `code/`, `screenshots/` --
because a grader's expectations are not something to refactor.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from labsagent.blocks import all_shots
from labsagent.emit import EmitContext, register


class ArchiveEmitter:
    name = "zip"
    label = "Complete package"
    kind = "package"
    extensions = (".zip",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        zip_path = ctx.out_dir / f"{ctx.stem()}.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in ctx.produced:
                path = Path(path)
                if path.exists() and path != zip_path:
                    zf.write(path, arcname=path.name)

            for outcome in ctx.outcomes:
                code_path = outcome.code_path
                if code_path and Path(code_path).exists():
                    zf.write(code_path, arcname=f"code/{Path(code_path).name}")
                elif outcome.code_text:
                    zf.writestr(f"code/{outcome.task.id}.py", outcome.code_text)

                # The zip carries the pictures the report carries: figures
                # unless left out, terminal screenshots only when asked for.
                include = ctx.plan_for(outcome.task.id).include
                figures = {Path(f) for f in outcome.figure_paths}
                for shot in all_shots(outcome):
                    shot = Path(shot)
                    wanted = include.figures if shot in figures else include.screenshots
                    if wanted and shot.exists():
                        zf.write(shot, arcname=f"screenshots/{shot.name}")

        return [zip_path]


register(ArchiveEmitter())
