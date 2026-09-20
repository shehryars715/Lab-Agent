"""Error taxonomy.

The distinction that matters: SandboxError is infrastructure and retries with
backoff WITHOUT consuming the task's attempt budget. ExecutionFailure is the
agent's problem and DOES consume it. Conflating them either wastes attempts on
network blips or masks a genuinely broken solution.
"""

from __future__ import annotations


class LabsAgentError(Exception):
    """Base for everything this package raises."""


class IngestError(LabsAgentError):
    """The DOCX could not be read."""


class SpecError(LabsAgentError):
    """Tasks could not be extracted from the manual."""


class AnchorError(SpecError):
    """No insertion anchors found -- often means tasks live in table cells."""


class DataError(LabsAgentError):
    """A dataset could not be obtained.

    Deliberately NOT a subclass of the execution errors. Failing to fetch a
    CSV is not a failed task and must never consume a solver attempt -- the
    acquisition step catches this, says which reference failed and why, and
    lets every task that does not need that file run anyway.
    """


class SandboxError(LabsAgentError):
    """Infrastructure failure. Retry with backoff; does NOT consume an attempt."""


class ExecutionFailure(LabsAgentError):
    """The program itself failed. DOES consume an attempt."""


class TimeoutFailure(ExecutionFailure):
    """The program exceeded its wall-clock budget."""


class InputExhausted(ExecutionFailure):
    """The program asked for more stdin values than were supplied."""


class GiveUp(LabsAgentError):
    """Retries exhausted for a task. Caller records a failed outcome and moves on."""
