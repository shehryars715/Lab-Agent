"""Datasets: a resolved fact, not a string the solver has to interpret.

WHY THIS PACKAGE EXISTS. A lab that says "use the attached sales.csv" or
"download the dataset from Kaggle" had no channel at all. The solver invented
data, or wrote `pd.read_csv("data.csv")` against a file that was not there and
failed after the run was paid for. Meanwhile `briefing.py` has always listed
"data that is referenced but not supplied ('which CSV?')" as a good question to
ask -- so the agent asked, and nothing could answer.

THE SHAPE IS `Intent`'S. `intent.py` exists because user input used to reach the
engine only as free text stapled onto a task statement, where it could change
how code was written but never what was produced. A dataset reference has the
same problem and gets the same fix: it is resolved once, into a value with a
real path on it, and everything downstream reads the value.

ACQUISITION IS A SEAM, like `Sandbox`, `ScreenshotBackend`, `EventConsumer` and
`Emitter`. Upload, URL and Kaggle are three implementations of "get me this
file" -- see `sources.py`. A missing credential or a dead link produces a
`DataError` that the caller turns into a sentence, never a crashed run.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from labsagent.blocks import LEAD, Block
from labsagent.data.refs import candidate_tokens, split_refs
from labsagent.errors import DataError

#: Refused above this, per file. The point is to turn "you attached the wrong
#: thing" into a fast error rather than a full disk, and to keep the per-task
#: copy below (one file per task) bounded.
MAX_DATASET_BYTES = 100 * 1024 * 1024

#: Extensions we will accept as data. Deliberately a list and not "anything not
#: a document": an .exe copied into a workspace the agent can run from is not a
#: dataset, and the solver's own language is one of the suffixes we exclude.
DATA_SUFFIXES = (
    ".csv", ".tsv", ".tab", ".txt", ".data", ".dat",
    ".json", ".jsonl", ".ndjson",
    ".xlsx", ".xls", ".xlsm",
    ".parquet", ".zip", ".gz",
)


@dataclass(frozen=True)
class Dataset:
    """One data file, already on disk and already described.

    `name` is what the solver is told to open, so it is a bare filename: the
    file is copied into each task's workspace and the program's cwd is that
    workspace. A path here would be a path the generated code hardcodes, and
    the submitted .py would only run on this machine.
    """

    name: str
    path: Path
    origin: str = "upload"      # "upload" | "url" | "kaggle"
    ref: str = ""               # the URL, slug, or filename it came from
    bytes: int = 0
    preview: str = ""

    @property
    def source_note(self) -> str:
        """Where this came from, or "" when the name already says it.

        An upload gets no note on purpose. This line is rendered into the
        report, the notebook AND the submitted .py, and "supplied by you"
        reads wrong in a file the student hands to someone else -- while a
        Kaggle slug or a URL is worth stating in all three.
        """
        if self.origin == "kaggle":
            return f"from Kaggle dataset {self.ref}"
        if self.origin == "url":
            return f"downloaded from {self.ref}"
        return ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path.as_posix(),
            "origin": self.origin,
            "ref": self.ref,
            "bytes": self.bytes,
            "preview": self.preview,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Dataset":
        return cls(
            name=data["name"],
            path=Path(data["path"]),
            origin=data.get("origin", "upload"),
            ref=data.get("ref", ""),
            bytes=data.get("bytes", 0),
            preview=data.get("preview", ""),
        )


@dataclass
class Acquisition:
    """What the acquire step managed, and what it did not.

    Failures are carried rather than raised because one unreachable URL must
    not cost you the four tasks that never needed it -- the same policy
    `emit_all` applies to a format that will not render.
    """

    datasets: list[Dataset] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (ref, reason)
    #: Every reference we were asked to get, deduped. Needed to tell "this lab
    #: wanted no data" apart from "this lab wanted data and got none" -- which
    #: look identical if you only ever look at `datasets`.
    requested: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.datasets)

    @property
    def total_failure(self) -> bool:
        """Asked for data, got none of it.

        Distinct from `not ok`: a lab that never named a dataset is not
        failing, it simply has no data. One reference failing out of three is
        not this either -- that is the partial case `acquire` exists to
        tolerate, and it must keep being tolerated.
        """
        return bool(self.requested) and not self.datasets


def materialize(datasets, workspace: Path) -> list[str]:
    """Copy each dataset into a task workspace. Returns the names written.

    COPY, NOT LINK. `orchestrator.solve_task` guarantees that "Task 3 cannot
    read or overwrite task 1's solution" by giving each task its own directory;
    a hardlink would quietly reintroduce exactly the sharing that isolation
    exists to prevent, for the one file most likely to be rewritten in place by
    a task that cleans its data.

    Skips a file that is already there and the same size, so a retry of the
    same task does not recopy a 90 MB CSV three times.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for dataset in datasets:
        source = Path(dataset.path)
        if not source.is_file():
            continue
        target = workspace / dataset.name
        if target.exists() and target.stat().st_size == source.stat().st_size:
            written.append(dataset.name)
            continue
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise DataError(f"could not place {dataset.name} in the workspace: {exc}") from exc
        written.append(dataset.name)

    return written


def stage_outputs(produced, workspace: Path) -> list[str]:
    """Copy earlier tasks' data products into this task's workspace.

    Same skip-if-same-size rule as `materialize`, and for the same reason: an
    attempt that retries must not recopy 40 MB it already has.

    COPY, NOT A SHARED DIRECTORY. The solver is told to open files by bare name
    because the .py it writes is handed in, and a `../task2/features.csv` baked
    into that file only runs on this machine. See `Dataset`'s docstring, which
    makes the same argument for datasets.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for item in produced:
        source = Path(item.get("path", ""))
        name = item.get("name") or source.name
        if not name or not source.is_file():
            continue
        target = workspace / name
        if target.exists() and target.stat().st_size == source.stat().st_size:
            written.append(name)
            continue
        try:
            shutil.copy2(source, target)
        except OSError:
            # A missing hand-off file is a prompt that names one fewer file,
            # not a failed task. The task may not even need it.
            continue
        written.append(name)

    return written


def describe_produced(produced, limit: int = 3) -> str:
    """The block naming what earlier tasks left in this workspace.

    Same shape as `describe`, so the solver reads one consistent format for
    "here is a file and here is what is in it".
    """
    items = [p for p in produced if p.get("name")][:limit]
    if not items:
        return ""
    lines = [
        "",
        "Files produced by earlier tasks, already in your working directory. "
        "Open them by name:",
        "",
    ]
    lines.extend(p.get("preview") or f"  {p['name']}" for p in items)
    return "\n".join(lines)


def describe(datasets, failures=()) -> str:
    """The block appended to a task prompt. Empty string when there is no data.

    The two prohibitions are here rather than only in the system prompt because
    this is where the file is named: the instruction and the thing it is about
    arrive together, which is the difference between a rule the model has to
    remember and one it is reading.

    WHEN NOTHING RESOLVED, SAY SO. The system prompt states that listed data
    files "are already saved in your workspace". With no data and no block, the
    solver read that, believed it, went looking for a file it had never been
    given, found the upload OUTSIDE its workspace and copied 23 MB in by hand --
    burning most of a run. Contradicting that sentence is cheap here and
    impossible in the prompt, which is byte-stable so it can be cached.
    """
    items = list(datasets)
    problems = list(failures)
    if not items and problems:
        lines = ["", "No data was resolved for this lab. I tried:", ""]
        lines.extend(f"  {ref} -- {reason}" for ref, reason in problems)
        lines += [
            "",
            "So there are no data files in your workspace. Do not go looking for "
            "them, do not read anything from outside your workspace, and do not "
            "invent substitute data. If a task cannot be done without the file, "
            "call record_task_result with status \"blocked\" and say in `missing` "
            "which data it needs.",
        ]
        return "\n".join(lines)
    if not items:
        return ""

    lines = [
        "",
        "Data files, already saved in your workspace. Open them by name -- they are "
        "in the same directory your program runs from:",
        "",
    ]
    lines.extend(d.preview or f"  {d.name}" for d in items)
    lines += [
        "",
        "Do not download anything and do not generate substitute data: these files "
        "are the data. Do not open them with read_file either -- the columns and "
        "types above are everything you need to write the code.",
    ]
    return "\n".join(lines)


def provenance_block(datasets) -> Block | None:
    """One line saying what data was used and where it came from.

    Returned as a `Block` so every emitter renders it for free -- which is what
    the block IR is for. That includes the `.py`, which renders prose as a
    comment: a submitted script that reads `customers.csv` is more useful, not
    less, for saying so at the top. The wording is therefore written to read
    correctly in a Word report, a notebook and a source file alike.
    """
    items = list(datasets)
    if not items:
        return None
    parts = [
        f"{d.name} ({d.source_note})" if d.source_note else d.name for d in items
    ]
    # Marked LEAD: it is context for the whole run, placed before the first
    # task's work, and `leading_prose` only takes prose marked this way.
    return Block("prose", text=f"Data used: {'; '.join(parts)}.", role=LEAD)


__all__ = [
    "Acquisition",
    "DATA_SUFFIXES",
    "candidate_tokens",
    "split_refs",
    "Dataset",
    "MAX_DATASET_BYTES",
    "describe",
    "describe_produced",
    "stage_outputs",
    "materialize",
    "provenance_block",
]
