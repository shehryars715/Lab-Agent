"""Event bus: fan-out, isolation from consumer failures, data-not-formatting."""

from __future__ import annotations

from labsagent import events as ev


def test_emitter_fans_out_to_every_consumer():
    a, b = ev.Recorder(), ev.Recorder()
    em = ev.Emitter(a, b)
    em.emit(ev.TaskStarted(task_id="task1", title="T", index=1, total=3))

    assert a.kinds == b.kinds == ["TaskStarted"]


def test_a_broken_consumer_never_breaks_the_run():
    """A failing progress bar is not a reason to lose a lab report."""
    def explode(event):
        raise RuntimeError("consumer bug")

    good = ev.Recorder()
    em = ev.Emitter(explode, good)
    em.emit(ev.RunStarted(run_id="r", lab_number="03", task_count=1))

    assert good.kinds == ["RunStarted"]
    assert em.errors and "consumer bug" in em.errors[0]


def test_subscribe_adds_a_consumer_later():
    em = ev.Emitter()
    rec = ev.Recorder()
    em.subscribe(rec)
    em.emit(ev.TaskFinished(task_id="task1", status="passed", attempts=1))

    assert rec.kinds == ["TaskFinished"]


def test_events_carry_data_not_formatting():
    e = ev.AttemptFailed(task_id="task1", attempt=2, error="NameError: x")

    assert e.error == "NameError: x"
    assert e.kind == "AttemptFailed"
    assert e.at is not None


def test_console_consumer_renders_without_raising(capsys):
    for event in (
        ev.RunStarted(run_id="r", lab_number="03", task_count=2),
        ev.IngestFinished(task_count=2, anchor_repairs=1),
        ev.TaskStarted(task_id="task1", title="T", index=1, total=2),
        ev.AttemptStarted(task_id="task1", attempt=1, max_attempts=3),
        ev.AttemptFailed(task_id="task1", attempt=1, error="boom"),
        ev.ArtifactWritten(task_id="task1", artifact="code", path="code/task1.py"),
        ev.TaskFinished(task_id="task1", status="passed", attempts=2, cost_usd=0.0004),
        ev.RunFinished(run_id="r", passed=1, failed=1, cost_usd=0.001),
    ):
        ev.console_consumer(event)

    out = capsys.readouterr().out
    assert "lab 03" in out and "1 passed, 1 failed" in out
