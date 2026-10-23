"""A run in progress: its event log, its download whitelist, and the gate that
stops it for a human.

THREE IDEAS, and they are the whole of this file.

1. AN EVENT LOG IS A BETTER INTERFACE THAN A SOCKET.

   The obvious design for streaming progress is: the worker writes to a queue,
   the browser reads from the queue, done. It breaks the moment the browser
   reconnects -- everything already sent is gone, and there is no way to ask
   for it again, because a queue is consumed by definition.

   A LOG is an append-only list with sequence numbers. A consumer says "give me
   everything after 41" and gets it, whether it is connecting for the first
   time, reconnecting after a dropped connection, or slower than the producer.
   Replay and streaming stop being two features and become one: this is why
   Kafka, Redis Streams and SSE's own `Last-Event-ID` all have the same shape.

   THE LOG OUTLIVES THE PROCESS (2026-10-23). It used to live only in memory,
   so every deploy or crash emptied every chat. Now each event is also
   appended to `<job folder>/events.jsonl` and FLUSHED -- handed to the
   operating system, which survives the process being killed (a deploy, an
   OOM kill), though not a power cut; that would take an fsync per event, and
   costs a few lines of chat in a rare event. The in-memory list is now a
   cache of the file. Beside it, `job.json` says what work is in flight, so a
   new process can tell a lab that finished from one a restart cut short.

2. A PAUSE IS A CONDITION WAIT, NOT A BUSY LOOP.

   The worker thread that needs an answer from the browser parks on a
   Condition. The HTTP request that carries the answer notifies it. The thread
   consumes no CPU while parked, and the wake-up is immediate rather than
   polled. The `min(remaining, 1.0)` in the wait is not imprecision -- it is a
   bounded nap so that a cancel is noticed within a second without the waiter
   spinning.

3. THE CLIENT NEVER LEARNS A FILESYSTEM PATH.

   Downloads are looked up by key in a dict this process built. The browser
   asks for "report", not for "../../.env". Path traversal is not prevented
   here; it is unrepresentable. Prevention is a filter you can get wrong -- a
   whitelist is a data structure you cannot.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from labsagent.runstore import atomic_write_text

# "interrupted": a restart cut the work short and it is waiting for a button
# (durable.py). Terminal, so it never counts as an account's running lab.
TERMINAL_STATUSES = frozenset({"done", "failed", "interrupted"})
HEARTBEAT_S = 15.0

EVENTS_FILE = "events.jsonl"
STATE_FILE = "job.json"

#: What `JobRegistry.create` mints: `uuid4().hex[:12]`. A job id from a URL is
#: about to become a folder name, so anything else -- "../../etc" above all --
#: is not a job, before any path is built from it.
JOB_ID = re.compile(r"[0-9a-f]{12}")


class Job:
    """One run, from upload to download.

    Written by exactly one worker thread and read by any number of HTTP
    threads, so every field that crosses that line is guarded by `_cv`.
    """

    def __init__(self, job_id: str, folder: Path | None = None) -> None:
        self.id = job_id
        #: Who started it (auth's user id). Set once, before any work, by
        #: ownership.record; every route that takes this job's id checks it.
        self.owner: str | None = None
        self.solve_started = False
        # queued | running | awaiting_input | done | failed | interrupted
        self.status = "queued"
        self.created_at = time.time()
        self.error: str | None = None
        self.summary: dict[str, Any] = {}
        # key -> path. The download whitelist; nothing outside it is served.
        self.artifacts: dict[str, Path] = {}
        #: Where this job keeps its log and `job.json` (and its uploads).
        #: None keeps it in memory only, as the tests and the eval do.
        self.folder = Path(folder) if folder is not None else None
        #: The run folder, once there is one. Saved in `job.json` by name.
        self.run_dir: Path | None = None
        #: What `job.json` holds: `work` in flight, `pending` work waiting for
        #: the Resume button, `run_dir`, and the whitelist by relative path.
        self.state: dict[str, Any] = {}
        #: Work a restart cut short, found when this job was read back from
        #: disk and not yet dealt with (durable.adopt takes it).
        self.orphan: dict[str, Any] | None = None

        self._log: list[dict[str, Any]] = []
        self._sink = None  # the open events file, while work is in flight
        self._cv = threading.Condition()
        self._answers: dict[str, str] | None = None
        self._cancelled = False

    # ------------------------------------------------------------- disk

    def _persist_locked(self, payload: dict[str, Any]) -> None:
        """Append one event to the file. Caller holds the lock, so lines land
        in sequence order -- and a reader can only see an event after this has
        returned, so no browser ever holds an event the file lacks."""
        if self.folder is None:
            return
        try:
            if self._sink is None:
                self.folder.mkdir(parents=True, exist_ok=True)
                self._sink = (self.folder / EVENTS_FILE).open("a", encoding="utf-8")
            self._sink.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._sink.flush()
        except (OSError, TypeError, ValueError) as exc:
            # A full disk must not end a lab the student is watching. The chat
            # carries on in memory; only its survival of a restart is lost.
            print(f"jobs: stopped saving the log of {self.id}: {exc}", flush=True)
            self.folder = None

    def _close_sink_locked(self) -> None:
        if self._sink is not None:
            try:
                self._sink.close()
            except OSError:
                pass
            self._sink = None

    def _save_state_locked(self) -> None:
        if self.folder is None:
            return
        try:
            atomic_write_text(
                self.folder / STATE_FILE, json.dumps(self.state, ensure_ascii=False)
            )
        except (OSError, TypeError, ValueError) as exc:
            print(f"jobs: could not save the state of {self.id}: {exc}", flush=True)

    def begin(self, kind: str, *, tries: int = 0, **detail: Any) -> None:
        """Record the work about to start, BEFORE it starts.

        `kind` is run | followup | identity, and `detail` is what doing it
        again would need (the message, the file names, the change). Cleared by
        `finish`. Still set when a new process reads this job back means the
        process doing it died -- the one fact a restart cannot otherwise tell.
        """
        with self._cv:
            self.state["work"] = {"kind": kind, "tries": tries, "at": time.time(), **detail}
            self.state.pop("pending", None)
            self._save_state_locked()

    def attach_run(self, run_dir: Path) -> None:
        """This job's run folder: where its files are, after a restart too."""
        with self._cv:
            self.run_dir = Path(run_dir)
            self.state["run_dir"] = self.run_dir.name
            self._save_state_locked()

    def park(self, work: dict[str, Any], payload: dict[str, Any]) -> None:
        """Stop here, keeping `work` to redo when the student presses Resume."""
        with self._cv:
            self.state["work"] = None
            self.state["pending"] = work
            self._save_state_locked()
            self.status = "interrupted"
            self._emit_locked(payload)
            self._close_sink_locked()
            self._cv.notify_all()

    @classmethod
    def restore(cls, job_id: str, folder: Path, runs_root: Path) -> "Job | None":
        """This job as the last process left it, read from its folder.

        Status is replayed from the events, not stored: the log is the truth
        and `job.json` only adds what the log cannot say (work in flight, the
        files' paths). A torn last line -- the process died mid-write -- was
        never sent to anyone, since readers wait on the lock that write held.
        """
        events = folder / EVENTS_FILE
        if not events.is_file():
            return None
        job = cls(job_id, folder=folder)
        try:
            state = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        job.state = state if isinstance(state, dict) else {}

        by_seq: dict[int, dict] = {}
        for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            seq = event.get("seq") if isinstance(event, dict) else None
            if isinstance(seq, int) and seq > 0:
                by_seq[seq] = event
        # Position i holds seq i+1, which `since` relies on. A line lost to a
        # failed write leaves a gap; a placeholder keeps the numbering, and
        # the browser ignores a type it does not know.
        job._log = [by_seq.get(n, {"type": "gap", "seq": n}) for n in range(1, max(by_seq, default=0) + 1)]
        job.status, job.error, job.summary = _replay(job._log)
        job.created_at = float(job.state.get("created_at") or events.stat().st_mtime)

        name = str(job.state.get("run_dir") or "")
        if name and "/" not in name and "\\" not in name and not name.startswith("."):
            if (runs_root / name).is_dir():
                job.run_dir = runs_root / name
        if job.run_dir is not None:
            home = job.run_dir.resolve()
            for key, rel in (job.state.get("artifacts") or {}).items():
                path = (job.run_dir / str(rel)).resolve()
                # The disk is data, not authority: a path that leaves the run
                # folder is not one this server wrote, so it is not served.
                if path.is_relative_to(home) and path.is_file():
                    job.artifacts[str(key)] = path

        work = job.state.get("work")
        if work:
            # Set means its process died mid-way: no process now is doing it.
            job.orphan = dict(work)
            job.status = "interrupted"
        elif job.status not in TERMINAL_STATUSES:
            # Work with no record of starting: say it stopped, never hang. As
            # an EVENT, because a replay that never ends keeps the browser
            # reconnecting forever.
            job.finish(error="This lab stopped when the server restarted.")
        return job

    # ------------------------------------------------------------- producer
    # Everything below runs on the worker thread.

    def _emit_locked(self, payload: dict[str, Any]) -> None:
        """Append to the log. Caller holds the lock."""
        payload["seq"] = len(self._log) + 1
        self._log.append(payload)
        self._persist_locked(payload)
        self._cv.notify_all()

    def publish(self, payload: dict[str, Any]) -> None:
        with self._cv:
            self._emit_locked(payload)

    @property
    def last_seq(self) -> int:
        """The sequence number of the newest event (0 before any)."""
        with self._cv:
            return len(self._log)

    def phase(self, key: str, label: str) -> None:
        """Announce a stage by name. The core has no notion of phases; this is
        the web layer's own vocabulary for the stepper in the UI."""
        if key == "solving":
            # The line between free and paid: a lab stopped before this point
            # (not a lab, out of scope, missing data) is never charged.
            self.solve_started = True
        self.publish({"type": "phase", "key": key, "label": label})

    def register(self, key: str, path: Path, *, label: str, kind: str) -> None:
        """Add a file to the whitelist and tell the browser it exists.

        The browser receives `key` and a display filename, never the path on
        disk. A server that echoes a path is inviting a client to try another.
        """
        path = Path(path)
        with self._cv:
            self.artifacts[key] = path
            if self.run_dir is not None and path.is_relative_to(self.run_dir):
                # By path inside the run folder, so a new process can serve it.
                self.state.setdefault("artifacts", {})[key] = path.relative_to(self.run_dir).as_posix()
                self._save_state_locked()
            self._emit_locked(
                {
                    "type": "artifact",
                    "key": key,
                    "label": label,
                    "kind": kind,  # report | code | package
                    "filename": path.name,
                    "bytes": path.stat().st_size if path.exists() else 0,
                }
            )

    def ask(self, questions: list[dict[str, Any]], *, timeout_s: float) -> dict[str, str]:
        """Block this thread until the browser answers, or until we give up.

        Returning `{}` on timeout rather than raising is deliberate: the
        pipeline downstream has a placeholder path, and a run that completes
        with "Your Name" on the cover page is strictly more useful than a run
        that died because nobody was looking at the tab.
        """
        with self._cv:
            self.status = "awaiting_input"
            self._emit_locked(
                {
                    "type": "needs_input",
                    "questions": questions,
                    "timeout_s": int(timeout_s),
                }
            )

            deadline = time.monotonic() + timeout_s
            while self._answers is None and not self._cancelled:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Bounded wait: long enough to be idle, short enough that a
                # cancel or a clock change is noticed promptly.
                self._cv.wait(timeout=min(remaining, 1.0))

            answers = self._answers
            self._answers = None
            self.status = "running"
            if answers:
                # The answers go back out on the wire. Without them the
                # transcript shows an "Answered" card with nothing under it --
                # the browser typed them, but the browser does not keep its own
                # copy, precisely so there is one source of truth. Echoing them
                # costs a few bytes and is the only way the card can say what
                # was said after a page reload.
                self._emit_locked({"type": "input_received", "answers": answers})
            else:
                self._emit_locked({"type": "input_timeout"})
            return answers or {}

    def reopen(self) -> None:
        """Put a finished job back into `running` so the stream keeps flowing.

        A revision reuses the job, and `_sse` ends its response as soon as the
        status is terminal. Without this, a revision's events would be
        published onto a stream that had already closed -- the browser would
        see the run stop dead and never learn the revision happened.

        The event log is deliberately NOT cleared. The client's cursor is a
        sequence number into it, and truncating the log under a connected
        reader is how you get it to replay the whole run a second time.
        """
        with self._cv:
            self.status = "running"
            self.error = None
            self._cv.notify_all()

    def finish(self, *, summary: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._cv:
            if error:
                self.status, self.error = "failed", error
                self._emit_locked({"type": "failed", "error": error})
            else:
                self.status = "done"
                self.summary.update(summary or {})
                self._emit_locked({"type": "done", **self.summary})
            # Nothing in flight any more: a restart now finds a finished job.
            if self.state.get("work"):
                self.state["work"] = None
                self._save_state_locked()
            self._close_sink_locked()
            self._cv.notify_all()

    def cancel(self) -> None:
        with self._cv:
            self._cancelled = True
            self._cv.notify_all()

    # ------------------------------------------------------------- consumer
    # Everything below runs on an HTTP thread.

    def submit(self, answers: dict[str, str]) -> bool:
        """Accept the browser's answers. False if the job was not waiting."""
        with self._cv:
            if self.status != "awaiting_input":
                return False
            self._answers = {str(k): str(v) for k, v in answers.items()}
            self._cv.notify_all()
            return True

    def is_terminal(self) -> bool:
        with self._cv:
            return self.status in TERMINAL_STATUSES

    def since(self, cursor: int, *, timeout_s: float = HEARTBEAT_S) -> tuple[list[dict], int, bool]:
        """Everything after `cursor`. Blocks up to `timeout_s` if there is nothing.

        Returns (events, new_cursor, finished). An empty `events` list is not
        an error -- it is the heartbeat case, and the caller is expected to
        send something anyway so a dead connection is noticed.
        """
        with self._cv:
            if cursor >= len(self._log) and self.status not in TERMINAL_STATUSES:
                self._cv.wait(timeout=timeout_s)
            return self._log[cursor:], len(self._log), self.status in TERMINAL_STATUSES


def _replay(log: list[dict]) -> tuple[str, str | None, dict]:
    """(status, error, summary) as the events left them.

    A terminal event ends a stretch of work; anything after one is a change
    that reopened the job. Ending outside a terminal event means the work was
    still going when the log stopped.
    """
    status, error, summary = "queued", None, {}
    for event in log:
        kind = event.get("type")
        if kind == "gap":
            continue
        if kind == "done":
            status, error = "done", None
            summary.update({k: v for k, v in event.items() if k not in ("type", "seq")})
        elif kind == "failed":
            status, error = "failed", event.get("error")
        elif kind == "interrupted" and not event.get("resuming"):
            status = "interrupted"
        elif kind == "needs_input":
            status = "awaiting_input"
        else:
            status = "running"
    return status, error, summary


class JobRegistry:
    """Jobs, kept in memory, bounded -- and read back from disk on demand.

    Eviction is "keep the last N", which bounds memory, not history: with a
    `root`, an evicted job (or one from before a restart) is read back from
    its folder the next time someone asks for it. `owner_of` says whose it is
    (the database, in multi-user mode) BEFORE its log is read, and
    `on_restore` deals with work a restart cut short (durable.adopt).
    """

    def __init__(
        self,
        *,
        keep: int = 200,
        root: Path | None = None,
        owner_of: Callable[[str], str | None] | None = None,
        on_restore: Callable[[Job], None] | None = None,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._keep = keep
        self.root = Path(root) if root is not None else None
        self._owner_of = owner_of
        self._on_restore = on_restore

    def _add_locked(self, job: Job) -> None:
        self._jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > self._keep:
            oldest = self._order.pop(0)
            if self._jobs.get(oldest, job).is_terminal() and oldest != job.id:
                self._jobs.pop(oldest, None)
            else:
                self._order.append(oldest)  # still running; try the next one
                break

    def create(self, root: Path | None = None) -> Job:
        """A new job. Its folder is `<root>/<id>` (the registry's own root
        unless one is given -- the upload route passes the folder its files
        go to, so the log lands beside them)."""
        job_id = uuid.uuid4().hex[:12]
        home = root if root is not None else self.root
        job = Job(job_id, folder=Path(home) / job_id if home is not None else None)
        job.state["created_at"] = job.created_at
        with self._lock:
            self._add_locked(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None or self.root is None:
            return job
        return self._restore(job_id)

    def _restore(self, job_id: str) -> Job | None:
        if not JOB_ID.fullmatch(job_id):
            return None
        folder = self.root / job_id
        if not (folder / EVENTS_FILE).is_file():
            return None
        # Whose it is first, and from the database: a stranger's id never
        # gets as far as reading the log. May raise (unreachable: a 503).
        owner = self._owner_of(job_id) if self._owner_of else None
        if owner is None:
            return None
        with self._lock:
            # Read under the lock, so two requests racing for the same job
            # get one Job, not two writing one file.
            job = self._jobs.get(job_id)
            if job is not None:
                return job
            job = Job.restore(job_id, folder, self.root.parent)
            if job is None:
                return None
            job.owner = owner
            self._add_locked(job)
        if job.orphan and self._on_restore is not None:
            self._on_restore(job)
        return job

    def orphans(self) -> list[str]:
        """Ids of jobs whose `job.json` says work was in flight: the ones the
        last process was doing when it stopped. Read at startup."""
        if self.root is None or not self.root.is_dir():
            return []
        found = []
        for folder in self.root.iterdir():
            if not JOB_ID.fullmatch(folder.name) or folder.name in self._jobs:
                continue
            try:
                state = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and state.get("work"):
                found.append(folder.name)
        return found

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.is_terminal()]

    def for_run(self, run_id: str, owner: str | None) -> Job | None:
        """A job of this owner's already holding this run folder, if any --
        so reopening a lab twice gives one job, not two writing one folder."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in reversed(jobs):
            store = getattr(getattr(job, "context", None), "store", None)
            name = store.run_id if store is not None else getattr(job.run_dir, "name", None)
            if name == run_id and job.owner == owner:
                return job
        return None

    def active_for(self, owner: str) -> list[Job]:
        """This user's runs that are still working (or waiting on them)."""
        return [j for j in self.active() if j.owner == owner]
