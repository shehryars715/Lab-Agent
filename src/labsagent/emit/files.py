"""`files` -- one submission-ready Python file per task.

"Upload each task as its own .py" is a common submission rule, and the raw
`taskN.py` the solver wrote is the wrong thing to hand in: it has no header
saying which task it answers or whose it is. These do -- the task's title, its
statement as a comment (unless the student left statements out) and the
student's details -- above exactly the code the report shows.

They are written to `report/tasks/` rather than beside the report, so the run
history (which recognises a deliverable by its extension) never mistakes one
for the single combined `.py`.
"""

from __future__ import annotations

from pathlib import Path

from labsagent.blocks import blocks_for
from labsagent.emit import EmitContext, register
from labsagent.emit.script import _comment
from labsagent.present import select

SUBDIR = "tasks"


def render(ctx: EmitContext, outcome) -> str:
    task = outcome.task
    include = ctx.plan_for(task.id).include
    lines = [f"# {task.title or task.id}"]
    lines += [f"# {key.replace('_', ' ').title()}: {value}" for key, value in ctx.profile.as_display().items()]
    if include.statement and task.statement.strip():
        lines.append("#")
        lines += _comment(task.statement)
    lines.append("")
    code = [b.text.rstrip() for b in select(blocks_for(outcome), include) if b.kind == "code"]
    lines.append("\n\n".join(code) if code else "# no solution was produced for this task")
    return "\n".join(lines).rstrip() + "\n"


class FilesEmitter:
    name = "files"
    label = "Task files"
    kind = "code"
    extensions: tuple[str, ...] = ()

    def emit(self, ctx: EmitContext) -> list[Path]:
        folder = ctx.out_dir / SUBDIR
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for outcome in ctx.outcomes:
            path = folder / f"{ctx.stem()}_{outcome.task.id}.py"
            path.write_text(render(ctx, outcome), encoding="utf-8")
            written.append(path)
        return written

    @staticmethod
    def key_for(path: Path) -> str:
        """`code:task2` -- the same key the raw code download uses, which
        these replace when they were asked for."""
        return f"code:{Path(path).stem.rsplit('_', 1)[-1]}"


register(FilesEmitter())
