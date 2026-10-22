"""The editing agent: the open-ended fallback for how the files look.

WHY THIS EXISTS (2026-10-22). The report spec (`present.Presentation`) covers
what students usually ask -- the cover, the layout, labels, sizes, page breaks,
formats -- and does it for free, the same way every time. But no list of
fields covers "make the headings dark blue", "add a table of contents" or "put
the university's name in the footer", and a request that fits no field used to
come back as "I'm not sure what to change there". This is where those go: a
small agent, built exactly like the solver (same tools, same confined
filesystem, same sandbox), writes ONE python-docx script that makes the change.

THREE RULES MAKE IT SAFE TO SAY YES.

1. THE SCRIPT IS THE EDIT, NOT THE FILE. Any later change rebuilds the files
   from the content -- re-solving a task, a new cover line, a name added -- and
   a one-off change to the old file would silently vanish. So the script is
   saved (`<run>/edits/NN.py`) and replayed, in order, after every rebuild
   (`replay_edits`). An edit that no longer fits is said, never dropped quietly.

2. TRUST EXECUTION, NOT THE AGENT'S CLAIM. Whatever the agent did to its own
   working copy is thrown away. Its final script is run again by us, on a
   pristine copy of the current file, exactly as a replay will run it -- so
   what ships is what the saved script produces, and a script that only worked
   because of something an earlier run left behind fails here, not next week.

3. A GUARD IN CODE, NOT A PROMISE IN THE PROMPT (`check_edit`). The owner's
   rule: the editor changes how the report looks and adds text the student
   dictated; it never changes the work. Every line of the student's code,
   output and written answers that was in the file must still be in it, and no
   picture may disappear. An edit that breaks that is rejected and the file is
   left as it was.

Cost: one agent loop, bounded by `edit_max_turns` and `max_cost_per_edit_usd`
(about three credits at most), inside the follow-up's own credit hold.
"""

from __future__ import annotations

import html as htmllib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langgraph.errors import GraphRecursionError

from labsagent import credits
from labsagent.agent.build import build_model, build_solver, solver_config
from labsagent.agent.prompts import EDITOR_PROMPT
from labsagent.budget import BudgetExceeded, LiveUsage
from labsagent.errors import SandboxError
from labsagent.intent import detect_formats
from labsagent.present import FORMAT_NAMES
from labsagent.report.outline import outline
from labsagent.runner import run_solution
from labsagent.sandbox import make_sandbox

#: What the editor may change, most likely meant first: "make the headings
#: blue" means the Word report when there is one.
TARGETS = (("docx", ".docx"), ("html", ".html"), ("ipynb", ".ipynb"), ("md", ".md"), ("txt", ".txt"))
SCRIPT = "edit.py"
WORK = "edit"
REPLAY = "edit_replay"
TIMEOUT_S = 60
MIN_PROTECTED = 4


@dataclass
class EditOutcome:
    #: applied | blocked | failed | rejected | nothing
    status: str
    #: One sentence for the chat.
    note: str
    #: The saved edit, when applied (goes into `Presentation.edits`).
    edit: dict | None = None
    target: Path | None = None


def targets(produced) -> dict[str, Path]:
    """Format -> file, for the files this rebuild produced that an edit can change."""
    by_suffix = {suffix: fmt for fmt, suffix in TARGETS}
    out: dict[str, Path] = {}
    for path in produced:
        path = Path(path)
        fmt = by_suffix.get(path.suffix.lower())
        if fmt and fmt not in out and path.parent.name != "tasks":
            out[fmt] = path
    return out


def _target_for(request: str, available: dict[str, Path]) -> str | None:
    named = [f for f in detect_formats(request) if f in available]
    if named:
        return named[0]
    return next((fmt for fmt, _ in TARGETS if fmt in available), None)


# -- the guard ----------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _content(path: Path) -> tuple[str, int]:
    """(all of the file's text, flattened; how many pictures it holds)."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        import docx

        from labsagent.report.docx_utils import flatten_paragraphs

        doc = docx.Document(str(path))
        text = " ".join(p.text for p in flatten_paragraphs(doc))
        return _norm(text), len(doc.inline_shapes)
    if suffix == ".ipynb":
        import nbformat

        nb = nbformat.read(str(path), as_version=4)
        parts: list[str] = []
        pictures = 0
        for cell in nb.cells:
            parts.append(cell.source or "")
            for output in cell.get("outputs", []):
                parts.append(str(output.get("text") or ""))
                data = output.get("data") or {}
                pictures += sum(1 for kind in data if kind.startswith("image/"))
        return _norm(" ".join(parts)), pictures
    raw = path.read_text(encoding="utf-8")
    if suffix == ".html":
        pictures = len(re.findall(r"<img\b", raw, re.I))
        text = htmllib.unescape(re.sub(r"<[^>]+>", " ", raw))
        return _norm(text), pictures
    return _norm(raw), raw.count("![")


def _protected(outcomes) -> list[str]:
    """The student's work, line by line: code, output, written answers."""
    texts: list[str] = []
    for outcome in outcomes:
        texts.append(outcome.code_text or "")
        if outcome.transcript is not None:
            texts.append("\n".join(outcome.transcript.lines))
        texts.append(outcome.explanation or "")
        for item in getattr(outcome, "answers", None) or []:
            texts.append(str(item.get("answer") or ""))
        for part in getattr(outcome, "sections", None) or []:
            texts += [str(part.get("code") or ""), str(part.get("output") or "")]
    lines: list[str] = []
    for text in texts:
        for line in str(text).splitlines():
            line = _norm(line)
            if len(line) >= MIN_PROTECTED and line not in lines:
                lines.append(line)
    return lines


def check_edit(before: Path, after: Path, outcomes) -> str | None:
    """None when the edit kept the student's work; otherwise why not."""
    try:
        old_text, old_pictures = _content(before)
    except Exception as exc:  # noqa: BLE001 -- nothing to compare against
        return f"the file before the change would not open ({type(exc).__name__})"
    try:
        new_text, new_pictures = _content(after)
    except Exception as exc:  # noqa: BLE001
        return f"the changed file would not open ({type(exc).__name__})"
    if new_pictures < old_pictures:
        lost = old_pictures - new_pictures
        return f"it removed {lost} picture{'s' if lost != 1 else ''}"
    # Only what is actually in the file can be lost: a part the student left
    # out (no code, say) is not protected, because it was never there.
    for line in _protected(outcomes):
        if line in old_text and line not in new_text:
            return f"it changed part of your work (“{line[:60]}”)"
    return None


# -- running a script ourselves ---------------------------------------------------


def _fresh(folder: Path) -> Path:
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def apply_script(settings, store, source: str, target: Path, outcomes) -> str | None:
    """Run an edit script on a pristine copy of `target`; keep the result only
    if it ran cleanly and passed the guard. None means applied.

    Raises SandboxError when the runner itself failed: that is the world
    failing, not the edit, and must not mark a saved edit as broken.
    """
    work = _fresh(store.workspace / REPLAY)
    name = f"report{target.suffix.lower()}"
    shutil.copyfile(target, work / name)
    (work / SCRIPT).write_text(source, encoding="utf-8")
    with make_sandbox(settings, work) as sandbox:
        ran = run_solution(sandbox, SCRIPT, [], TIMEOUT_S)
    if ran.result.timed_out:
        return "its script ran out of time"
    if ran.result.exit_code != 0:
        return "its script stopped with an error"
    reason = check_edit(target, work / name, outcomes)
    if reason:
        return reason
    shutil.copyfile(work / name, target)
    return None


# -- the agent ---------------------------------------------------------------------


def _message(request: str, fmt: str, name: str, path: Path) -> str:
    return (
        f"The student asked for this change to {FORMAT_NAMES.get(fmt, fmt)}:\n{request}\n\n"
        f"The file is {name} in your working directory. Its outline:\n{outline(path)}\n\n"
        f"Write {SCRIPT}, run it, and record the result."
    )


def _cost(usd: float) -> str:
    amount = credits.exact(usd)
    if amount < 0.5:
        return "under 1 credit"
    whole = round(amount)
    return f"about {whole} credit{'s' if whole != 1 else ''}"


def run_edit(context, request: str, produced, outcomes, usage, settings, *, pdf_too=False) -> EditOutcome:
    """Make one look change the report spec has no field for. Never raises."""
    available = targets(produced)
    fmt = _target_for(request, available)
    if fmt is None:
        return EditOutcome(
            "nothing",
            "There's no Word report, web page, notebook or text copy to change, so I couldn't "
            f"{request.rstrip('.')}. Ask for one of those files first.",
        )
    target = available[fmt]
    name = f"report{target.suffix.lower()}"
    store = context.store
    work = _fresh(store.workspace / WORK)
    shutil.copyfile(target, work / name)

    spent_before = usage.total.cost_usd
    recorder = None
    try:
        with make_sandbox(settings, work) as sandbox:
            agent, recorder = build_solver(
                sandbox, settings, model=build_model(settings), system_prompt=EDITOR_PROMPT
            )
            live = LiveUsage(
                usage.phase("edit"),
                run_usage=usage,
                task_cap=settings.max_cost_per_edit_usd,
                run_cap=settings.max_cost_per_run_usd,
                task_start=spent_before,
            )
            agent.invoke(
                {"messages": [{"role": "user", "content": _message(request, fmt, name, work / name)}]},
                config=solver_config(settings, callbacks=[live], max_turns=settings.edit_max_turns),
            )
    except (GraphRecursionError, BudgetExceeded):
        pass  # judged below by what it left behind, like a solver attempt
    except SandboxError:
        return EditOutcome("failed", "The program runner wasn't available, so I couldn't make that change. Try again in a minute.")
    except Exception:  # noqa: BLE001 -- a decoration never undoes a finished lab
        pass
    cost = _cost(usage.total.cost_usd - spent_before)

    if recorder is not None and recorder.status == "blocked":
        why = (recorder.missing or "it can't be done inside the file").rstrip(".")
        return EditOutcome("blocked", f"I couldn't make that change: {why} ({cost}).")
    script = work / SCRIPT
    if recorder is None or recorder.status == "failed" or not script.exists():
        return EditOutcome(
            "failed",
            f"I tried to {request.rstrip('.')}, but couldn't get it to work, so the files are as "
            f"they were ({cost}).",
        )

    source = script.read_text(encoding="utf-8")
    try:
        reason = apply_script(settings, store, source, target, outcomes)
    except SandboxError:
        reason = "the program runner wasn't available"
    if reason:
        return EditOutcome(
            "rejected",
            f"I made that change, but {reason}, so I threw it away and kept the files as they "
            f"were ({cost}).",
        )

    existing = list(getattr(context.presentation, "edits", ()) or ())
    number = len(existing) + 1
    saved = store.dir / "edits" / f"{number:02d}.py"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(source, encoding="utf-8")
    note = f"Changed {FORMAT_NAMES.get(fmt, fmt)}: {request.rstrip('.')} ({cost})."
    if pdf_too:
        note += " The PDF is drawn from the content, so it doesn't show this change."
    return EditOutcome(
        "applied",
        note,
        edit={
            "n": number,
            "request": request,
            "target": fmt,
            "script": saved.relative_to(store.dir).as_posix(),
            "status": "applied",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        target=target,
    )


def replay_edits(context, produced, outcomes, settings) -> tuple[tuple[dict, ...], list[str], list[Path]]:
    """Re-apply every saved edit, in order, to the files just rebuilt.

    Returns the edits with their new status, sentences for the chat, and the
    files that changed. No model call. An edit whose file was not produced
    this time is kept for when it is; one that no longer fits is marked stale
    and said once; a runner outage leaves it applied, to be tried next time.
    """
    available = targets(produced)
    edits: list[dict] = []
    notes: list[str] = []
    touched: list[Path] = []
    for raw in getattr(context.presentation, "edits", ()) or ():
        edit = dict(raw)
        target = available.get(edit.get("target", ""))
        if edit.get("status") != "applied" or target is None:
            edits.append(edit)
            continue
        script = context.store.dir / str(edit.get("script", ""))
        try:
            reason = (
                apply_script(settings, context.store, script.read_text(encoding="utf-8"), target, outcomes)
                if script.is_file()
                else "its saved script is missing"
            )
        except SandboxError:
            notes.append(
                f"I couldn't re-apply your earlier change “{edit.get('request', '')}” this "
                "time; it will be tried again next time the files are rebuilt."
            )
            edits.append(edit)
            continue
        if reason:
            edit["status"] = "stale"
            edit["reason"] = reason
            notes.append(
                f"Your earlier change “{edit.get('request', '')}” no longer fits after "
                f"this rebuild ({reason}), so it's left out. Ask again to redo it."
            )
        elif target not in touched:
            touched.append(target)
        edits.append(edit)
    return tuple(edits), notes, touched
