"""`.py` -- just the code, runnable top to bottom.

The narrowest possible deliverable, and the one a "give me a .py" request
means. Each task contributes its statement as a comment header and then its
source; nothing else survives, because a screenshot and a Word cover page have
no representation in a Python file and pretending otherwise would produce a
file that does not run.
"""

from __future__ import annotations

from pathlib import Path

from labsagent.blocks import blocks_for
from labsagent.present import select
from labsagent.emit import EmitContext, register


def _comment(text: str, width: int = 88) -> list[str]:
    """Wrap a task statement into `# ` comment lines."""
    out: list[str] = []
    for para in text.strip().splitlines():
        para = para.strip()
        if not para:
            continue
        line = "#"
        for word in para.split():
            if len(line) + len(word) + 1 > width:
                out.append(line)
                line = "#"
            line += f" {word}"
        out.append(line)
    return out


def render(ctx: EmitContext) -> str:
    lines: list[str] = [f'"""{ctx.spec.title}']
    if ctx.spec.course:
        lines.append(f"{ctx.spec.course}")
    for key, value in ctx.profile.as_display().items():
        lines.append(f"{key.replace('_', ' ').title()}: {value}")
    lines += ['"""', ""]

    for outcome in ctx.outcomes:
        task = outcome.task
        lines.append("# " + "-" * 70)
        lines.append(f"# {task.title or task.id}")
        lines.append("# " + "-" * 70)
        lines += _comment(task.statement)
        lines.append("")

        blocks = select(blocks_for(outcome), ctx.plan_for(task.id).include)
        for block in blocks:
            if block.kind == "code":
                lines.append(block.text.rstrip())
                lines.append("")
            elif block.kind == "error":
                lines += _comment(block.text)
                lines.append("")
            elif block.kind == "prose":
                if block.title:
                    lines += _comment(f"Q: {block.title}")
                lines += _comment(block.text)
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class ScriptEmitter:
    name = "py"
    label = "Python script"
    kind = "code"
    extensions = (".py",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        path = ctx.out_dir / f"{ctx.stem()}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(ctx), encoding="utf-8")
        return [path]


register(ScriptEmitter())
