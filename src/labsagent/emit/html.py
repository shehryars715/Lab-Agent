"""`.html` -- the report as one web page that opens anywhere.

Self-contained on purpose: pictures are embedded as data URIs and the styles
are inline, so the single file can be uploaded, emailed or opened offline and
still look the same. It walks the same blocks as the markdown copy, with the
report spec's cover, labels, sizes and page breaks (for printing).
"""

from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path

from labsagent.blocks import has_screenshot_twin
from labsagent.emit import EmitContext, register
from labsagent.present import arrange

STYLE = """
body {{ font-family: "Segoe UI", system-ui, -apple-system, sans-serif; color: #1f2136;
       max-width: 820px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5;
       font-size: {body}pt; background: #fff; }}
h1 {{ font-size: 1.8em; margin-bottom: .2em; }}
h2 {{ font-size: 1.35em; margin-top: 2em; border-bottom: 1px solid #e5e7eb; padding-bottom: .2em; }}
h3 {{ font-size: 1.05em; margin: 1.2em 0 .3em; }}
.label {{ font-weight: 600; margin: 1em 0 .3em; }}
.tagline {{ font-style: italic; color: #595959; }}
pre {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: {code}pt;
      white-space: pre-wrap; padding: .7em .9em; border-radius: 4px; margin: 0 0 1em; }}
pre.code {{ background: #f2f2f2; }}
pre.output {{ background: #fafafa; border: 1px solid #eee; }}
img {{ max-width: 100%; margin: .3em 0 1em; }}
.cover {{ border-bottom: 3px solid #4f46e5; padding-bottom: 1em; margin-bottom: 2em; }}
.cover table {{ border-collapse: collapse; margin-top: 1em; }}
.cover td {{ padding: .25em 1em .25em 0; vertical-align: top; }}
.cover td:first-child {{ font-weight: 600; color: #595959; }}
.status {{ border-left: 3px solid #d97706; padding-left: .8em; }}
{breaks}
"""


def _data_uri(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def render(ctx: EmitContext) -> str:
    pres = ctx.presentation
    esc = html.escape
    title = pres.cover.title or ctx.spec.title or f"Lab {ctx.spec.lab_number}"
    style = STYLE.format(
        body=pres.body_pt or 11,
        code=pres.code_pt or 9.5,
        breaks="section.task + section.task { break-before: page; }" if pres.page_breaks else "",
    )
    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(title)}</title><style>{style}</style></head><body>",
    ]

    info = ctx.cover
    if info is not None:
        out.append('<header class="cover">')
        if info.department:
            out.append(f'<p class="tagline">{esc(info.department)}</p>')
        out.append(f"<h1>{esc(info.heading())}</h1>")
        if info.tagline:
            out.append(f'<p class="tagline">{esc(info.tagline)}</p>')
        rows = info.rows()
        if rows:
            out.append("<table>")
            out += [f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows]
            out.append("</table>")
        out.append("</header>")
    else:
        out.append(f"<h1>{esc(title)}</h1>")

    for outcome in ctx.outcomes:
        task = outcome.task
        include = ctx.plan_for(task.id).include
        words = ctx.words_for(task.id)
        out.append(f'<section class="task" id="{esc(task.id)}">')
        out.append(f"<h2>{esc(task.title or task.id)}</h2>")
        if include.statement and task.statement.strip():
            out += [f"<p>{esc(p)}</p>" for p in task.statement.strip().split("\n\n")]

        labelled_output = False
        answers_headed = False
        blocks = arrange(outcome, ctx.style_for(task.id), include)
        for index, block in enumerate(blocks):
            if block.kind == "code":
                head = block.title or words.code
                if head:
                    out.append(f'<p class="label">{esc(head)}</p>')
                out.append(f'<pre class="code">{esc(block.text.rstrip())}</pre>')
                labelled_output = False
            elif block.kind == "error":
                out.append(f'<p class="status">{esc(words.status)} {esc(block.text)}</p>')
            elif block.kind == "image" and block.path is not None and Path(block.path).exists():
                if not labelled_output and words.output:
                    out.append(f'<p class="label">{esc(words.output)}</p>')
                labelled_output = True
                out.append(f'<img alt="{esc(Path(block.path).name)}" src="{_data_uri(Path(block.path))}">')
            elif block.kind == "output" and block.text.strip() and not has_screenshot_twin(blocks, index):
                if not labelled_output and words.output:
                    out.append(f'<p class="label">{esc(words.output)}</p>')
                labelled_output = True
                out.append(f'<pre class="output">{esc(block.text.rstrip())}</pre>')
            elif block.kind == "prose":
                if block.role == "answer" and words.answers and not answers_headed:
                    out.append(f"<h3>{esc(words.answers)}</h3>")
                    answers_headed = True
                if block.title:
                    out.append(f'<p class="label">{esc(block.title)}</p>')
                out.append(f"<p>{esc(block.text)}</p>")
        out.append("</section>")

    out.append("</body></html>")
    return "\n".join(out) + "\n"


class HtmlEmitter:
    name = "html"
    label = "Web page"
    kind = "report"
    extensions = (".html",)

    def emit(self, ctx: EmitContext) -> list[Path]:
        path = ctx.out_dir / f"{ctx.report_name()}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(ctx), encoding="utf-8")
        return [path]


register(HtmlEmitter())
