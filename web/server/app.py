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
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from labsagent.config import PROJECT_ROOT, load_settings

from . import auth, history, metering, ownership, profiles, supa
from .jobs import HEARTBEAT_S, JobRegistry
from labsagent.data import DATA_SUFFIXES
from labsagent.ingest.readers import ACCEPTED_SUFFIXES

from .pipeline import UPLOADS_ROOT, repackage_job, revise_job, run_job
from .slots import SOLVE_SLOTS

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
# A no-op unless LABSAGENT_USER and LABSAGENT_PASSWORD are both set. See auth.py.
app.add_middleware(auth.RequireLogin)
registry = JobRegistry()


@app.on_event("startup")
def _start_retention() -> None:
    from . import retention

    settings = load_settings()
    retention.start(settings.runs_dir.resolve(), settings.retention_days)
    # Holds left by runs that died with the last process: release them.
    metering.release_stale_holds()

UI_DIST = PROJECT_ROOT / "web" / "ui" / "dist"


class Answers(BaseModel):
    answers: dict[str, str]


class Feedback(BaseModel):
    feedback: str


class Login(BaseModel):
    username: str = ""
    password: str = ""


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
        # How busy the solve queue is: what to curl when labs seem slow.
        "solves": SOLVE_SLOTS.stats(),
    }


# --------------------------------------------------------------------- sign-in


class Exchange(BaseModel):
    access_token: str


def _set_session(response: Response, request: Request, user_id: str) -> None:
    response.set_cookie(
        auth.COOKIE,
        auth.mint(user_id),
        max_age=auth.SESSION_S,
        # Page scripts cannot read it, so an XSS bug cannot lift it.
        httponly=True,
        samesite="lax",
        # https behind Caddy (uvicorn trusts its X-Forwarded-Proto), plain http
        # on localhost -- where a Secure cookie would never be sent back.
        secure=request.url.scheme == "https",
        path="/",
    )


@app.get("/api/session")
def session(request: Request) -> dict:
    mode = auth.mode()
    answer = {
        "mode": mode,
        "required": mode != "open",
        "signed_in": request.state.user_id is not None,
    }
    if mode == "supabase":
        # Both public by design: the browser needs them to start a Google
        # sign-in. The service key never appears in any response.
        answer["supabase"] = {"url": supa.settings.base, "anon_key": supa.settings.anon_key}
    return answer


@app.post("/api/session")
def exchange(body: Exchange, request: Request, response: Response) -> dict:
    """Trade a Supabase access token for our own session cookie. Once."""
    if auth.mode() != "supabase":
        raise HTTPException(404, "Google sign-in is not set up on this server.")
    try:
        claims = supa.verify(body.access_token)
    except supa.BadToken:
        raise HTTPException(401, "That sign-in did not check out. Try again.") from None
    meta = claims.get("user_metadata") or {}
    try:
        supa.rpc(
            "ensure_user",
            p_user_id=claims["sub"],
            p_email=str(claims.get("email") or "")[:320],
            p_name=str(meta.get("full_name") or meta.get("name") or "")[:120],
        )
    except supa.SupaError:
        raise HTTPException(503, "Can’t reach your account right now. Try again in a minute.") from None
    _set_session(response, request, claims["sub"])
    return {"signed_in": True}


# `def`, not `async def`: FastAPI runs a sync route on a worker thread, so the
# failure delay parks that thread and never the event loop every SSE stream
# shares.
@app.post("/api/login")
def login(body: Login, request: Request, response: Response) -> dict:
    if auth.mode() == "supabase":
        raise HTTPException(404, "Sign in with Google instead.")
    if auth.mode() == "open":
        return {"signed_in": True}
    if not auth.credentials_match(body.username, body.password):
        time.sleep(auth.FAILURE_DELAY_S)
        raise HTTPException(401, "That username and password do not match.")
    _set_session(response, request, auth.OWNER_USER)
    return {"signed_in": True}


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"signed_in": False}


# --------------------------------------------------------------------- you


@app.get("/api/me")
def me(request: Request) -> dict:
    """Who you are, and (with accounts) how many credits you have left."""
    return {
        "user_id": request.state.user_id,
        "mode": auth.mode(),
        "credits": metering.balance(request.state.user_id, load_settings()),
    }


@app.get("/api/profile")
def get_profile(request: Request) -> dict:
    """Your name and CMS ID, for the cover page. Kept with your account."""
    return profiles.get(request.state.user_id)


@app.put("/api/profile")
def put_profile(body: Identity, request: Request) -> dict:
    return profiles.save(request.state.user_id, body.model_dump())


# --------------------------------------------------------------------- runs


async def _save_capped(upload: UploadFile, target: Path, limit: int) -> int:
    """Copy an upload to disk in chunks, stopping the moment it passes `limit`.

    `await upload.read()` used to pull the whole file into memory BEFORE the
    size check (audit finding 4) -- so the check guarded the disk but not the
    1 GB server's memory. Starlette has already spooled the body to a temp
    file; this copies it out 1 MB at a time. Returns the bytes written.
    """
    written = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        413,
                        f"{target.name} is larger than {limit // 1024 // 1024} MB.",
                    )
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    return written


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
        target = upload_dir / filename
        await _save_capped(upload, target, MAX_DATA_BYTES)
        saved.append(target)

    return saved


@app.post("/api/runs", status_code=202)
async def create_run(
    request: Request,
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
        # At most one byte past the cap: enough to know it is too big without
        # pulling a 120 MB body into memory to find out.
        manual_bytes = await manual.read(MAX_UPLOAD_BYTES + 1)
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

    user_id = request.state.user_id
    # ONE LAB AT A TIME PER ACCOUNT, with public sign-up. Otherwise one person
    # can fill the solve queue for everyone else.
    if auth.mode() == "supabase" and registry.active_for(user_id):
        raise HTTPException(
            409, "You already have a lab running. Start the next one when it finishes."
        )
    job = registry.create()
    hold = metering.UNMETERED
    try:
        ownership.record(job, user_id)

        # Made unconditionally now: data can arrive with a pasted lab and no
        # file, and it still needs somewhere of its own to land.
        upload_dir = UPLOADS_ROOT / job.id
        upload_dir.mkdir(parents=True, exist_ok=True)

        manual_path = None
        if has_manual:
            manual_path = upload_dir / manual_name
            manual_path.write_bytes(manual_bytes)

        dataset_paths = await _save_datasets(datasets, upload_dir)

        # LAST, after every check that can refuse the upload, so a refused
        # file never holds credits. A refusal here is an HTTP answer (402,
        # 503) before any work starts, not an event in a stream.
        settings = load_settings()
        hold = metering.reserve(user_id, job.id, "run", settings)
    except Exception as exc:
        # Never leave a job "queued" forever: it would count as this
        # account's running lab and block the next one.
        job.finish(error=str(getattr(exc, "detail", exc))[:300])
        metering.settle(hold, metering.RunUsage(), free=True)
        raise

    job.publish(
        {
            "type": "accepted",
            "filename": manual_path.name if manual_path else None,
            "bytes": len(manual_bytes),
            "datasets": [p.name for p in dataset_paths],
        }
    )

    threading.Thread(
        target=metering.run_metered,
        args=(run_job, hold),
        kwargs={
            # Free if it stops before solving (not a lab, out of scope, no data).
            "free_unless_solved": True,
            "job": job,
            "manual_path": manual_path,
            "dataset_paths": dataset_paths,
            "instructions": instructions,
            # Who you are comes from your account. Anything typed into this
            # request wins for this run only (older pages still send it).
            "profile_seed": {
                **profiles.get(user_id),
                **profiles.clean(
                    {"name": name, "cms_id": cms_id, "section": section, "program": program}
                ),
            },
            # Its cost ceiling is what its credit hold can pay for.
            "settings": metering.capped(settings, hold),
        },
        name=f"labsagent-{job.id}",
        daemon=True,  # a stuck job must never keep the process alive
    ).start()

    return {"job_id": job.id}


@app.get("/api/runs/{job_id}")
def run_status(job_id: str, request: Request) -> dict:
    """Polling fallback, and the thing to curl when the stream looks wrong."""
    job = ownership.require_job(registry, job_id, request.state.user_id)
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
    request: Request,
    last_event_id: str | None = Header(default=None),
):
    job = ownership.require_job(registry, job_id, request.state.user_id)

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
def submit_answers(job_id: str, body: Answers, request: Request) -> dict:
    job = ownership.require_job(registry, job_id, request.state.user_id)
    if not job.submit(body.answers):
        # Not waiting: either already answered (a double submit) or it timed
        # out and moved on. Both are normal, and neither should look like a
        # server error to the user.
        raise HTTPException(409, "this run is not waiting for input")
    return {"ok": True}


@app.post("/api/runs/{job_id}/revise", status_code=202)
def revise_run(job_id: str, body: Feedback, request: Request) -> dict:
    """Redo part of a finished run, in place.

    Runs on the same `Job` rather than a new one. That is the point: the chat
    shows one report that improved, not two that disagree, and the artifacts
    the revision replaces are replaced where the browser already looks for
    them.
    """
    job = ownership.require_job(registry, job_id, request.state.user_id)
    if not job.is_terminal():
        raise HTTPException(409, "this run is still working")
    if not body.feedback.strip():
        raise HTTPException(422, "feedback is empty")
    # A follow-up is paid for like a run: even a question is a model call.
    hold = metering.reserve(request.state.user_id, job.id, "followup", load_settings())

    # Reopen the event stream's producer. `finish()` set a terminal status, so
    # the job has to go back to running or `_sse` would end the response the
    # moment it starts -- and the browser would see the revision's events
    # arrive after a stream that had already closed.
    job.reopen()
    threading.Thread(
        target=metering.run_metered,
        args=(revise_job, hold),
        kwargs={
            "free_unless_solved": False,
            "job": job,
            "feedback": body.feedback,
            "cap_usd": metering.capped(load_settings(), hold).max_cost_per_run_usd
            if hold.id
            else None,
        },
        name=f"labsagent-revise-{job.id}",
        daemon=True,
    ).start()
    return {"job_id": job.id, "revising": True}


@app.post("/api/runs/{job_id}/identity", status_code=202)
def set_identity(job_id: str, body: Identity, request: Request) -> dict:
    """Put the student's details on the files -- a rebuild, no model call.

    Identity used to be a REQUIRED question before any code was written, though
    it only ever reached the cover and the filenames. It is now offered after
    the files exist, and this route rebuilds them with it.
    """
    job = ownership.require_job(registry, job_id, request.state.user_id)
    if not job.is_terminal():
        raise HTTPException(409, "this run is still working")
    values = profiles.clean(body.model_dump())
    if not values:
        raise HTTPException(422, "nothing to add")
    # Remembered with your account, so the next lab's cover is right first time.
    profiles.save(request.state.user_id, values)
    job.reopen()
    threading.Thread(
        target=repackage_job,
        args=(job, values),
        name=f"labsagent-identity-{job.id}",
        daemon=True,
    ).start()
    return {"job_id": job.id, "repackaging": True}


@app.get("/api/runs/{job_id}/download/{key}")
def download(job_id: str, key: str, request: Request) -> FileResponse:
    job = ownership.require_job(registry, job_id, request.state.user_id)

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
def history_list(request: Request, limit: int = 12) -> dict:
    limit = max(1, min(limit, 100))
    mine = ownership.run_ids_for(request.state.user_id, limit)
    return {"runs": history.list_runs(limit=limit, only=mine)}


@app.get("/api/history/{run_id}")
def history_detail(run_id: str, request: Request) -> dict:
    directory = ownership.require_run(run_id, request.state.user_id)
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
def history_download(run_id: str, key: str, request: Request) -> FileResponse:
    directory = ownership.require_run(run_id, request.state.user_id)
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
