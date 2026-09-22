"""Settings. Deliberately minimal -- only what Phase 1 needs.

Expanded in Phase 3 when the run store and retry policy arrive. Building the
full labsagent.toml schema now would be speculative.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    # Optional. Only a lab that says "download the dataset from Kaggle" needs
    # these, and the failure without them is a sentence telling you to add them
    # -- not a crash, and not a silently wrong run on invented data.
    kaggle_username: str = Field(default="", alias="KAGGLE_USERNAME")
    kaggle_key: str = Field(default="", alias="KAGGLE_KEY")
    #: Per dataset file. Turns "you attached the wrong thing" into a fast error
    #: rather than a full disk, and bounds the per-task copy.
    max_dataset_mb: int = 100

    # A workbook is converted to CSV once, at acquire time, rather than parsed
    # again by every task and every attempt -- and the .py that gets handed in
    # then carries `pd.read_csv`, which runs anywhere pandas does, instead of
    # `pd.read_excel`, which needs an engine the student may not have. Turn it
    # off only to debug the conversion itself; the workbook is still readable.
    convert_excel_to_csv: bool = True

    model_name: str = "deepseek-flash"
    temperature: float = 0.0
    timeout_s: int = 30
    max_retries_per_task: int = 3

    # CIRCUIT BREAKERS, NOT TARGETS. Healthy labs run 5-6 model calls and about
    # $0.0007 per task, so these sit roughly an order of magnitude above normal
    # and should never fire on a run that is going well. They exist because
    # nothing else bounded an attempt: deepagents defaults `recursion_limit` to
    # 9,999, and `max_retries_per_task` bounds attempts rather than turns
    # inside one. A single attempt once ran seven minutes unchecked, and the
    # retry budget then bought two more of them.
    #
    # The turn cap alone is not enough -- 30 turns x 3 attempts x 5 tasks is
    # still 450 calls -- so the cost ceiling is the binding constraint and the
    # knob to reach for first.
    max_turns_per_attempt: int = 30
    max_cost_per_task_usd: float = 0.02
    max_cost_per_run_usd: float = 0.15

    # A task's data products are copied into the workspaces of the tasks that
    # reference it. Capped because the point is to stop later tasks hunting for
    # a file, not to duplicate a 40 MB intermediate into every directory -- one
    # run held the same CSV three times and came to 188 MB.
    handoff_max_mb: int = 50

    # A retry starts from a clean directory. Without this an attempt inherits
    # the previous one's half-finished intermediates, which is how a third
    # attempt once "passed" in 22 seconds off stale files and reported a
    # different number than the attempt that wrote them. Datasets and
    # handed-forward files survive the reset, so nothing is re-fetched.
    wipe_workspace_between_attempts: bool = True

    # Ship only the tools this job needs. deepagents offers ls/glob/grep/delete/
    # edit_file and a subagent `task` tool; solving a one-file lab task needs
    # none of them, and every unused schema is resent on every turn of every
    # task. Measured: 3,271 -> 1,369 tokens of fixed prefix per call.
    lean_tools: bool = True

    # DeepSeek V4.1 thinks by default, and reasoning tokens are billed as output
    # at 2x the cache-miss input rate while never appearing in the next request.
    # "none" disables thinking. Left off by default because it is a QUALITY
    # trade-off, not a free win -- measure it on your own labs before trusting it.
    reasoning_effort: str | None = None

    # Reasoning is worth paying for where the model must DECIDE something, and
    # wasted where it must merely TRANSCRIBE. Ingest is the second kind: copy the
    # task text out of a document into a fixed schema. Measured on Lab 10,
    # thinking cost 8,554 output tokens there against 766 without, for the same
    # five tasks and the same anchors -- so this defaults to off while the solver
    # keeps its reasoning. Per-phase is the right granularity for this knob;
    # one global switch would force a bad trade in one direction or the other.
    ingest_reasoning_effort: str | None = "none"

    # Explaining is the same KIND of work as ingest: the program already exists
    # and already works, so the model is describing, not deciding. By the rule
    # above that makes reasoning wasted spend, and the explanation is the one
    # place where a longer, more deliberated answer is actively worse -- the
    # target is two plain sentences, not an essay.
    explain_reasoning_effort: str | None = "none"

    @property
    def configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def max_dataset_bytes(self) -> int:
        return self.max_dataset_mb * 1024 * 1024

    @property
    def kaggle_configured(self) -> bool:
        return bool(self.kaggle_username and self.kaggle_key)


def load_settings() -> Settings:
    return Settings()
