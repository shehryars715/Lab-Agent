"""Stopping a run that is spending more than it is worth.

WHY A CALLBACK AND NOT A SUM AFTERWARDS. `solve_task` used to add up usage from
`result["messages"]` once the agent returned. That works right up to the case
you need it for: an attempt that is aborted never returns, so a post-hoc sum
loses precisely the attempt that burned the money. The ceiling has to be
checked as the tokens are billed, which means inside the callback.

WHY TWO CEILINGS AND NOT ONE. A per-task ceiling stops one pathological task
from eating the run; a per-run ceiling stops five merely-expensive tasks from
adding up to the same thing. Neither is a target -- healthy labs finish three
tasks for about a fifth of a cent, so both defaults sit roughly an order of
magnitude above anything normal. They exist to convert an unbounded tail into a
recorded, readable stop.

THE TURN CAP IS SEPARATE AND LIVES IN `agent/build.py`, because it is a
property of the graph rather than of the money. It is also not sufficient on its
own: thirty turns across three attempts and five tasks is still 450 calls. The
cost ceiling is the binding constraint; the turn cap is what stops a single
attempt spinning.
"""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler


class BudgetExceeded(Exception):
    """Raised from inside the agent loop when a ceiling is crossed."""

    def __init__(self, scope: str, spent: float, cap: float) -> None:
        super().__init__(
            f"{scope} budget exhausted: ${spent:.4f} of ${cap:.4f}"
        )
        self.scope = scope  # "task" | "run"
        self.spent = spent
        self.cap = cap


class LiveUsage(BaseCallbackHandler):
    """Accumulates usage as it is billed, and stops the loop at the ceiling.

    `raise_error = True` is load-bearing and easy to lose: LangChain swallows
    exceptions raised by a callback handler unless it is set, so without it the
    ceiling would be computed correctly and then ignored.
    """

    raise_error = True

    def __init__(
        self,
        phase,
        run_usage=None,
        task_cap: float = 0.0,
        run_cap: float = 0.0,
        task_start: float | None = None,
    ):
        self.phase = phase
        self.run_usage = run_usage
        self.task_cap = task_cap
        self.run_cap = run_cap
        #: The run's spend when the TASK began. The caller passes it, because
        #: this handler is built once per attempt: defaulting to "now" made the
        #: task ceiling a per-attempt ceiling, so two attempts could spend twice.
        self.task_start = self._run_total() if task_start is None else task_start

    def _run_total(self) -> float:
        return self.run_usage.total.cost_usd if self.run_usage is not None else 0.0

    @property
    def task_spent(self) -> float:
        return max(0.0, self._run_total() - self.task_start)

    def on_llm_end(self, response, **kwargs) -> None:
        for generations in getattr(response, "generations", []) or []:
            for generation in generations or []:
                message = getattr(generation, "message", None)
                if message is not None:
                    self.phase.add_message(message)

        if self.run_cap and self._run_total() >= self.run_cap:
            raise BudgetExceeded("run", self._run_total(), self.run_cap)
        if self.task_cap and self.task_spent >= self.task_cap:
            raise BudgetExceeded("task", self.task_spent, self.task_cap)
