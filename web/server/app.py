"""HTTP surface: upload, stream, answer, download.

THE SHAPE OF THE CONCURRENCY, because it is the thing most likely to be got
wrong. The pipeline is synchronous and blocking -- it runs subprocesses and
waits on HTTP calls to a model. FastAPI is async. Calling the pipeline directly
from an async route would block the event loop, and the visible symptom would
be that the SSE stream *freezes during exactly the work it exists to report*.

So the pipeline runs on its own thread, and the two sides meet at `Job`:

    POST /api/runs  ->  thread(run_job) -> job.publish(...)  [worker]
                                            |
    GET  .../events ->  StreamingResponse <- job.since(cursor)  [HTTP thread]

The SSE generator is deliberately a SYNC generator. Starlette iterates sync
generators in a threadpool, so the blocking `Condition.wait()` inside
`job.since` parks a pool thread instead of the event loop -- which is exactly
the right trade at this scale. The async alternative (asyncio.Queue plus
`loop.call_soon_threadsafe`) is more correct on paper and roughly twice the
code, and it buys nothing when the ceiling is one user on localhost.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from labsagent.config import PROJECT_ROOT, load_settings

from . import history
from .jobs import HEARTBEAT_S, JobRegistry
from labsagent.data import DATA_SUFFIXES
from labsagent.ingest.readers import ACCEPTED_SUFFIXES

from .pipeline import UPLOADS_ROOT, repackage_job, revise_job, run_job

# The upload ceiling. A lab manual is tens of kilobytes; this exists to turn a
# wrong-file-selected mistake into a fast error rather than a slow one.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Data gets its own, larger ceiling, because the two are different kinds of
# mistake. A 30 MB "manual" is the wrong file; a 30 MB CSV is Tuesday. The real
# cap lives in Settings, so the eval and the server cannot disagree about it.
MAX_DATA_BYTES = load_settings().max_dataset_bytes

# At most this many data files in one run. The same number bounds the upload
# handler, the prompt block and the per-task copy.
MAX_DATA_FILES = 8

# A .docx is a ZIP container, so it starts with the ZIP local file header.
# Checking this and not just the extension is the difference between validating
# a file and validating a filename -- an .exe renamed to .docx passes the
# second check and fails the first.
#
# This is still applied, but only to .docx. It used to be the gate for EVERY
# upload, which had it backwards in both directions: a PDF or a notebook lab
# was refused for not being a ZIP, while any .xlsx, .pptx or renamed .zip
# sailed through and failed deeper in. The extension now picks the reader, and
# the magic bytes verify the one format whose container they describe.
ZIP_MAGIC = b"PK"

app = FastAPI(title="Labs-Agent", docs_url="/api/docs", openapi_url="/api/openapi.json")
registry = JobRegistry()

UI_DIST = PROJECT_ROOT / "web" / "ui" / "dist"


class Answers(BaseModel):
    answers: dict[str, str]


class Feedback(BaseModel):
    feedback: str


class Identity(BaseModel):
    name: str = ""
    cms_id: str = ""
    section: str = ""
    program: str = ""


# --------------------------------------------------------------------- meta


@app.get("/api/health")
def health() -> dict:
    settings = load_settings()
    return {
        "ok": True,
        "model": settings.model_name,
        "api_key_configured": settings.configured,
        "ui_built": UI_DIST.is_dir(),
    }


# There is deliberately no GET /api/profile. Identity lives in the browser
# (see `pipeline.profile_from`), so the server has nothing to remember and
# nothing to hand back.


# --------------------------------------------------------------------- runs


async def _save_datasets(uploads, upload_dir: Path) -> list[Path]:
    """Validate and store attached data files. Returns where they landed.

    Checked by SUFFIX and SIZE only. The .docx gets its magic bytes verified
    because it claims to be a ZIP container and either is one or is not; a CSV
    has no signature to check. Its real validation is `data.preview`, which
    either parses the file into a table or says plainly that it could not.
    """
    saved: list[Path] = []
    real = [u for u in (uploads or []) if u is not None and (u.filename or "").strip()]
    if not real:
        return saved

    if len(real) > MAX_DATA_FILES:
        raise HTTPException(
            413,
            f"That is {len(real)} data files; I take at most {MAX_DATA_FILES}. "
            "Attach the ones the lab actually uses, or zip them together.",
        )

    for upload in real:
        # Path(...).name strips any directory component the client sent, so a
        # filename like "../../.env" lands as ".env" inside the job's own folder.
        filename = Path(upload.filename or "data.csv").name
        suffix = Path(filename).suffix.lower()
        if suffix not in DATA_SUFFIXES:
            raise HTTPException(
                415,
                f"I cannot read {filename} as data. Data files can be: "
                f"{', '.join(DATA_SUFFIXES)}.",
            )
        blob = await upload.read()
        if len(blob) > MAX_DATA_BYTES:
            raise HTTPException(
                413, f"{filename} is larger than {MAX_DATA_BYTES // 1024 // 1024} MB."
            )
        target = upload_dir / filename
        target.write_bytes(blob)
        saved.append(target)

    return saved


@app.post("/api/runs", status_code=202)
async def create_run(
    manual: UploadFile | None = File(None),
    datasets: list[UploadFile] | None = File(None),
    instructions: str = Form(""),
    name: str = Form(""),
    cms_id: str = Form(""),
    section: str = Form(""),
    program: str = Form(""),
) -> dict:
    # NO FILE IS A VALID RUN. The lab can simply be typed or pasted into the
    # composer, in which case the message is the document.
    has_manual = manual is not None and bool((manual.filename or "").strip())
    if not has_manual and not instructions.strip():
        raise HTTPException(
            422,
            "Nothing to work from. Attach a lab file, or paste the tasks into "
            "the message and I will read them from there.",
        )

    manual_bytes = b""
    manual_name = ""
    if has_manual:
        manual_bytes = await manual.read()
        if len(manual_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"File is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
            )

        # A LOCAL NAME, not the `name` form field. This used to assign straight
        # over it -- so any run started with a file attached filed its report
        # under "lab03_manual.docx" instead of under whoever sent it, and the
        # browser's remembered identity was silently discarded.
        manual_name = Path(manual.filename or "manual.docx").name
        suffix = Path(manual_name).suffix.lower()
        if suffix not in ACCEPTED_SUFFIXES:
            raise HTTPException(
                415,
                f"I cannot read {suffix or 'a file with no extension'}. "
                f"Try one of: {', '.join(ACCEPTED_SUFFIXES)} -- or paste the tasks "
                "into the message instead.",
            )
        if suffix == ".docx" and not manual_bytes.startswith(ZIP_MAGIC):
            raise HTTPException(
                415,
                "That is named .docx but is not one. Word documents are ZIP "
                "containers and this file does not start like one -- if it is a "
                ".doc, open it in Word and save as .docx.",
            )

    job = registry.create()

    # Made unconditionally now: data can arrive with a pasted lab and no file,
    # and it still needs somewhere of its own to land.
    upload_dir = UPLOADS_ROOT / job.id
    upload_dir.mkdir(parents=True, exist_ok=True)

    manual_path = None
    if has_manual:
        manual_path = upload_dir / manual_name
        manual_path.write_bytes(manual_bytes)

    dataset_paths = await _save_datasets(datasets, upload_dir)

    job.publish(
        {
            "type": "accepted",
            "filename": manual_path.name if manual_path else None,
            "bytes": len(manual_bytes),
            "datasets": [p.name for p in dataset_paths],
        }
    )

    threading.Thread(
        target=run_job,
        kwargs={
            "job": job,
            "manual_path": manual_path,
            "dataset_paths": dataset_paths,
            "instructions": instructions,
            "profile_seed": {
                "name": name.strip(),
                "cms_id": cms_id.strip(),
                "section": section.strip(),
                "program": program.strip(),
            },
            "settings": load_settings(),
        },
        name=f"labsagent-{job.id}",
        daemon=True,  # a stuck job must never keep the process alive
    ).start()

    return {"job_id": job.id}


@app.get("/api/runs/{job_id}")
def run_status(job_id: str) -> dict:
    """Polling fallback, and the thing to curl when the stream looks wrong."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return {"job_id": job.id, "status": job.status, "error": job.error, "summary": job.summary}


def _sse(job, cursor: int):
    """Sync generator: one SSE frame per event, heartbeat when idle.

    Framing, line by line:

        id: 42            the browser stores this and sends it back as
                          `Last-Event-ID` when EventSource reconnects on its own
        data: {...}       one JSON payload; newline-free so one data line suffices
        <blank line>      ends the frame -- omit it and the client buffers forever

    The `: ping` is a comment frame. It carries no data and exists so that a
    connection which has silently died is noticed by a write failing, rather
    than by the job mysteriously never finishing.
    """
    try:
        while True:
            events, cursor, finished = job.since(cursor, timeout_s=HEARTBEAT_S)
            for payload in events:
                yield f"id: {payload['seq']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if finished:
                return
            if not events:
                yield ": ping\n\n"
    except GeneratorExit:  # the client went away; nothing to clean up
        return


@app.get("/api/runs/{job_id}/events")
def stream_events(
    job_id: str,
    last_event_id: str | None = Header(default=None),
):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")

    # A reconnecting EventSource replays from where it left off, so a dropped
    # connection or a page refresh costs nothing. This is the whole reason the
    # job keeps a log instead of a queue.
    #
    # The +0 is not a no-op to reason about: `seq` is 1-based and the log is
    # sliced by 0-based index, so "I have seen seq N" means "start at index N".
    # Off by one here re-sends one event on every reconnect -- harmless with an
    # idempotent reducer, and a duplicated finish event with a naive one.
    try:
        cursor = max(0, int(last_event_id)) if last_event_id else 0
    except ValueError:
        cursor = 0

    return StreamingResponse(
        _sse(job, cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx not to buffer. Irrelevant behind uvicorn directly,
            # and the first thing to check if this is ever put behind a proxy.
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs/{job_id}/answers")
def submit_answers(job_id: str, body: Answers) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if not job.submit(body.answers):
        # Not waiting: either already answered (a double submit) or it timed
        # out and moved on. Both are normal, and neither should look like a
        # server error to the user.
        raise HTTPException(409, "this run is not waiting for input")
    return {"ok": True}


@app.post("/api/runs/{job_id}/revise", status_code=202)
def revise_run(job_id: str, body: Feedback) -> dict:
    """Redo part of a finished run, in place.

    Runs on the same `Job` rather than a new one. That is the point: the chat
    shows one report that improved, not two that disagree, and the artifacts
    the revision replaces are replaced where the browser already looks for
    them.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if not job.is_terminal():
        raise HTTPException(409, "this run is still working")
    if not body.feedback.strip():
        raise HTTPException(422, "feedback is empty")

    # Reopen the event stream's producer. `finish()` set a terminal status, so
    # the job has to go back to running or `_sse` would end the response the
    # moment it starts -- and the browser would see the revision's events
    # arrive after a stream that had already closed.
    job.reopen()
    threading.Thread(
        target=revise_job,
        args=(job, body.feedback),
        name=f"labsagent-revise-{job.id}",
        daemon=True,
    ).start()
    return {"job_id": job.id, "revising": True}


@app.post("/api/runs/{job_id}/identity", status_code=202)
def set_identity(job_id: str, body: Identity) -> dict:
    """Put the student's details on the files -- a rebuild, no model call.

    Identity used to be a REQUIRED question before any code was written, though
    it only ever reached the cover and the filenames. It is now offered after
    the files exist, and this route rebuilds them with it.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if not job.is_terminal():
        raise HTTPException(409, "this run is still working")
    values = {k: v.strip() for k, v in body.model_dump().items() if v and v.strip()}
    if not values:
        raise HTTPException(422, "nothing to add")
    job.reopen()
    threading.Thread(
        target=repackage_job,
        args=(job, values),
        name=f"labsagent-identity-{job.id}",
        daemon=True,
    ).start()
    return {"job_id": job.id, "repackaging": True}


@app.get("/api/runs/{job_id}/download/{key}")
def download(job_id: str, key: str) -> FileResponse:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")

    # A dict lookup, not a path join. `key` can be anything the client likes --
    # "../../.env" simply is not a key in this dict.
    path = job.artifacts.get(key)
    if path is None or not path.exists():
        raise HTTPException(404, "no such artifact")

    return FileResponse(path, filename=path.name)


# ------------------------------------------------------------------ history
#
# Past runs. Same whitelist discipline as the live ones -- the difference is
# that the whitelist is rebuilt by scanning the run directory rather than held
# in memory, because the process that produced it is long gone. See history.py.


@app.get("/api/history")
def history_list(limit: int = 12) -> dict:
    return {"runs": history.list_runs(limit=max(1, min(limit, 100)))}


@app.get("/api/history/{run_id}")
def history_detail(run_id: str) -> dict:
    directory = history.run_dir(run_id)
    if directory is None:
        raise HTTPException(404, "no such run")
    # `path` is dropped from the response on purpose: the browser asks by key,
    # and an on-disk path in a JSON body is an invitation to try another one.
    return {
        **history.summarise(directory),
        "artifacts": [
            {k: v for k, v in a.items() if k != "path"}
            for a in history.artifacts_for(directory)
        ],
    }


@app.get("/api/history/{run_id}/download/{key}")
def history_download(run_id: str, key: str) -> FileResponse:
    directory = history.run_dir(run_id)
    if directory is None:
        raise HTTPException(404, "no such run")
    # Same dict lookup as the live route. An unknown key simply has no entry.
    for artifact in history.artifacts_for(directory):
        if artifact["key"] == key:
            return FileResponse(artifact["path"], filename=artifact["filename"])
    raise HTTPException(404, "no such artifact")


# Mounted last so it cannot shadow /api. Present only after `npm run build`;
# during development the Vite dev server serves the UI and proxies /api here.
if UI_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(UI_DIST), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
