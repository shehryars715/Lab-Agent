"""The eval set: fixtures, scoring, and the report.

`manuals.py`  declares the fixture manuals as data and renders them to .docx
`cases.py`    pairs each manual with hand-written goldens
`harness.py`  runs the pipeline over a case and scores it on `ran` and `matched`
`report.py`   aggregates samples into a table, keeping the spread visible
"""

from labsagent.evals.cases import EvalCase, build_cases, real_cases
from labsagent.evals.harness import SampleResult, TaskScore, run_case, run_sample
from labsagent.evals.report import CaseSummary, summarize

__all__ = [
    "CaseSummary",
    "EvalCase",
    "SampleResult",
    "TaskScore",
    "build_cases",
    "real_cases",
    "run_case",
    "run_sample",
    "summarize",
]
