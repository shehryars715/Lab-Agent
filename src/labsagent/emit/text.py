"""`.txt` -- the report as plain text, for portals that take nothing else.

Code, printed output and written answers, in the report spec's order and with
its labels. A picture cannot be text, so it is named instead, and the zip is
where it lives.
"""

from __future__ import annotations

from pathlib import Path

from labsagent.emit import EmitContext, register
from labsagent.present import arrange


def _indent(text: str) -> list[str]:
    return ["    " + line for line in text.rstrip("\n").replace("\t", "    ").splitlines()]


def render(ctx: EmitContext) -> str:
    pres = ctx.presentation
    title = pres.cover.title or ctx.spec.title or f"Lab {ctx.spec.lab_number}"
    out = [title, "=" * min(78, max(3, len(title)))]
    if ctx.cover is not None:
        if ctx.cover.tagline:
            out.append(ctx.cover.tagline)
        out += [f"{label}: {value}" for label, value in ctx.cover.rows()]
    out.append("")

    for outcome in ctx.outcomes:
        task = outcome.task
        include = ctx.plan_for(task.id).include
        words = ctx.words_for(task.id)
        heading = task.title or task.id
        out += [heading, "-" * min(78, max(3, len(heading)))]
        if include.statement and task.statement.strip():
            out += [task.statement.strip(), ""]
        answers_headed = False
        for block in arrange(outcome, ctx.style_for(task.id), include):
            if block.kind == "code":
                head = block.title or words.code
                if head:
                    out.append(head)
                out += _indent(block.text) + [""]
            elif block.kind == "output" and block.text.strip():
                if words.output:
                    out.append(words.output)
                out += _indent(block.text) + [""]
            elif block.kind == "image" and block.path is not None:
                out += [f"[picture: {Path(block.path).name}]", ""]
            elif block.kind == "error":
                out += [f"{words.status} {block.text}".strip(), ""]
            elif block.kind == "prose":
                if block.role == "answer" and words.answers and not answers_headed:
                    out.append(words.answers)
                    answers_headed = True
                if block.title:
                    out.append(block.title)
                out += [block.text.strip(), ""]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


class TextEmitter:
    name = "txt"
    label = "Plain text"
    kind = "report"
    extensions = (".txt",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        path = ctx.out_dir / f"{ctx.report_name()}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(ctx), encoding="utf-8")
        return [path]


register(TextEmitter())
