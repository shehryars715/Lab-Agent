"""Typed progress events.

WHY THIS EXISTS BEFORE IT HAS TWO CONSUMERS. The core must not know whether it
is being watched by a terminal, a web socket, a log file, or nothing. Today the
CLI is the only consumer; in Phase 7 a browser becomes a second one. If the core
printed directly, adding the browser would mean rewriting the core.

The pattern is an observer/event bus, and the rule generalises: when you can
name a future consumer, emit events rather than side effects. The cost now is
about forty lines. The cost later is a rewrite of everything that prints.

Events are DATA, not formatting. `AttemptFailed` carries the error; it does not
carry a red X or a wrapped string. Consumers decide how to render. That split is
what lets the same event drive a terminal line, a JSON log, and a progress bar.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event. `at` is set automatically so consumers can order or time them."""

    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, kw_only=True)
class RunStarted(Event):
    run_id: str
    lab_number: str
    task_count: int


@dataclass(frozen=True, kw_only=True)
class IngestFinished(Event):
    task_count: int
    anchor_repairs: int


@dataclass(frozen=True, kw_only=True)
class TaskStarted(Event):
    task_id: str
    title: str
    index: int
    total: int


@dataclass(frozen=True, kw_only=True)
class AttemptStarted(Event):
    task_id: str
    attempt: int
    max_attempts: int


@dataclass(frozen=True, kw_only=True)
class AttemptFailed(Event):
    task_id: str
    attempt: int
    error: str


@dataclass(frozen=True, kw_only=True)
class ArtifactWritten(Event):
    task_id: str
    # NOT named `kind`: that collides with Event.kind (the event-type property),
    # and a dataclass field cannot shadow a base-class property. The collision
    # is invisible at class-definition time and only raises on instantiation.
    artifact: str  # "code" | "screenshot" | "report" | "archive"
    path: str


@dataclass(frozen=True, kw_only=True)
class TaskFinished(Event):
    task_id: str
    status: str
    attempts: int
    cost_usd: float = 0.0


@dataclass(frozen=True, kw_only=True)
class RunFinished(Event):
    run_id: str
    passed: int
    failed: int
    cost_usd: float
    aborted: bool = False


@runtime_checkable
class EventConsumer(Protocol):
    def __call__(self, event: Event) -> None: ...


class Emitter:
    """Fan-out to zero or more consumers.

    A failing consumer must never break the run -- a broken progress bar is not
    a reason to lose a lab report. Consumer exceptions are swallowed and
    recorded, which is the standard contract for observability side-channels.
    """

    def __init__(self, *consumers: Callable[[Event], None]) -> None:
        self._consumers: list[Callable[[Event], None]] = list(consumers)
        self.errors: list[str] = []

    def subscribe(self, consumer: Callable[[Event], None]) -> None:
        self._consumers.append(consumer)

    def emit(self, event: Event) -> Event:
        for consumer in self._consumers:
            try:
                consumer(event)
            except Exception as exc:  # noqa: BLE001 - never break the run
                self.errors.append(f"{type(exc).__name__}: {exc}")
        return event


class Recorder:
    """Consumer that keeps every event. Useful in tests and for run logs."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)

    def of_kind(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    @property
    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


def console_consumer(event: Event) -> None:
    """Render events for a terminal. The ONLY place formatting lives."""
    if isinstance(event, RunStarted):
        print(f"run {event.run_id}  lab {event.lab_number}  {event.task_count} tasks")
    elif isinstance(event, IngestFinished):
        extra = f", {event.anchor_repairs} anchor repair(s)" if event.anchor_repairs else ""
        print(f"  parsed {event.task_count} tasks{extra}")
    elif isinstance(event, TaskStarted):
        print(f"\n[{event.index}/{event.total}] {event.task_id}: {event.title}")
    elif isinstance(event, AttemptStarted):
        print(f"    attempt {event.attempt}/{event.max_attempts}")
    elif isinstance(event, AttemptFailed):
        print(f"    failed: {event.error[:110]}")
    elif isinstance(event, ArtifactWritten):
        print(f"    {event.artifact}: {event.path}")
    elif isinstance(event, TaskFinished):
        mark = "ok" if event.status == "passed" else "FAILED"
        print(f"    -> {mark} after {event.attempts} attempt(s)  ${event.cost_usd:.6f}")
    elif isinstance(event, RunFinished):
        state = "ABORTED" if event.aborted else "done"
        print(
            f"\n{state}: {event.passed} passed, {event.failed} failed, "
            f"${event.cost_usd:.6f}"
        )


def event_to_dict(event: Event) -> dict:
    """One event as a JSON-safe dict, kind included.

    `dataclasses.asdict` is the convenient call and chokes on `at`, which is a
    datetime -- the same boundary `runstore` and `wire_event` each have to
    cross. This is that boundary for the log on disk.
    """
    data = {"kind": event.kind}
    for key, value in vars(event).items():
        data[key] = value.isoformat() if isinstance(value, datetime) else value
    return data


def write_events_log(store, events) -> "Path":
    """The run's events as JSON Lines.

    WHY NOT `f"{at} {kind}"`. That is what both call sites wrote, separately,
    and it drops every payload -- so a finished run could tell you that an
    attempt failed three times and not why, which is the one thing you want
    when a run has gone wrong. One writer, one format, and the payload kept.
    """
    return store.write_log(
        "events.log",
        "\n".join(json.dumps(event_to_dict(e), default=str) for e in events),
    )
