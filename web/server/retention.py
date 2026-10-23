"""Delete runs and uploads after `retention_days` (decided: 30 for public use).

Strangers' course documents sit on this server. Keeping them forever is a
privacy liability and, on a 40 GB disk, eventually an outage. So a daemon
thread wakes at startup and every few hours and removes:

  - run folders whose name says they are older than the window
    (`<YYYYmmdd-HHMMSS>_lab...`: the name IS the timestamp, which survives a
    copy or a restore where an mtime would not);
  - upload staging folders (`_web_uploads/<job>`) by modification time --
    and, with a run folder, together with it: the job id is the run
    folder's suffix. Since 2026-10-23 that folder also holds the job's chat
    (`events.jsonl`), so a lab's conversation goes when its files go;
    otherwise every change would keep the chat alive past its files;
  - and, with Supabase, the `runs` rows that pointed at deleted folders.

OFF BY DEFAULT (`retention_days = 0`). Turning it on is a decision about the
data on a particular server -- the private beta's history is older than 30
days, and switching this on there deletes it.
"""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import auth, supa
from .jobs import JOB_ID

SWEEP_EVERY_S = 6 * 3600


def _stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sweep(root: Path, days: int, *, now: datetime | None = None) -> list[str]:
    """Delete what is older than `days`. Returns the run folders removed."""
    if days <= 0 or not root.is_dir():
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    removed: list[str] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        stamp = _stamp(folder.name)
        if stamp is not None and stamp < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    uploads = root / "_web_uploads"
    for name in removed:
        job_id = name.rsplit("_", 1)[-1]
        if JOB_ID.fullmatch(job_id):
            shutil.rmtree(uploads / job_id, ignore_errors=True)
    if uploads.is_dir():
        for staged in uploads.iterdir():
            try:
                old = staged.stat().st_mtime < cutoff.timestamp()
            except OSError:
                continue
            if old:
                shutil.rmtree(staged, ignore_errors=True) if staged.is_dir() else staged.unlink(missing_ok=True)
    if removed and auth.mode() == "supabase":
        try:
            supa.rpc("forget_runs", p_run_dirs=removed)
        except supa.SupaError:
            pass  # the folders are gone; owns_run on a missing folder is 404 anyway
    return removed


def start(root: Path, days: int) -> threading.Thread | None:
    if days <= 0:
        return None

    def loop() -> None:
        while True:
            try:
                removed = sweep(root, days)
                if removed:
                    print(f"retention: removed {len(removed)} run(s) older than {days} days", flush=True)
            except Exception as exc:  # noqa: BLE001 -- a sweep must never kill the server
                print(f"retention: sweep failed: {exc}", flush=True)
            time.sleep(SWEEP_EVERY_S)

    thread = threading.Thread(target=loop, name="labsagent-retention", daemon=True)
    thread.start()
    return thread
