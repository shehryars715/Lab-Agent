"""RawManual -> LabSpec via one structured LLM call.

STRUCTURED OUTPUT is the concept here. Instead of asking for prose and parsing
it with regexes, we pin the response to a schema.

There are three mechanisms and they are NOT equivalent:

  function_calling  forced tool_choice; model must call a schema-shaped tool
  json_schema       response_format with server-side schema validation
  json_mode         response_format json_object -- valid JSON only, no schema

DeepSeek V4.1 runs in thinking mode and rejects a forced tool_choice, so the
first two fail here. json_mode works, which means schema enforcement moves
CLIENT-SIDE: the schema goes in the prompt and Pydantic is the real validator.
Know which of the three you are actually getting -- "structured output" is not
one guarantee, it is three different ones.

THE ANCHOR PROBLEM. We ask the model for `anchor_idx`, a paragraph index. Models
are unreliable at positional counting: they will confidently return 7 when the
answer is 8. But they are very good at quoting text they can see.

So we ask for BOTH: the index, and a short quote of the paragraph being anchored
to. Then we reconcile -- if the quote does not match the paragraph at that
index, we search for the quote and trust it instead. The redundant field costs a
handful of tokens and converts a silent misplacement into a self-correcting one.

The general technique: when a model must produce something it is weak at, have
it also produce something it is strong at, and verify one against the other.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel, Field

from labsagent.errors import SpecError
from labsagent.ingest.docx_reader import RawManual
from labsagent.models import LabSpec, Task
from labsagent.usage import Usage

EXTRACTION_PROMPT = """You are reading a university programming lab manual.

Each line below is one paragraph of the document, prefixed with its index in
square brackets, e.g. "[7]". Style names appear in brackets after the index when
the paragraph is not body text.

Identify every TASK the student must implement. Ignore objectives, headers,
titles and closing remarks -- only tasks that require writing a program.

For each task report:

- task_number: 1, 2, 3... in document order.
- title: the task's heading, or a short descriptive title if it has none.
- statement: the COMPLETE requirement text, verbatim where possible, including
  any prompt strings, expected output format and sample runs the task mentions.
  This is the only thing the solver will see, so it must be self-contained.
- sample_inputs: the exact input values a test run should use, in order, as
  strings. Derive them from any sample run the manual gives. If the manual gives
  no values, choose simple sensible ones that satisfy the task.
- wants_explanation: true only if the task explicitly asks the student to
  explain, discuss, describe or comment on their approach.
- anchor_idx: the index of the LAST paragraph belonging to this task -- the
  paragraph the student's code and output should be inserted after. This is the
  final paragraph of the task's description, NOT the heading, and NOT the next
  task's heading.
- anchor_quote: the first 40 characters of the paragraph at anchor_idx, copied
  exactly. This is used to verify anchor_idx, so it must be an exact copy.

Also report the lab number, the lab title, and the course code if present.

Return ONLY a JSON object matching this schema exactly:

{schema}

Document:

{document}
"""


class ExtractedTask(BaseModel):
    task_number: int = Field(description="1-based position in document order")
    title: str
    statement: str = Field(description="complete, self-contained requirement text")
    sample_inputs: list[str] = Field(default_factory=list)
    wants_explanation: bool = False
    anchor_idx: int = Field(description="index of the task's LAST paragraph")
    anchor_quote: str = Field(description="first 40 chars at anchor_idx, verbatim")


class ExtractedLab(BaseModel):
    lab_number: str
    title: str
    course: str | None = None
    tasks: list[ExtractedTask]


@dataclass
class AnchorRepair:
    task_id: str
    claimed: int
    corrected: int
    reason: str


def _reconcile_anchor(
    extracted: ExtractedTask, manual: RawManual
) -> tuple[int, AnchorRepair | None]:
    """Trust the quote over the index. Models quote well and count badly."""
    paragraphs = manual.paragraphs
    quote = (extracted.anchor_quote or "").strip()
    task_id = f"task{extracted.task_number}"

    def matches(idx: int) -> bool:
        if not (0 <= idx < len(paragraphs)):
            return False
        return paragraphs[idx].text.strip().startswith(quote[:30]) if quote else False

    if matches(extracted.anchor_idx):
        return extracted.anchor_idx, None

    if not (0 <= extracted.anchor_idx < len(paragraphs)):
        reason = f"index {extracted.anchor_idx} out of range"
    else:
        reason = "quote does not match the paragraph at that index"

    if quote:
        # Exact prefix match anywhere in the document.
        for p in paragraphs:
            if p.text.strip().startswith(quote[:30]):
                return p.idx, AnchorRepair(task_id, extracted.anchor_idx, p.idx, reason)

        # Fall back to closest fuzzy match.
        texts = [p.text.strip() for p in paragraphs]
        close = difflib.get_close_matches(quote, texts, n=1, cutoff=0.6)
        if close:
            idx = texts.index(close[0])
            return idx, AnchorRepair(task_id, extracted.anchor_idx, idx, reason + " (fuzzy)")

    if 0 <= extracted.anchor_idx < len(paragraphs):
        return extracted.anchor_idx, None

    raise SpecError(
        f"{task_id}: anchor_idx {extracted.anchor_idx} is out of range and its "
        f"quote {quote[:40]!r} matches no paragraph"
    )


def to_labspec(extracted: ExtractedLab, manual: RawManual) -> tuple[LabSpec, list[AnchorRepair]]:
    tasks: list[Task] = []
    repairs: list[AnchorRepair] = []

    for item in sorted(extracted.tasks, key=lambda t: t.task_number):
        anchor, repair = _reconcile_anchor(item, manual)
        if repair:
            repairs.append(repair)
        tasks.append(
            Task(
                id=f"task{item.task_number}",
                title=item.title,
                statement=item.statement,
                sample_inputs=list(item.sample_inputs),
                anchor_idx=anchor,
                wants_explanation=item.wants_explanation,
            )
        )

    if not tasks:
        raise SpecError(f"no tasks found in {manual.path.name}")

    spec = LabSpec(
        lab_number=extracted.lab_number,
        title=extracted.title,
        course=extracted.course,
        tasks=tasks,
        skipped_images=list(manual.image_names),
    )
    return spec, repairs


SCHEMA_HINT = """{
  "lab_number": "03",
  "title": "string",
  "course": "string or null",
  "tasks": [
    {
      "task_number": 1,
      "title": "string",
      "statement": "string",
      "sample_inputs": ["5", "3"],
      "wants_explanation": false,
      "anchor_idx": 8,
      "anchor_quote": "first 40 chars of the paragraph at anchor_idx"
    }
  ]
}"""


INGEST_ATTEMPTS = 3


def extract_labspec(
    manual: RawManual, model
) -> tuple[LabSpec, list[AnchorRepair], Usage]:
    """One constrained call.

    NOTE ON METHOD. DeepSeek V4.1 runs in thinking mode and rejects a forced
    `tool_choice`, so both `function_calling` and `json_schema` fail with
    "Thinking mode does not support this tool_choice". `json_mode` works, but it
    only guarantees SYNTACTICALLY VALID JSON -- schema conformance is not
    enforced server-side.

    So enforcement moves to us: the schema goes in the prompt, and Pydantic is
    the real validator. That is the trade with json_mode everywhere, not just
    here -- know which of the three you are getting.
    """
    structured = model.with_structured_output(ExtractedLab, method="json_mode")
    prompt = EXTRACTION_PROMPT.format(
        schema=SCHEMA_HINT, document=manual.as_numbered_text()
    )

    # with_structured_output swallows the AIMessage, so usage_metadata never
    # reaches the caller. A callback handler is the supported way to observe it
    # -- do NOT hardcode a previously measured figure, which silently goes stale
    # and then lies in your cost report.
    handler = UsageMetadataCallbackHandler()

    # RETRY, because json_mode has no server-side schema enforcement. Observed
    # in the wild: the model answered with the literal response_format object,
    # {"type": "json_object"}, which is valid JSON and wrong. Ingest is a single
    # call that gates the entire run, so one bad sample must not be terminal --
    # the cheapest fix for a stochastic failure is another sample.
    last: Exception | None = None
    for attempt in range(1, INGEST_ATTEMPTS + 1):
        try:
            extracted = structured.invoke(prompt, config={"callbacks": [handler]})
        except Exception as exc:  # noqa: BLE001 - classified below, then re-raised
            last = exc
            continue
        if extracted.tasks:
            spec, repairs = to_labspec(extracted, manual)
            return spec, repairs, _usage_from_handler(handler)
        # Well-formed but empty is still a failed extraction, not a lab with
        # zero tasks -- treat it as a retryable miss rather than shipping it.
        last = SpecError(f"attempt {attempt} returned no tasks")

    raise SpecError(f"extraction failed for {manual.path.name}: {last}") from last


def _usage_from_handler(handler) -> Usage:
    """Flatten the handler's per-model dict into one Usage."""
    total = Usage()
    for model_name, meta in (handler.usage_metadata or {}).items():
        total.model = model_name
        total.calls += 1
        total.input_tokens += meta.get("input_tokens", 0)
        total.output_tokens += meta.get("output_tokens", 0)
        total.cached_tokens += (meta.get("input_token_details") or {}).get("cache_read", 0)
    return total
