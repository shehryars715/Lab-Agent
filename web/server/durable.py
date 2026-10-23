"""Labs that survive the server: whose a job is after a restart, and what
happens to the work a restart cut short.

WHAT A RESTART USED TO DO. Jobs lived in memory, so a deploy or a crash
ended every running lab, emptied every chat, and released the credit holds
with nothing said. The files a lab had finished were safe on disk; the
conversation and the work in flight were not.

THREE KINDS OF STATE, and only one of them used to survive:

    the conversation   Job._log                 -> events.jsonl (jobs.py)
    the work done      manifest.json, per task  -> already on disk
    how to go on       RunContext               -> saved BEFORE the solve now

The third is the subtle one. A checkpoint that holds the results but not
the inputs that produced them cannot be continued: tasks 1-3 are on disk,
and a new process has no idea what the student asked for. Same lesson as
`manifest.anchors`, which existed for months with nothing writing it.

THE RULE (owner's decision, 2026-10-23): AUTO-RESUME ONCE, THEN A BUTTON.
Anything a restart interrupts -- a lab being read, a lab being solved, a
change, a rebuild with the student's details -- is started again once by
itself, as soon as the server is back. If the SAME work is interrupted a
second time, it waits for Resume. The count is per piece of work, not per
lab: twice in a row at the same point suggests the work itself is what
brought the server down (a "poison pill"), and redoing it on every start is
a crash loop.

  REJECTED  Always automatic: the crash loop above.
  REJECTED  Always a button: a student who walked away comes back to half a
            lab, and a deploy is never their fault.
  REJECTED  Waiting for running labs before a deploy (a drain): deploys of
            ten minutes, and crashes still need all of this anyway.

WHAT IT COSTS THE STUDENT. The interrupted part is never charged: its hold
is released at startup (metering.release_stale_holds), and the usage it
measured died with the process. Starting again takes a fresh hold and pays
only for what is done from then on. A credit refusal ("you've used this
month's credits") does not lose the lab: it waits for Resume, with that
sentence on screen.

REDOING IS NOT ALWAYS SAFE. A first run is: reading starts from the files,
and a solve continues from the manifest (`run_lab(resume=True)` skips the
settled tasks). A change is NOT: a code change rewrites the task's
instruction and drops its outcome before solving, so running it twice would
apply it twice. So a change snapshots the manifest before it starts, and
running it again restores the snapshot first -- back to the checkpoint,
then forward. Undo-then-redo is what makes a non-idempotent step safe to
repeat.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from labsagent.config import load_settings
from labsagent.runstore import MANIFEST_NAME, RunStore
from labsagent.usage import RunUsage

from . import auth, metering, ownership, profiles
from .jobs import Job, JobRegistry
from .pipeline import RunContext, profile_from, repackage_job, resume_job, revise_job, run_job

#: How many times work is started again without anyone pressing a button.
AUTO_TRIES = 1
#: The manifest as it was before a change, in the job's own folder.
SNAPSHOT = "manifest.before-change.json"

TWICE = (
    "It was interrupted twice at the same point, so I've stopped rather than "
    "try a third time on my own."
)

# One resume at a time: a double-clicked button must not start two workers
# writing one run folder. Resumes are rare; serialising them costs nothing.
_RESUMING = threading.Lock()


# ------------------------------------------------------------------ who


def owner_of(job_id: str) -> str | None:
    """Whose job this is, for a job this process did not start.

    From the database, never from the job's folder: the disk is data, not
    authority (ownership.py). One call per job per process; the answer is
    kept on the Job.
    """
    mode = auth.mode()
    if mode == "supabase":
        return ownership.job_owner(job_id)
    if mode == "open":
        return auth.LOCAL_USER
    return None


# ------------------------------------------------------------------ what to redo


def checkpoint(job: Job) -> RunStore | None:
    """The run folder, if solving had started: a manifest with a context.

    `run_job` writes one just before the solve (and again at the end), so its
    presence is exactly "the questions are answered and the data is here".
    """
    if job.run_dir is None or not (job.run_dir / MANIFEST_NAME).is_file():
        return None
    try:
        store = RunStore.open(job.run_dir)
        return store if store.load().context else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _stage(job: Job, work: dict) -> tuple[str, int, int]:
    """(stage, tasks done, tasks in all) of the work a restart cut short."""
    kind = work.get("kind")
    if kind == "followup":
        return "change", 0, 0
    if kind == "identity":
        return "details", 0, 0
    store = checkpoint(job)
    if store is None:
        return "reading", 0, 0
    manifest = store.load()
    return "solving", len(manifest.outcomes), len(manifest.spec.tasks)


def _said(stage: str, done: int, total: int, *, resuming: bool) -> str:
    """What happened and what happens now, in the chat's own voice."""
    free = " The interrupted part isn't charged."
    if stage == "reading":
        what = "The server restarted before I'd written any code"
        return what + (", so I'm reading your lab again from the start." if resuming else ".") + (
            " Nothing was charged."
        )
    if stage == "solving":
        if done >= total:
            what = "The server restarted while I was making your files"
            return what + (", so I'm making them again." if resuming else ".")
        if done == 0:
            what = "The server restarted as I began the tasks"
            return what + (", so I'm starting them again." if resuming else ".") + free
        kept = f"The {done} finished {'one is' if done == 1 else 'ones are'} kept"
        what = f"The server restarted during task {done + 1} of {total}. {kept}"
        return what + (", and I'm carrying on from there." if resuming else ".") + free
    if stage == "change":
        what = "The server restarted while I was making your change"
        return what + (", so I'm making it again." if resuming else ".") + free
    what = "The server restarted while I was adding your details"
    return what + (", so I'm adding them again." if resuming else ".")


def _carrying_on(stage: str, done: int, total: int) -> str:
    if stage == "reading":
        return "Reading your lab again from the start."
    if stage == "solving":
        if done >= total:
            return "Making your files again."
        return f"Carrying on from task {done + 1}." if done else "Starting the tasks again."
    if stage == "change":
        return "Making your change again."
    return "Adding your details again."


# ------------------------------------------------------------------ doing it


def run_kwargs(job: Job, work: dict, upload_dir: Path, settings) -> dict[str, Any]:
    """`run_job`'s arguments, from what `job.json` says was sent.

    The same function builds them for a first run and for one started again,
    so a resumed lab is read exactly as a fresh one is.
    """
    manual = work.get("manual")
    return {
        "manual_path": upload_dir / manual if manual else None,
        "dataset_paths": [upload_dir / name for name in work.get("datasets") or []],
        "instructions": work.get("instructions") or "",
        # Who you are comes from your account. Anything typed into the
        # request wins for this run only (older pages still send it).
        "profile_seed": {**profiles.get(job.owner), **(work.get("identity") or {})},
        "settings": settings,
    }


def ensure_context(job: Job) -> None:
    """A job read back from disk has no context in memory: rebuild it from
    the run folder, as `/reopen` does, before a change or a rebuild."""
    if getattr(job, "context", None) is not None:
        return
    if job.run_dir is None:
        raise HTTPException(
            409, "This lab never got as far as its files, so it can't take changes. Start it again."
        )
    try:
        store = RunStore.open(job.run_dir)
        job.context = RunContext.from_disk(store, load_settings(), profile_from(profiles.get(job.owner)))
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        raise HTTPException(
            409, "This lab's record is incomplete, so it can't take changes. Start it again."
        ) from exc


def _snapshot(job: Job, *, restore: bool) -> None:
    """Before a change: keep the manifest. Running it again: put it back."""
    if job.folder is None or job.run_dir is None:
        return
    saved = job.folder / SNAPSHOT
    live = job.run_dir / MANIFEST_NAME
    try:
        if restore:
            if saved.is_file():
                shutil.copyfile(saved, live)
        elif live.is_file():
            job.folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(live, saved)
    except OSError as exc:
        print(f"durable: snapshot of {job.id} failed: {exc}", flush=True)


def _followup(job: Job, hold: metering.Hold, feedback: str) -> None:
    try:
        metering.run_metered(
            revise_job,
            hold,
            free_unless_solved=False,
            job=job,
            feedback=feedback,
            cap_usd=metering.capped(load_settings(), hold).max_cost_per_run_usd if hold.id else None,
        )
    finally:
        # Finished, or failed in its own words: either way not interrupted,
        # so there is nothing to restore. (A dead process never gets here.)
        if job.folder is not None:
            (job.folder / SNAPSHOT).unlink(missing_ok=True)


def launch(job: Job, hold: metering.Hold, *, upload_dir: Path | None = None) -> None:
    """Start the work `job.json` records, on its own thread.

    One door for a change, a rebuild and every resume. (A first run is
    started by the upload route itself, with the same `run_kwargs`.)
    """
    work = job.state.get("work") or {}
    kind = work.get("kind")
    settings = load_settings()
    tries = int(work.get("tries", 0))

    if kind == "run":
        capped = metering.capped(settings, hold)
        if checkpoint(job) is not None:
            target, kwargs = resume_job, {
                "job": job,
                "settings": capped,
                "profile_seed": {**profiles.get(job.owner), **(work.get("identity") or {})},
            }
        else:
            folder = upload_dir or job.folder
            target, kwargs = run_job, {"job": job, **run_kwargs(job, work, folder, capped)}
        thread = threading.Thread(
            target=metering.run_metered,
            args=(target, hold),
            kwargs={"free_unless_solved": True, **kwargs},
            name=f"labsagent-{job.id}",
            daemon=True,
        )
    elif kind == "followup":
        _snapshot(job, restore=tries > 0)
        thread = threading.Thread(
            target=_followup,
            args=(job, hold, work.get("feedback") or ""),
            name=f"labsagent-revise-{job.id}",
            daemon=True,
        )
    elif kind == "identity":
        thread = threading.Thread(
            target=repackage_job,
            args=(job, work.get("identity") or {}),
            name=f"labsagent-identity-{job.id}",
            daemon=True,
        )
    else:
        job.finish(error="I lost track of what this lab was doing. Start it again.")
        metering.settle(hold, RunUsage(), free=True)
        return
    thread.start()


def resume(job: Job, work: dict, *, auto: bool) -> None:
    """Start interrupted work again: by itself after a restart, or because
    the student pressed Resume. Raises (402/503) only for the button; a
    refusal after a restart parks the lab with the reason instead."""
    with _RESUMING:
        if not auto and (job.status != "interrupted" or not job.state.get("pending")):
            raise HTTPException(409, "this run isn't waiting to be resumed")
        kind = work.get("kind")
        stage, done, total = _stage(job, work)
        try:
            if kind == "identity":
                hold = metering.UNMETERED  # a rebuild, no model call
            else:
                hold = metering.reserve(
                    job.owner, job.id, "run" if kind == "run" else "followup", load_settings()
                )
        except HTTPException as exc:
            if not auto:
                raise
            text = f"{_said(stage, done, total, resuming=False)} {exc.detail} Press Resume when you're ready."
            job.park(work, {"type": "interrupted", "stage": stage, "resuming": False, "text": text})
            return

        try:
            if kind in ("followup", "identity"):
                ensure_context(job)  # a run rebuilds its own (resume_job)
            detail = {k: v for k, v in work.items() if k not in ("kind", "tries", "at")}
            job.begin(kind, tries=int(work.get("tries", 0)) + 1, **detail)
            job.solve_started = False
            job.reopen()
            if auto:
                job.publish(
                    {
                        "type": "interrupted",
                        "stage": stage,
                        "resuming": True,
                        "text": _said(stage, done, total, resuming=True),
                    }
                )
            else:
                job.publish({"type": "resumed", "stage": stage, "text": _carrying_on(stage, done, total)})
            launch(job, hold)
        except Exception as exc:  # noqa: BLE001 -- a resume must never leave a hung lab
            metering.settle(hold, RunUsage(), free=True)
            detail = getattr(exc, "detail", None) or f"{type(exc).__name__}: {exc}"
            job.finish(error=str(detail)[:500])
            if not auto:
                raise HTTPException(409, str(detail)) from exc


def adopt(job: Job) -> None:
    """A job read back from disk with work no process is doing any more.

    Called by the registry as it restores one -- at startup for every such
    job, or later, the first time someone asks for it.
    """
    work, job.orphan = job.orphan, None
    if not work:
        return
    if int(work.get("tries", 0)) < AUTO_TRIES:
        resume(job, work, auto=True)
        return
    stage, done, total = _stage(job, work)
    text = f"{_said(stage, done, total, resuming=False)} {TWICE} Press Resume to try again."
    job.park(work, {"type": "interrupted", "stage": stage, "resuming": False, "text": text})


def recover(registry: JobRegistry) -> threading.Thread:
    """At startup, AFTER the stale holds are released: every job the last
    process was in the middle of, read back -- which starts it again once,
    or parks it. On a thread, so the server answers while it works; a
    browser asking for one of these first simply adopts it itself."""

    def run() -> None:
        for job_id in registry.orphans():
            try:
                registry.get(job_id)
            except Exception as exc:  # noqa: BLE001 -- one bad job must not stop the rest
                print(f"durable: could not recover {job_id}: {exc}", flush=True)

    thread = threading.Thread(target=run, name="labsagent-recover", daemon=True)
    thread.start()
    return thread
