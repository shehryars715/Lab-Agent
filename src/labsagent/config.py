"""Settings, read from `.env`. Every knob the harness has lives here."""

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

    # WHERE MODEL-WRITTEN PROGRAMS RUN. "local" is a subprocess on this
    # machine (development). "runner" sends each run to the runner sidecar
    # (runner/app.py), which starts a throwaway, network-less container that
    # sees only the task's own folder. The server sets "runner"; see compose.yaml.
    sandbox_backend: str = Field(default="local", alias="SANDBOX_BACKEND")
    runner_url: str = Field(default="http://runner:7000", alias="RUNNER_URL")
    runner_token: str = Field(default="", alias="RUNNER_TOKEN")
    # Where runs live. On the server this must be the SAME absolute path in
    # the app and the runner, because the runner asks the host's Docker to
    # mount it, and the host resolves the path on the host.
    runs_dir: Path = Field(default=PROJECT_ROOT / "runs", alias="LABSAGENT_RUNS_DIR")

    # How many labs may SOLVE at once (reading and questions never wait). One,
    # because a solve plus its program container is most of a 1 GB server.
    max_concurrent_solves: int = 1

    # Days a run and its uploads are kept (web/server/retention.py). 0 = keep
    # forever. The public server sets 30; leave it 0 anywhere whose history
    # you mean to keep.
    retention_days: int = 0

    # CREDITS PER ACCOUNT (public sign-up only; web/server/metering.py). 1
    # credit = $0.0001 (credits.py). Each account gets `monthly_credits` per
    # calendar month (UTC), no rollover. A run or follow-up holds at most
    # `max_hold_credits` (its ceiling, $0.01; owner's choice 2026-10-22,
    # ~1.5x the heaviest real lab seen) and needs `min_start_credits`
    # free to start. `daily_cap_usd` bounds real spend across ALL accounts.
    monthly_credits: int = 300
    max_hold_credits: int = 100
    min_start_credits: int = 10
    daily_cap_usd: float = 2.0

    model_name: str = "deepseek-flash"
    temperature: float = 0.0
    timeout_s: int = 30
    # Two, not three: an honest "blocked" is never retried at all, and a third
    # fresh-context attempt at the same failure was the costliest way to learn
    # nothing (the `impossible` eval spent 12 calls reaching the same wall).
    max_retries_per_task: int = 2

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

    # A BASIC task's first attempt (ingest labels it) runs without thinking and
    # under a tighter turn cap -- write, run, record is 3 turns, so 12 leaves
    # room for a few fixes and not for polishing. A failure retries as standard.
    basic_max_turns: int = 12
    basic_reasoning_effort: str | None = "none"

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
