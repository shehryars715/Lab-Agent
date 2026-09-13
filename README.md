# Labs-Agent

Turn a programming lab manual into a finished submission — working code, terminal
screenshots, and a completed report — automatically.

Give it a Word document. It reads the tasks, writes a program for each, runs them,
captures the output, fills the answers into a copy of the manual, and packages
everything as a zip.

```bash
uv run python examples/solve_lab.py path/to/manual.docx   # the CLI
```

Or use it in a browser — a chat interface where you attach the manual, answer one
question partway through, and download the result:

```bash
cd web/ui && npm install && npm run build && cd ../..
uv run uvicorn web.server.app:app --port 8000     # then open http://127.0.0.1:8000
```

See [`web/README.md`](web/README.md) for the design reasoning behind it.

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
6. **Adds a cover sheet** — course, section, instructor, lab engineer and date are read
   straight out of the manual's own front matter; only your name and roll number are
   asked for, once, then remembered.
7. **Packages the submission** — report, loose code files, loose screenshots.

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

### Only ask for what the document can't tell you

A cover sheet needs course, section, date, instructor, lab engineer, name and roll
number. Six of those are already printed in the manual's front matter, so they are
matched out of it — by label where labelled, by shape otherwise — at zero cost and with
no model call. A field that isn't found comes back empty rather than guessed.

That leaves two questions, asked once and cached. The measure of this kind of feature is
how rarely it has to speak.

### Ask before the work, not after

The web interface pauses once per run, between reading the manual and writing any code —
so an answer changes what gets built rather than only what the cover page says. What it
asks is not a fixed form. The model reads the extracted tasks and proposes what it
genuinely cannot decide:

```
Q: Which dataset file should I load?
   why: Determines whether I read a CSV path or generate synthetic data.
Q: Which regression model should I fit?
   why: Changes the library calls and the coefficients I report.
```

Returning **zero questions is the correct answer for a well-specified lab**, and the
prompt says so explicitly. A briefing that invents a question to look useful is the
failure mode, not the feature. One structured call produces both the questions and the
cover design, for about $0.0002.

---

## Measuring before optimising

`probe.py` wraps the point where the request dict is built, so it sees exactly what goes
on the wire — the system prompt after middleware rewrites it, the full tool schemas after
binding, every accumulated message. tiktoken isn't DeepSeek's tokenizer, so the estimate
is rescaled by the true `input_tokens` the API reports back for that same payload: an
approximate tokenizer gives real numbers once you measure its error.

On one five-task lab that turned a $0.031 run into a $0.0095 run, unchanged in output:

| | Before | After |
|---|---|---|
| Cost | $0.0313 | **$0.0095** |
| Wall clock | 238 s | **125 s** |
| Billable (cache-miss) input | 118,580 | **9,619** |

Three things were paying for nothing:

- **Images read as text.** The agent called `read_file` on matplotlib PNGs. Each returned
  ~33,000 tokens of mojibake, and because context accumulates, each was resent on every
  later turn of that task — 70% of all tool-result tokens, for zero information. Binary
  reads are now refused at the confinement boundary. A tool that returns text into a
  context window needs a type check and a size ceiling, because the caller deciding what
  to read is a language model.
- **Ten tools shipped, three used.** Unused schemas are resent every turn, and the mere
  presence of `ls` and `glob` invited orientation calls that produced yet more context.
  Trimmed to what the job needs: 2,909 → 1,007 tokens of fixed prefix per call.
- **Reasoning tokens.** Billed as output at 2× the cache-miss input rate, and never
  echoed back, so they are invisible to input attribution — one turn generated 2,628
  output tokens of which 120 survived. Switchable off, off by default: it is a quality
  trade-off, not a free win.

Attribution is the point. A per-phase total says a run cost three cents; it cannot tell
you that two of them went on reading a picture of a graph.

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

# Test suite — 171 tests, fully offline, no API key
uv run pytest
```

The web interface needs three extra packages and a Node toolchain, kept out of
`pyproject.toml` so a CLI-only user never installs a server they don't run:

```bash
uv pip install -r web/requirements.txt
cd web/ui && npm install && npm run build && cd ../..
uv run uvicorn web.server.app:app --port 8000

cd web/ui && npm test             # 14 reducer tests, no browser
uv run python web/smoke_test.py   # 40 end-to-end assertions, live, ~$0.002
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
  report/          in-place DOCX annotation, and four cover layouts
  package/         submission archive
  orchestrator.py  the multi-task loop: retries, isolation, recovery
  runstore.py      run directories, atomic manifests, resumability
  events.py        typed progress events
  usage.py         token and cost accounting
  probe.py         per-call token attribution: where the context actually went
  profile.py       student identity, asked once and cached

web/
  server/          FastAPI: the event log, the pause gate, the composed pipeline
  ui/              React chat interface; its reducer is pure and tested without a browser
```

**Nothing in `src/labsagent/` was modified to build the web layer** except the cover
layouts. That is what `events.py` was written for — two phases before it had a second
consumer.

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
| Web | FastAPI + uvicorn, SSE; React 19 + Vite |
| Tests | `pytest` — 171 tests, no network, no API key |

---

## Testing

The test suite never calls the API. The model is scripted (`tests/fakes.py`), so the loop
is exercised deterministically, for free, in about six seconds:

```bash
uv run pytest
```

Coverage includes the agent loop contract, sandbox conformance, the filesystem security
boundary, transcript fidelity, DOCX annotation (including that the original document is
never modified), cost arithmetic, run-store atomicity, and resumability.

The sample manual used by the examples is generated on demand from
`tests/fixtures/make_manual.py` — no binary fixtures are committed.

The web layer has two more suites. The chat's transcript state machine is a pure function
of the event stream, so it is tested in ~100ms with no browser and no DOM
(`cd web/ui && npm test`). `web/smoke_test.py` is the opposite: it drives a real run
against a live server, answers the mid-run pause, downloads every artifact and asserts on
the bytes — including that the name typed into the browser reaches the cover page of the
Word document, which is the one thing a mocked test cannot prove.

---

## Status

Working end to end, from the command line and in a browser. Roadmap: cloud sandbox
backend, a dedicated explanation sub-agent, an evaluation harness, and support for
languages beyond Python.

### Scope

This assumes the agent *writes* the code and the deliverable is a Word report plus a zip.
That assumption was checked against the real course material in this repo — the DS311
Data Mining manual, where the code is already given and the deliverable is a notebook on
LMS — and **confirmed as the right default**: labs of that second shape are rare. The
notebook path is not planned.

If that ever changes, the reusable half is the sandbox, run store, event system, cost
tracking, orchestration and in-place document editing. The parts that would need
replacing are the input-echo shim, the write-from-scratch solver prompt, and the
plain-text screenshot rendering.

## License

MIT
