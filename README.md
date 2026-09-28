<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="logos/export/logo-dark.svg">
    <img src="logos/export/logo.svg" alt="Labs-Agent" width="360">
  </picture>
</p>

# Labs-Agent

A solution you review before you submit.

Labs-Agent reads your lab manual, writes and runs each task, captures the real output,
and hands back the document your course asks for — the manual filled in, a notebook,
or a script.

- Everything you see actually ran.
- When it can't do something, it says so.
- It only asks when it truly can't continue.

## What it does

- **Reads any lab document** — `.docx`, `.ipynb`, `.pdf`, `.md`/`.txt`, or a lab pasted
  into the chat. One model call classifies it, extracts every task, and resolves your
  request (which tasks, which files, any coding instructions).
- **Gets the data itself.** A Kaggle link in the manual — even one hidden behind linked
  words in a Word file — is downloaded before any code is written. Uploads, direct links
  and Kaggle competitions work the same way. The solver is told each file's columns,
  types and encoding, so it never guesses a column name.
- **Solves each task with an agent** that writes a program, runs it in a sandbox, reads
  the real output and fixes it. Basic tasks take a fast path.
- **Stops instead of improvising.** If a task needs something the environment doesn't
  have — a library, a readable file, the data — it stops and says so in one plain
  sentence ("I can't open .xls files here — please save it as .xlsx or .csv"), finishes
  everything else, and tells you what's needed at the end.
- **Asks only when it truly can't proceed** — e.g. the manual names a dataset but
  gives no link. Otherwise it never pauses.
- **Produces what you asked for:** the manual annotated in place (`.docx`), an executed
  notebook (`.ipynb`), a script (`.py`), a write-up (`.md`), or a `.zip` of them.
  Screenshots are real terminal output, captured as the program ran.

Usage is counted in credits (1 credit = $0.0001 of model time); a typical lab uses 10–40. Open the app, attach the manual (and any data),
say what you want — *"just the notebook for tasks 2 and 4"* — and ask for changes
afterwards in the same chat.

## Use it well

1. **Review everything.** If you can't explain it, don't hand it in.
2. **Follow your course's rules.** If AI help isn't allowed, don't use it.
3. **Learn from it.** Compare it with your own attempt; study the version that works.

## Running it

Needs Python 3.14 with [uv](https://docs.astral.sh/uv/), Node 20+, and a DeepSeek API key.

```bash
# once
uv sync
uv pip install -r web/requirements.txt
cd web/ui && npm install && npm run build && cd ../..

# .env at the repo root
DEEPSEEK_API_KEY=sk-...
KAGGLE_USERNAME=...        # optional; ~/.kaggle/kaggle.json also works
KAGGLE_KEY=...

# run
uv run python -m uvicorn web.server.app:app --port 8000
# open http://127.0.0.1:8000
```

For development, run `npm run dev` in `web/ui` alongside the server (Vite proxies
`/api`, port 5173).

It binds to `127.0.0.1` and has no accounts yet — see [Status](#status).

## How it works

```
upload + message
  → read the document (hyperlink targets included)
  → one ingest call: is it a lab? which tasks? what did you ask for? what data?
  → brief: cover design, layout — and a question only if something blocks a task
  → fetch the data (uploads, links, Kaggle), profile it
  → solve each task: an agent writes, runs and fixes one program
  → emit the formats you asked for, each built independently
  → chat: follow-ups re-solve or rewrite only what they name
```

Three ideas carry most of the design:

- **The model decides *what*; the engine owns *how*.** Classification, task extraction
  and the request are one structured call. Execution, retries, data fetching and file
  building are deterministic code.
- **Trust execution, not claims.** A task passes only on a real successful run of its
  own file. The agent's own "blocked" or "failed" is always honoured; its "passed" is
  checked.
- **One value, one channel.** What the student hands in (`statement`), what steers the
  solver (`instruction`), and what the chat shows are separate fields, so none leaks
  into another.

## Testing and evals

```bash
uv run python -m pytest            # core suite, offline, no key
cd web/ui && npm test              # UI transcript reducer
uv run python -m labsagent.evals   # live eval set: pass rate and cost (~$0.004)
uv run python web/smoke_test.py    # live end-to-end check against a running server
uv run python web/matrix_test.py   # every input type x every deliverable, live
```

The eval set scores against hand-written expected output, including a task that
cannot be done — reporting it as passed would be a false success.

## Stack

Python 3.14 · LangChain deepagents · DeepSeek (`deepseek-flash`) · python-docx ·
nbformat · pandas / numpy / matplotlib / seaborn / scikit-learn · kagglehub ·
FastAPI + server-sent events · React + Vite.

## Status

Working end to end on one machine, for one person. Next: run agent code in an isolated
cloud sandbox, then host it for a few classmates with Google sign-in.

## License

See [LICENSE](LICENSE).
