# Labs-Agent

Turn a programming lab manual into a finished submission — working code, terminal
screenshots, and a completed report — automatically.

Give it a Word document. It reads the tasks, writes a program for each, runs them,
captures the output, fills the answers into a copy of the manual, and packages
everything as a zip.

```bash
uv run python examples/solve_lab.py path/to/manual.docx
```

```
run 20260913-011204_lab03  lab 03  3 tasks

[1/3] task1: Sum of Two Numbers
    attempt 1/3
    code: runs/20260913-011204_lab03/code/task1.py
    screenshot: runs/20260913-011204_lab03/screenshots/task1_output.png
    -> ok after 1 attempt(s)  $0.000233
...
done: 3 passed, 0 failed, $0.001625
```

---

## How it works

1. **Reads the manual** — flattens the document into a numbered paragraph list,
   including text inside tables (which the standard Word API silently skips).
2. **Extracts the tasks** — a language model returns structured data: what each task
   asks, what test inputs it needs, and which paragraph the answer belongs under.
3. **Solves each task** — an agent writes a program, runs it in an isolated workspace,
   reads the output, and fixes it if it fails. Up to three attempts per task.
4. **Captures the output** — renders the terminal session as a PNG.
5. **Annotates the manual** — opens a *copy* and inserts `Code:` and `Output:` beneath
   each task. The original text is never regenerated, so formatting survives intact.
6. **Packages the submission** — report, loose code files, loose screenshots.

A task that can't be solved is marked failed *in the report*, with its error, and the
run continues. A partial report beats a crashed run.

---

## Two design decisions worth knowing

### The manual is the template

Rather than composing a new document, Labs-Agent edits a copy of the original. Headers,
numbering, styles and institutional formatting survive by construction because nothing
is recreated. A test asserts that every original paragraph still exists, verbatim, with
its original style.

This requires XML-level work — `python-docx` has no insert-after API — and three
non-obvious details are handled: sibling insertion order, paragraph references that stay
valid across mutations, and tasks wrapped in table cells (whose content is placed after
the table so every task is presented consistently).

### Screenshots are instrumented, not reconstructed

Programs that read input are a problem. Pipe answers to stdin and the captured output
reads:

```
Enter n: Enter m: Sum = 8
```

The typed values are gone. Reconstructing where they belong is guesswork that breaks
whenever prompts don't map one-to-one to inputs.

Instead, a shim wraps `input()` and echoes each value at the moment it is consumed:

```
PS C:\lab> python task1.py
Enter n: 5
Enter m: 3
Sum = 8
```

The transcript is correct by construction — the echo happens where the read happens, so
nothing is fabricated.

---

## Quick start

```bash
uv sync

cp .env.example .env        # then add your DeepSeek API key
```

```bash
# How an agent loop works — free, offline, no key required
uv run python examples/loop_anatomy.py

# Solve a single task, live
uv run python examples/solve_one_task.py

# Solve a whole lab and produce a submission zip
uv run python examples/solve_lab.py

# Test suite — 85 tests, fully offline, no API key
uv run pytest
```

Output is written to `runs/<timestamp>_lab<NN>/`:

```
manifest.json      what happened, per task, plus token usage and cost
code/              the generated solutions
screenshots/       rendered terminal output
report/            the annotated manual and the submission zip
logs/              the event stream
```

---

## Architecture

```
src/labsagent/
  ingest/          DOCX -> structured task data
  agent/           model, tools, prompt, sandboxed filesystem
  sandbox/         where code is written and executed
  capture/         execution -> terminal-styled PNG
  report/          in-place DOCX annotation
  package/         submission archive
  orchestrator.py  the multi-task loop: retries, isolation, recovery
  runstore.py      run directories, atomic manifests, resumability
  events.py        typed progress events
  usage.py         token and cost accounting
```

Two protocols define the extension points:

- **`Sandbox`** — `write_file` / `read_file` / `list_files` / `run`. A local subprocess
  backend ships today; a cloud backend slots in behind the same interface.
- **`ScreenshotBackend`** — `render(transcript) -> [png]`. The rendered-terminal backend
  ships today; a real window-capture backend implements the same protocol.

### Isolation

Each task runs in its own subdirectory, and the agent's filesystem access is confined to
it. The confinement layer does two jobs: it refuses paths that resolve outside the
workspace, and it maps the agent's virtual root (`/`) onto that workspace. Both matter —
the first prevents reading files it shouldn't, the second prevents it wasting turns
hunting for its own working directory.

### Progress events

The core emits typed events (`TaskStarted`, `AttemptFailed`, `ArtifactWritten`,
`TaskFinished`) rather than printing. Events carry data, never formatting. Two consumers
ship today — a terminal renderer and a log recorder — and a failing consumer can never
break a run.

---

## Stack

| | |
|---|---|
| Python | 3.14, managed with [uv](https://docs.astral.sh/uv/) |
| Agent harness | [deepagents](https://github.com/langchain-ai/deepagents) (LangChain / LangGraph) |
| Model | DeepSeek `deepseek-flash` via `langchain-deepseek` |
| Documents | `python-docx` + `lxml` |
| Images | `Pillow` |
| Tests | `pytest` — 85 tests, no network, no API key |

---

## Testing

The test suite never calls the API. The model is scripted (`tests/fakes.py`), so the loop
is exercised deterministically, for free, in about five seconds:

```bash
uv run pytest
```

Coverage includes the agent loop contract, sandbox conformance, the filesystem security
boundary, transcript fidelity, DOCX annotation (including that the original document is
never modified), cost arithmetic, run-store atomicity, and resumability.

The sample manual used by the examples is generated on demand from
`tests/fixtures/make_manual.py` — no binary fixtures are committed.

---

## Status

Working end to end. Roadmap: cloud sandbox backend, a dedicated explanation sub-agent,
evaluation harness, a web interface, and support for languages beyond Python.

## License

MIT
