"""Run store: round-tripping, atomicity, and resumability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome, Transcript
from labsagent.runstore import (
    RunStore,
    atomic_write_text,
    completed_task_ids,
    manifest_from_dict,
    manifest_to_dict,
    pending_tasks,
)


def _spec() -> LabSpec:
    return LabSpec(
        lab_number="03",
        title="Lab 03",
        course="CS-102",
        tasks=[
            Task(id=f"task{i}", title=f"T{i}", statement="s",
                 sample_inputs=["5"])
            for i in (1, 2, 3)
        ],
    )


def _manifest() -> RunManifest:
    spec = _spec()
    return RunManifest(
        run_id="20260913-000000_lab03",
        started_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        spec=spec,
        outcomes=[
            TaskOutcome(
                task=spec.tasks[0],
                status="passed",
                code_path=Path("code/task1.py"),
                code_text="print('hi')",
                screenshot_paths=[Path("screenshots/task1.png")],
                transcript=Transcript(command="python task1.py", lines=["hi"]),
                explanation="It prints hi.",
                attempts=1,
            )
        ],
        cost_usd=0.0004,
        anchors={f"task{i}": i * 2 for i in (1, 2, 3)},
    )


def test_manifest_round_trips_through_json():
    """Path and datetime are not JSON types -- the boundary must be reversible."""
    original = _manifest()
    restored = manifest_from_dict(json.loads(json.dumps(manifest_to_dict(original))))

    assert restored.run_id == original.run_id
    assert restored.started_at == original.started_at
    assert restored.cost_usd == original.cost_usd
    assert [t.id for t in restored.spec.tasks] == ["task1", "task2", "task3"]

    out = restored.outcomes[0]
    assert out.code_path == Path("code/task1.py")
    assert out.screenshot_paths == [Path("screenshots/task1.png")]
    assert out.transcript.lines == ["hi"]
    # Anchors are a side table now, not a field on each Task -- they must
    # still survive the round trip or a revision cannot rebuild the report.
    assert restored.anchors == {"task1": 2, "task2": 4, "task3": 6}


def test_create_makes_the_full_tree(tmp_path):
    store = RunStore.create("03", root=tmp_path)

    for d in (store.workspace, store.code_dir, store.shots_dir,
              store.report_dir, store.logs_dir):
        assert d.is_dir()
    assert store.run_id.endswith("_lab03")


def test_save_then_load(tmp_path):
    store = RunStore.create("03", root=tmp_path)
    store.save(_manifest())

    assert store.load().outcomes[0].task.id == "task1"


def test_atomic_write_leaves_no_partial_file(tmp_path, monkeypatch):
    """Simulate a crash BEFORE the replace lands.

    Note: an earlier version of this test faked the crash with a str subclass
    whose .encode raised -- which never fired, because TextIOWrapper.write does
    not call str.encode on the object. The test passed for the wrong reason and
    then failed for the right one. Patch the syscall, not the payload.
    """
    import os as os_mod

    target = tmp_path / "manifest.json"
    atomic_write_text(target, '{"a": 1}')

    def exploding_replace(*args, **kwargs):
        raise OSError("simulated crash before commit")

    monkeypatch.setattr(os_mod, "replace", exploding_replace)

    with pytest.raises(OSError):
        atomic_write_text(target, '{"b": 2}')

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}, "old content lost"
    assert not list(tmp_path.glob("*.tmp")), "temp file leaked"


def test_atomic_write_commits_when_it_succeeds(tmp_path):
    target = tmp_path / "manifest.json"
    atomic_write_text(target, '{"a": 1}')
    atomic_write_text(target, '{"b": 2}')

    assert json.loads(target.read_text(encoding="utf-8")) == {"b": 2}
    assert not list(tmp_path.glob("*.tmp"))


def test_open_requires_a_manifest(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        RunStore.open(tmp_path / "empty")


def test_latest_picks_the_newest_run(tmp_path):
    a = RunStore.create("01", root=tmp_path, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    b = RunStore.create("02", root=tmp_path, now=datetime(2026, 9, 2, tzinfo=timezone.utc))
    a.save(_manifest())
    b.save(_manifest())

    assert RunStore.latest(tmp_path).dir == b.dir


def test_latest_on_empty_root_is_none(tmp_path):
    assert RunStore.latest(tmp_path / "nothing") is None


def test_resuming_skips_settled_tasks():
    manifest = _manifest()

    assert completed_task_ids(manifest) == {"task1"}
    assert [t.id for t in pending_tasks(manifest)] == ["task2", "task3"]


def test_failed_tasks_count_as_settled():
    """A failed task consumed its budget and is in the report. Re-running it
    spends money to reach the same place."""
    manifest = _manifest()
    manifest.outcomes.append(
        TaskOutcome(task=manifest.spec.tasks[1], status="failed", attempts=3,
                    error="NameError")
    )

    assert completed_task_ids(manifest) == {"task1", "task2"}
    assert [t.id for t in pending_tasks(manifest)] == ["task3"]


def test_manifest_survives_a_real_reload_cycle(tmp_path):
    store = RunStore.create("03", root=tmp_path)
    manifest = _manifest()
    store.save(manifest)

    reopened = RunStore.open(store.dir).load()
    reopened.outcomes.append(
        TaskOutcome(task=reopened.spec.tasks[1], status="passed", attempts=2)
    )
    RunStore.open(store.dir).save(reopened)

    assert len(RunStore.open(store.dir).load().outcomes) == 2


# --- fields added after the first manifest was written ----------------------


def test_a_manifest_round_trips_the_per_task_accounting(tmp_path):
    from labsagent.models import LabSpec, RunManifest, Task, TaskOutcome
    from labsagent.runstore import RunStore

    store = RunStore.create("99", root=tmp_path / "runs")
    manifest = RunManifest(
        run_id="r",
        started_at=datetime.now(timezone.utc),
        spec=LabSpec(lab_number="99", title="T", tasks=[Task(id="task1", title="a", statement="b")]),
        outcomes=[
            TaskOutcome(
                task=Task(id="task1", title="a", statement="b"),
                status="passed",
                cost_usd=0.012,
                usage={"calls": 7},
                attempt_errors=["no successful run of task1.py"],
                stopped_reason="task",
                produced=[{"name": "out.csv", "bytes": 9}],
            )
        ],
        usage={"total": {"calls": 7}, "phases": {"solve": {"calls": 6}}},
    )
    store.save(manifest)
    loaded = store.load()

    outcome = loaded.outcomes[0]
    assert outcome.cost_usd == 0.012
    assert outcome.usage == {"calls": 7}
    assert outcome.attempt_errors == ["no successful run of task1.py"]
    assert outcome.stopped_reason == "task"
    assert outcome.produced == [{"name": "out.csv", "bytes": 9}]
    assert loaded.usage["phases"]["solve"]["calls"] == 6


def test_a_manifest_written_before_these_fields_existed_still_loads(tmp_path):
    """The `.get`-with-a-default rule, as an assertion. Resuming depends on it."""
    import json

    from labsagent.runstore import RunStore

    store = RunStore.create("99", root=tmp_path / "runs")
    store.manifest_path.write_text(
        json.dumps(
            {
                "run_id": "r",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "spec": {"lab_number": "99", "title": "T", "tasks": []},
                "outcomes": [
                    {
                        "task": {"id": "task1", "title": "a", "statement": "b"},
                        "status": "passed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = store.load()
    assert loaded.outcomes[0].cost_usd == 0.0
    assert loaded.outcomes[0].attempt_errors == []
    assert loaded.usage == {}


def test_reset_workspace_keeps_what_it_is_told_to(tmp_path):
    """`data_dir`'s docstring has always claimed this happens. Now it does."""
    from labsagent.runstore import RunStore

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sales.csv").write_text("a,b", encoding="utf-8")
    (ws / "scratch.py").write_text("x = 1", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "deep.txt").write_text("gone", encoding="utf-8")

    RunStore.reset_workspace(ws, keep={"sales.csv"})

    assert (ws / "sales.csv").exists(), "the dataset is not re-fetched"
    assert not (ws / "scratch.py").exists()
    assert not (ws / "sub").exists()


def test_reset_workspace_on_a_missing_directory_is_a_no_op(tmp_path):
    from labsagent.runstore import RunStore

    RunStore.reset_workspace(tmp_path / "nope", keep=set())
