"""The solve queue: one at a time, first come first served, honest places."""

from __future__ import annotations

import threading
import time

from web.server.slots import SolveSlots


def _start(slots, name, log, places, release):
    def body():
        with slots.hold(on_wait=lambda ahead: places.setdefault(name, []).append(ahead)):
            log.append(f"start {name}")
            release[name].wait(5)
            log.append(f"end {name}")

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_free_slot_never_reports_a_queue():
    slots, told = SolveSlots(1), []
    with slots.hold(on_wait=told.append):
        assert slots.stats() == {"capacity": 1, "solving": 1, "waiting": 0}
    assert told == []
    assert slots.stats()["solving"] == 0


def test_labs_solve_one_at_a_time_in_arrival_order():
    slots, log, places = SolveSlots(1), [], {}
    release = {n: threading.Event() for n in "abc"}
    threads = [_start(slots, "a", log, places, release)]
    assert _until(lambda: log == ["start a"])
    threads.append(_start(slots, "b", log, places, release))
    assert _until(lambda: slots.stats()["waiting"] == 1)
    threads.append(_start(slots, "c", log, places, release))
    assert _until(lambda: slots.stats()["waiting"] == 2)

    assert places == {"b": [1], "c": [2]}  # b waits behind a; c behind a and b
    for name in "abc":
        release[name].set()
        assert _until(lambda n=name: f"end {n}" in log)
    for thread in threads:
        thread.join(5)

    assert log == ["start a", "end a", "start b", "end b", "start c", "end c"]
    assert places["c"] == [2, 1]  # told again when b started
    assert slots.stats() == {"capacity": 1, "solving": 0, "waiting": 0}


def test_a_failing_solve_still_frees_its_slot():
    slots = SolveSlots(1)
    try:
        with slots.hold():
            raise RuntimeError("the solve crashed")
    except RuntimeError:
        pass
    assert slots.stats()["solving"] == 0
    with slots.hold():
        pass
