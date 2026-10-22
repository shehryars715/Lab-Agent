"""The solve queue: at most N labs solve at once, the rest wait their turn.

WHY ONLY THE SOLVE WAITS. Reading a manual and asking the questions are
cheap and need the student's attention, so they start the moment a lab is
sent. Solving is the expensive part: the model loop plus a program container
of up to 384 MB, on a 1 GB server whose app already uses about 530 MB. Two
solves at once is how the server runs out of memory, so solves take a slot.

WHY FIRST COME, FIRST SERVED. A plain semaphore wakes waiters in no promised
order, so a lab could wait behind ones sent after it. Each waiter takes a
ticket; only the oldest ticket may take a free slot, and everyone behind it
knows exactly how many are ahead -- which is what the run panel shows.

`ahead` counts the waiters before you plus one, the lab that is solving. With
one slot that is exactly "how many labs finish before mine starts".
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from labsagent.config import load_settings


class SolveSlots:
    def __init__(self, capacity: int = 1) -> None:
        self.capacity = max(1, int(capacity))
        self._cv = threading.Condition()
        self._waiting: deque[object] = deque()
        self._active = 0

    def _ahead(self, ticket: object) -> int:
        return self._waiting.index(ticket) + 1

    @contextmanager
    def hold(self, on_wait: Callable[[int], None] | None = None) -> Iterator[None]:
        """Wait for a slot (FIFO), run the block, give the slot back.

        `on_wait(ahead)` is called when the lab first has to wait and every
        time its place in line changes -- never when a slot is free at once,
        so an idle server shows no queue at all.
        """
        ticket = object()
        with self._cv:
            self._waiting.append(ticket)
            told = None
            while not (self._active < self.capacity and self._waiting[0] is ticket):
                ahead = self._ahead(ticket)
                if on_wait is not None and ahead != told:
                    told = ahead
                    # Called with the lock held; publishing an event is a
                    # quick in-memory append, never a wait.
                    on_wait(ahead)
                self._cv.wait(timeout=5)
            self._waiting.popleft()
            self._active += 1
            # Everyone behind just moved up one. Without this they learned it
            # only if they happened to wake after us, so a lab could keep
            # showing "2 ahead" until the next lab finished.
            self._cv.notify_all()
        try:
            yield
        finally:
            with self._cv:
                self._active -= 1
                self._cv.notify_all()

    def stats(self) -> dict[str, int]:
        with self._cv:
            return {
                "capacity": self.capacity,
                "solving": self._active,
                "waiting": len(self._waiting),
            }


SOLVE_SLOTS = SolveSlots(load_settings().max_concurrent_solves)
