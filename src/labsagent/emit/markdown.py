"""`.md` -- the whole run as one readable text file.

Markdown is where the block IR reads most directly: a `code` block is a fence,
an `output` block is a fence, an `image` block is a link, a `prose` block is a
paragraph. Nothing is projected away, which makes this the honest view of what
the run actually produced -- useful as a diffable record even when the graded
deliverable is something else.

Images are linked relatively, so the file makes sense next to the run's
`screenshots/` directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from labsagent.present import arrange
from labsagent.emit import EmitContext, register


def _rel(path: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(path, start)).as_posix()
    except ValueError:  # different drive on Windows -- absolute is the only option
        return Path(path).as_posix()


def render(ctx: EmitContext) -> str:
    out: list[str] = [f"# {ctx.spec.title}", ""]
    if ctx.spec.course:
        out += [f"**Course:** {ctx.spec.course}", ""]
    for key, value in ctx.profile.as_display().items():
        out.append(f"**{key.replace('_', ' ').title()}:** {value}")
    if ctx.profile.as_display():
        out.append("")

    for outcome in ctx.outcomes:
        task = outcome.task
        out += [f"## {task.title or task.id}", "", task.statement.strip(), ""]

        for block in arrange(outcome, ctx.style, ctx.plan_for(task.id).include):
            if block.kind == "code":
                out += [f"```{block.lang}", block.text.rstrip(), "```", ""]
            elif block.kind == "output":
                out += ["```text", block.text.rstrip(), "```", ""]
            elif block.kind == "image" and block.path is not None:
                name = Path(block.path).name
                out += [f"![{name}]({_rel(Path(block.path), ctx.out_dir)})", ""]
            elif block.kind == "error":
                out += [f"> **Not completed.** {block.text.strip()}", ""]
            elif block.kind == "prose":
                if block.title:
                    out += [f"**{block.title.strip()}**", ""]
                out += [block.text.strip(), ""]

    return "\n".join(out).rstrip() + "\n"


class MarkdownEmitter:
    name = "md"
    label = "Markdown"
    kind = "report"
    extensions = (".md",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        path = ctx.out_dir / f"{ctx.stem()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(ctx), encoding="utf-8")
        return [path]


register(MarkdownEmitter())
