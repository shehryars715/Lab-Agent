"""Is this yours? Every route that takes a run's id asks here, and nowhere else.

AUTHENTICATION IS NOT AUTHORISATION. Signing in proves who you are; it says
nothing about which runs you may open. With one reviewer that never mattered.
With strangers it is the classic hole -- IDOR, an insecure direct object
reference: sign in, change /api/runs/A to /api/runs/B, read someone else's
lab. So every `{job_id}` and `{run_id}` route goes through `require_job` or
`require_run`, and `tests/test_web_ownership.py` walks the app's routes and
fails if one does not.

A RUN YOU DO NOT OWN IS "NOT FOUND", never "forbidden". A 403 confirms the id
exists, which is itself something to learn about another student.

WHERE OWNERSHIP IS RECORDED.
  - Live runs: `Job.owner`, set when the run is created. The job is in this
    process's memory, so the check costs nothing.
  - A job read back after a restart (durable.py): `job_owner`, the same
    `runs` row, asked BEFORE its log is read, once per process. Not from the
    job's folder, for the reason below.
  - Past runs (on disk): in Postgres (`runs` table), NOT in manifest.json. The
    disk is data, not authority -- history.py's rule -- and a manifest is a
    file that a program in that run's folder tree once sat next to.
  - Open and password modes have exactly one user, so every past run is
    theirs and the database is not consulted.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from . import auth, supa
from .jobs import Job, JobRegistry

NOT_FOUND = "no such run"


def _multi_user() -> bool:
    return auth.mode() == "supabase"


def _db(name: str, **args):
    try:
        return supa.rpc(name, **args)
    except supa.SupaError as exc:
        # Unreachable or refused: say so, never fall back to "allow".
        raise HTTPException(503, "Can’t check your account right now. Try again in a minute.") from exc


def record(job: Job, user_id: str) -> None:
    """Make `user_id` the owner of a new run. Before any work starts."""
    job.owner = user_id
    if _multi_user():
        _db("record_run", p_job_id=job.id, p_user_id=user_id)


def attach_run_dir(job: Job, run_id: str) -> None:
    """Remember which folder on disk this run wrote, so history can find it.

    Called from the worker thread, so it must not raise: a run whose folder
    could not be recorded still finishes, and is only missing from history.
    """
    if not _multi_user() or not job.owner:
        return
    try:
        supa.rpc("attach_run_dir", p_job_id=job.id, p_run_dir=run_id)
    except supa.SupaError:
        pass


def job_owner(job_id: str) -> str | None:
    """Who started this job, by its id -- for a job this process did not
    start. None: no such job. Unreachable: a 503, never "allow"."""
    owner = _db("job_owner", p_job_id=job_id)
    return str(owner) if owner else None


def require_job(registry: JobRegistry, job_id: str, user_id: str | None) -> Job:
    job = registry.get(job_id)
    if job is None or user_id is None or job.owner != user_id:
        raise HTTPException(404, "no such job")
    return job


def require_run(run_id: str, user_id: str | None) -> Path:
    from . import history  # history imports the pipeline; import late

    directory = history.run_dir(run_id)
    if directory is None or user_id is None:
        raise HTTPException(404, NOT_FOUND)
    if _multi_user() and not _db("owns_run", p_user_id=user_id, p_run_dir=run_id):
        raise HTTPException(404, NOT_FOUND)
    return directory


def run_ids_for(user_id: str, limit: int) -> list[str] | None:
    """The run folders this user owns, newest first; None means "all of them"."""
    if not _multi_user():
        return None
    rows = _db("my_runs", p_user_id=user_id, p_limit=limit) or []
    return [row["run_dir"] if isinstance(row, dict) else str(row) for row in rows]
