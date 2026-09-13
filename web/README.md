# Labs-Agent — web interface

A chat interface for the pipeline. Attach a manual, watch it work, answer one
question partway through, get the submission. Then ask for changes and it
re-runs only what your change affects.

```
web/
  server/          FastAPI + the composed pipeline
    app.py         HTTP surface: upload, stream, answer, download, revise, history
    jobs.py        the run's event log and its pause gate
    pipeline.py    manual in -> artifacts out, with a pause in the middle
    briefing.py    the pre-run read: what to ask, and how the cover should look
    revise.py      feedback -> which tasks to redo
    trace.py       tool calls and model narration -> activity frames
    history.py     past runs, read back from disk
  ui/              React + Vite front end
    src/lib/       the transcript state machine (pure, no React)
    src/hooks/     the React binding around it
    src/components/
    test/          reducer tests, no browser needed
  smoke_test.py    end-to-end test, live, ~$0.002
```

---

## Running it

```bash
# once
uv pip install -r web/requirements.txt
cd web/ui && npm install
```

**Development** — hot reload on both sides. Vite proxies `/api` to FastAPI, so
the browser sees one origin and no CORS is needed anywhere:

```bash
uv run uvicorn web.server.app:app --port 8000        # terminal 1
cd web/ui && npm run dev                             # terminal 2 -> :5173
```

**Production-ish** — one process. FastAPI serves the built assets if `dist/`
exists:

```bash
cd web/ui && npm run build
cd ../.. && uv run uvicorn web.server.app:app --port 8000
# open http://127.0.0.1:8000
```

```bash
cd web/ui && npm test                # 14 transcript tests, ~100ms, no browser
uv run python web/smoke_test.py      # 40 end-to-end assertions, live
uv run pytest                        # the core suite, offline
```

Binds to `127.0.0.1`. No auth, no accounts, no multi-tenancy — `PLAN.md` §7
says not to build those, and it is right: they are the single most likely thing
to stall this project.

---

## The decisions worth understanding

### 1. The job keeps a log, not a queue

The obvious design is a queue: the worker writes progress, the browser reads it.
It breaks the first time a connection drops, because a queue is consumed by
definition and there is no way to ask for what you missed.

A **log** is an append-only list with sequence numbers. A consumer says "give me
everything after 41" and gets it, whether it is connecting for the first time,
reconnecting, or slower than the producer. Replay and streaming stop being two
features and become one — the same shape as Kafka, Redis Streams, and SSE's own
`Last-Event-ID`, which `app.py` honours.

The cost is memory held until the job is evicted, which is the right trade for a
few hundred dicts.

### 2. The pause comes BEFORE the code

The first version paused after solving, which meant every answer could only
reach the cover page: the money was spent and the programs were already
written. It now pauses between ingest and solve, so an answer changes what gets
built. `smoke_test.py` asserts this positionally — that `needs_input` arrives
before the first `TaskStarted` — because that ordering is the entire point.

### 3. The agent decides what to ask

`briefing.py` reads the extracted tasks and proposes what it genuinely cannot
decide: which dataset, which library, what output format. Each question carries
a `reason`, which is shown to the user, because "why do you need this" is the
difference between a question that looks intelligent and one that looks like a
form.

Returning **zero questions is correct and common** — most labs are fully
specified — and the prompt says so explicitly. A briefing that invents a
question to look useful is the failure mode, not the feature.

One structured call (~$0.0002) produces both the questions and the cover
design, rather than two calls to read the same manual twice.

### 4. A pause is a condition wait

```
worker thread ──publish(needs_input)──▶ browser
browser ──POST /answers──▶ notify() → the same thread continues
```

The worker parks on a `Condition`; the HTTP request carrying the answer wakes
it. No CPU while parked, no polling. If nobody answers in ten minutes the run
continues with placeholders rather than hanging forever.

This is the same shape as a LangGraph interrupt. In a chat it is also why
asking more than once is acceptable: the question is a card in a conversation,
not a modal that blocks the screen.

### 5. The agent's tool calls are streamed without touching the core

`orchestrator.solve_task` calls `agent.invoke(...)` with no hook parameter, so
the seam used is the one that already exists: the `model` argument, which
LangChain Runnables propagate configuration down from.

`register_configure_hook` is that mechanism made global — the supported
extension point `tracing_v2_enabled` uses to attach the LangSmith tracer to
every run in a process. A `ContextVar` holds the handler, the `with` block in
`pipeline.run_job` scopes it to one run on one thread, and out comes the
agent's narration and its tool calls interleaved:

```
I'll write a short program that prompts for two integers and prints their sum.
  writing task2.py
  running task2.py
  exit 1 — fixing
  running task2.py
  exit 0
Task complete: task2.py reads two integers and prints their sum.
```

Two properties make this safe from outside the library. The `ContextVar` is
scoped to the worker thread, so concurrent runs cannot see each other. And
`BaseCallbackHandler.raise_error` defaults to False, so an exception in here is
logged and dropped rather than thrown into the agent's loop — the same contract
`events.Emitter` states for its consumers.

### 6. A revision reuses the core's resumability

`runstore.py` already documented the mechanism: *"Delete its outcome from the
manifest to force a retry."* That is the whole implementation. Drop the targeted
tasks' outcomes, call `run_lab(..., resume=True)`, and it re-solves exactly
those and appends. No parallel re-run path exists because the core already had
one.

Two steps are load-bearing and easy to miss, and both are explained at length in
`revise.py`: the manifest's spec must be rewritten *before* the resume (because
`run_lab` reads the task list from `manifest.spec` and ignores the argument, so
new feedback passed as an argument would be silently discarded), and only the
targeted outcomes may be dropped (or you pay for a whole lab to change one
task).

### 7. Extra instructions go into the spec, not the system prompt

Free text is folded into each `Task.statement` with `dataclasses.replace`,
producing a *new* `LabSpec`. Three reasons:

- **`Task` is frozen** — the codebase's way of saying a parsed spec is a fact.
- **`runstore.py` writes that spec into the manifest.** The record has to be
  what was actually asked, and `pending_tasks()` reads it for resumption — if
  the text were injected later, a resumed run would ask a different question
  than the original.
- **`prompts.py` documents `SOLVER_PROMPT` as byte-stable.** It is resent on
  every turn of every task, so a changed prefix turns cache *hits* into cache
  *misses* at 50× the price. The user message is already per-task and uncached.

The narration instruction lives here too rather than in `prompts.py`, now that
the core is editable — because the CLI narrates into nothing, and would pay
output tokens for prose nobody reads.

---

## Two bugs worth remembering

**The reducer's state became an array.** `pushTicker` returned the ticker
*array*, and two callers did `return pushTicker(...)`. `RunStarted` fires before
`TaskStarted`, so `state.tasks` became `undefined` and the next event threw
`Cannot read properties of undefined (reading 'task1')`. The other callers did
`.ticker` on the array, which is `undefined` and silently wiped the ticker. Two
bugs, one mismatch, and JavaScript saw neither. The rule — **a helper that
builds part of a state returns the state, not the part.**

**The sequence number across a revision.** A revision reuses the same job, so
the server keeps counting sequence numbers from where it left off; a new upload
starts a new job counting from 1. Reset the high-water mark on a revision and
the replay guard accepts the whole log a second time, duplicating the
transcript. Keep it on a new job and the guard drops every frame, producing a
chat that connects and then shows nothing.

Both are the same lesson from opposite directions: **a cursor is only
meaningful next to the thing it indexes.** Both are regression tests in
`ui/test/thread.test.mjs`, found by driving a real browser, which is a slow way
to find something a four-line test catches.

---

## Not built, on purpose

No queue of concurrent runs (one user, one machine), no persistence beyond the
run directory (the `JobRegistry` is in memory and bounded, so a revision is
unavailable after a server restart), and no auth.
