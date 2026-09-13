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
   The cost is that the log has to live in memory until the job is evicted --
   a real trade, and the right side of it when the log is a few hundred dicts.

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

import threading
import time
import uuid
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = frozenset({"done", "failed"})
HEARTBEAT_S = 15.0


class Job:
    """One run, from upload to download.

    Written by exactly one worker thread and read by any number of HTTP
    threads, so every field that crosses that line is guarded by `_cv`.
    """

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = "queued"  # queued | running | awaiting_input | done | failed
        self.created_at = time.time()
        self.error: str | None = None
        self.summary: dict[str, Any] = {}
        # key -> path. The download whitelist; nothing outside it is served.
        self.artifacts: dict[str, Path] = {}

        self._log: list[dict[str, Any]] = []
        self._cv = threading.Condition()
        self._answers: dict[str, str] | None = None
        self._cancelled = False

    # ------------------------------------------------------------- producer
    # Everything below runs on the worker thread.

    def _emit_locked(self, payload: dict[str, Any]) -> None:
        """Append to the log. Caller holds the lock."""
        payload["seq"] = len(self._log) + 1
        self._log.append(payload)
        self._cv.notify_all()

    def publish(self, payload: dict[str, Any]) -> None:
        with self._cv:
            self._emit_locked(payload)

    def phase(self, key: str, label: str) -> None:
        """Announce a stage by name. The core has no notion of phases; this is
        the web layer's own vocabulary for the stepper in the UI."""
        self.publish({"type": "phase", "key": key, "label": label})

    def register(self, key: str, path: Path, *, label: str, kind: str) -> None:
        """Add a file to the whitelist and tell the browser it exists.

        The browser receives `key` and a display filename, never the path on
        disk. A server that echoes a path is inviting a client to try another.
        """
        path = Path(path)
        with self._cv:
            self.artifacts[key] = path
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


class JobRegistry:
    """Jobs, kept in memory, bounded.

    There is no database and no eviction policy beyond "keep the last N".
    That is the correct amount of machinery for a single-user tool bound to
    localhost: anything more is a solution to a problem this deployment does
    not have.
    """

    def __init__(self, *, keep: int = 25) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._keep = keep

    def create(self) -> Job:
        job = Job(uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._keep:
                oldest = self._order.pop(0)
                if self._jobs.get(oldest, job).is_terminal() and oldest != job.id:
                    self._jobs.pop(oldest, None)
                else:
                    self._order.append(oldest)  # still running; try the next one
                    break
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.is_terminal()]
