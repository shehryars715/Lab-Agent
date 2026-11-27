"""Harness v2, Phase 3: C, C++, Java and JavaScript programs, run on a pty.

Two layers. Everything above the last test runs anywhere, with no Docker: the
driver's job validation, its screen emulator and its source closures, the
markers, the runner's second request shape, persistence, and the guard on a
forged source list. The last test is the real thing -- app -> HTTP -> the real
runner -> Docker -> the driver -> back -- and runs only when an image with the
toolchains is named:

    LABSAGENT_SANDBOX_IMAGE=labsagent-sandbox  python -m pytest tests/test_polyglot.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

import pytest

from labsagent import languages as langs
from labsagent.capture import driver
from labsagent.models import ExecResult, Task, TaskOutcome, Transcript
from labsagent.runstore import _outcome_from_dict, _outcome_to_dict

ROOT = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------- the job

def _job(tmp_path, **fields):
    import json

    job = {"language": "c", "entry": "task1.c", "inputs": ["5"], "timeout": 30, **fields}
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job))
    return str(path)


def test_a_good_job_loads(tmp_path):
    job = driver.load_job(_job(tmp_path))
    assert job == {"language": "c", "entry": "task1.c", "inputs": ["5"], "timeout": 30}


@pytest.mark.parametrize(
    "fields",
    [
        {"language": "bash"},          # not a template: no way to run a shell
        {"language": "python"},        # Python has its own path (the shim)
        {"entry": "../task2/x.c"},
        {"entry": "/etc/passwd"},
        {"entry": "a b.c"},
        {"entry": "-o.c"},             # cannot pose as a compiler flag
        {"inputs": "5"},
        {"inputs": ["ok", "nul\0byte"]},
        {"timeout": 0},
        {"timeout": 9999},
    ],
)
def test_the_driver_refuses_any_other_job(tmp_path, fields):
    with pytest.raises(driver.BadJob):
        driver.load_job(_job(tmp_path, **fields))


def test_every_driver_template_is_a_registry_language_and_vice_versa():
    non_python = {key for key, lang in langs.REGISTRY.items() if lang.echo == "pty"}
    assert set(driver.TEMPLATES) == non_python


# ----------------------------------------------------------- the screen

@pytest.mark.parametrize(
    "raw, shown",
    [
        # Node readline draws its own line editor with cursor codes.
        ("\x1b[1G\x1b[0JEnter name: \x1b[13GAli\r\nHello, Ali\r\n", "Enter name: Ali\nHello, Ali\n"),
        # A progress line rewritten with \r shows only its last state.
        ("\rLoading   0%\rLoading  50%\rLoading 100%\r\nDone\r\n", "Loading 100%\nDone\n"),
        # A full clear keeps what follows: that is the screen.
        ("old\r\n\x1b[2J\x1b[HMenu\r\nChoice: 1\r\n", "Menu\nChoice: 1\n"),
        # Colour passes through for the renderer, which interprets it.
        ("\x1b[31mRed\x1b[0m plain\r\n", "\x1b[31mRed\x1b[0m plain\n"),
        # Backspace and overwrite, as a typed correction shows.
        ("Enter n: 9\b7\r\n", "Enter n: 7\n"),
        ("", ""),
    ],
)
def test_the_transcript_is_what_the_screen_shows(raw, shown):
    assert driver.screen(raw) == shown


# ----------------------------------------------------------- the sources

def test_c_compiles_and_ships_the_include_closure_and_never_a_probe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "task1.c").write_text('#include <stdio.h>\n#include "stack.h"\nint main(){}\n')
    (tmp_path / "stack.h").write_text('#include "node.h"\nvoid push(int);\n')
    (tmp_path / "node.h").write_text("struct node;\n")
    (tmp_path / "stack.c").write_text('#include "stack.h"\nvoid push(int x){}\n')
    (tmp_path / "probe.c").write_text("int main(){return 0;}\n")  # a second main
    compile_, sources = driver.c_sources("task1.c")
    assert compile_ == ["task1.c", "stack.c"]
    # Breadth first: each header's own .c comes right after it.
    assert sources == ["task1.c", "stack.h", "stack.c", "node.h"]


def test_node_ships_its_require_closure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "task1.js").write_text("const u = require('./util');\nrequire('./gone');\n")
    (tmp_path / "util.js").write_text("module.exports = 1;\n")
    (tmp_path / "probe.js").write_text("console.log(1)\n")
    assert driver.js_sources("task1.js") == ["task1.js", "util.js"]


def test_java_ships_what_javac_parsed():
    # Captured from javac 21 -verbose in the sandbox image (2026-11-27).
    verbose = (
        "[parsing started SimpleFileObject[/work/Task1.java]]\n[parsing completed 30ms]\n"
        "[parsing started DirectoryFileObject[.:Helper.java]]\n[parsing completed 1ms]\n"
        "[wrote /tmp/out/Task1.class]\n"
    )
    assert driver.java_sources(verbose) == ["Task1.java", "Helper.java"]


# ----------------------------------------------------------- the app side

def test_markers_are_stripped_and_read():
    from labsagent.runner import _strip_marks

    stderr = (
        "warning: unused variable\n"
        f"{driver.MARK_BUILD} \"task1.c:3: warning: unused\"\n"
        f"{driver.MARK_SOURCES} [\"task1.c\", \"stack.c\"]\n"
    )
    clean, marks = _strip_marks(stderr)
    assert clean == "warning: unused variable"
    assert marks["sources"] == ["task1.c", "stack.c"]
    assert marks["build_output"] == "task1.c:3: warning: unused"
    assert not marks["compile_error"]
    assert _strip_marks(f"x.c:1: error\n{driver.MARK_COMPILE}\n")[1]["compile_error"]


@pytest.mark.skipif(os.name == "posix", reason="the refusal is for non-POSIX machines")
def test_on_windows_a_compiled_language_is_infrastructure_not_the_agent(tmp_path):
    from labsagent.errors import SandboxError
    from labsagent.runner import run_solution
    from labsagent.sandbox.local import LocalSandbox

    (tmp_path / "task1.c").write_text("int main(){return 0;}\n")
    with LocalSandbox(workdir=tmp_path) as sandbox:
        with pytest.raises(SandboxError, match="POSIX"):
            run_solution(sandbox, "task1.c", [], 10, language="c")


def _load_runner(runs_dir: Path):
    os.environ["RUNS_DIR"] = str(runs_dir)
    spec = importlib.util.spec_from_file_location("labsagent_runner_app", ROOT / "runner" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("labsagent_runner_app", None)
    spec.loader.exec_module(module)
    return module


def test_the_runner_takes_the_driver_shape_exactly(tmp_path):
    runs = (tmp_path / "runs").resolve()
    task = runs / "20261127-000000_lab07_abc" / "workspace" / "task1"
    task.mkdir(parents=True)
    app = _load_runner(runs)
    good = ["python", "_labsagent_run.py", "_labsagent_job.json"]
    assert app.validate({"task_dir": str(task), "argv": good, "timeout_s": 90})[1] == good
    for bad in (
        ["python", "_labsagent_run.py", "other.json"],
        ["python", "_labsagent_run.py", "_labsagent_job.json", "--x"],
        ["gcc", "task1.c"],
        ["python", "_labsagent_run.py"],
    ):
        with pytest.raises(app.Refused):
            app.validate({"task_dir": str(task), "argv": bad})


def test_compile_commands_show_as_their_own_prompt_lines():
    t = Transcript(command="java Task1", lines=["Enter n: 5"], prompt="PS>", setup=("javac Task1.java",))
    assert t.display_lines() == ["PS> javac Task1.java", "PS> java Task1", "Enter n: 5", "PS> "]


def test_what_a_student_types_per_platform():
    assert langs.C.commands("task1.c", "windows") == (["gcc task1.c -o task1"], ".\\task1.exe")
    assert langs.C.commands("task1.c", "linux") == (["gcc task1.c -o task1"], "./task1")
    assert langs.JAVA.commands("Task1.java") == (["javac Task1.java"], "java Task1")
    assert langs.JAVASCRIPT.commands("task1.js") == ([], "node task1.js")
    assert langs.PYTHON.commands("task1.py") == ([], "python task1.py")


def test_each_window_draws_its_own_platforms_commands():
    from labsagent.capture.rendered import RenderedBackend

    c = Transcript(command=".\\task1.exe", lines=["hi"], language="c", entry="task1.c",
                   setup=("gcc task1.c -o task1",))
    assert RenderedBackend(look="ubuntu")._commands(c) == ["gcc task1.c -o task1", "./task1"]
    assert RenderedBackend(look="terminal")._commands(c) == ["gcc task1.c -o task1", ".\\task1.exe"]
    py = Transcript(command="python task1.py", lines=["hi"])
    assert RenderedBackend(look="ubuntu")._commands(py) == ["python task1.py"]  # untouched


def test_the_ubuntu_window_has_bashs_prompt_and_draws(tmp_path):
    from labsagent.capture.rendered import RenderedBackend

    backend = RenderedBackend(look="ubuntu", folder="Lab 06")
    rows = backend._rows(Transcript(command="./task1", lines=["Sum = 8"], language="c",
                                    entry="task1.c"))
    first = "".join(text for text, _ in rows[0][1])
    assert first == "student@ubuntu:~/Desktop/Lab 06$ gcc task1.c -o task1"
    [png] = backend.render(Transcript(command="./task1", lines=["Sum = 8"]), tmp_path / "s.png")
    assert png.exists() and png.stat().st_size > 1000


class _Para:
    def __init__(self, text):
        self.text = text


class _Manual:
    def __init__(self, *texts):
        self.paragraphs = [_Para(t) for t in texts]


class _Spec:
    lab_number = "06"


@pytest.mark.parametrize(
    "text, look",
    [
        ("Use fork() to create a child process and wait() for it.", "ubuntu"),
        ("Create two threads with pthread_create.", "ubuntu"),
        ("Compile with gcc on Ubuntu.", "ubuntu"),
        ("Open Jupyter and run the cells. Use the Linux kernel dataset.", "notebook"),
        ("Write a program to add two numbers. Run it in the terminal.", "terminal"),
    ],
)
def test_the_window_follows_the_labs_platform(text, look):
    from web.server.pipeline import screenshot_backend

    assert screenshot_backend(_Spec(), _Manual(text), "lab", None).look == look


def test_new_fields_round_trip_and_python_outcomes_are_unchanged():
    task = Task(id="task1", title="t", statement="s", language="c")
    outcome = TaskOutcome(
        task=task, status="passed", code_text="int main(){}",
        transcript=Transcript(command=".\\task1.exe", lines=["hi"], language="c",
                              entry="task1.c", setup=("gcc task1.c -o task1",)),
        code_files=[{"name": "task1.c", "text": "int main(){}"}, {"name": "util.h", "text": ""}],
    )
    back = _outcome_from_dict(_outcome_to_dict(outcome))
    assert back.transcript.setup == ("gcc task1.c -o task1",)
    assert back.transcript.language == "c" and back.transcript.entry == "task1.c"
    assert [f["name"] for f in back.code_files] == ["task1.c", "util.h"]
    py = TaskOutcome(task=Task(id="task1", title="t", statement="s"), status="passed",
                     transcript=Transcript(command="python task1.py", lines=["hi"]))
    data = _outcome_to_dict(py)
    assert "code_files" not in data and set(data["transcript"]) == {"command", "lines", "prompt"}


class _Store:
    def __init__(self, root):
        self.code_dir = root


class _Sandbox:
    def __init__(self, files):
        self.files = files

    def read_file(self, name):
        if name not in self.files:
            raise FileNotFoundError(name)
        return self.files[name]


class _Chosen:
    def __init__(self, sources):
        self.sources = sources


def test_a_forged_source_list_cannot_ship_anything_but_the_tasks_own_code(tmp_path):
    from labsagent.orchestrator import _code_files

    sandbox = _Sandbox({"Task1.java": "class Task1{}", "Helper.java": "class Helper{}",
                        "secrets.csv": "a,b"})
    forged = _Chosen(["Task1.java", "Helper.java", "secrets.csv", "../../etc/passwd", "Gone.java"])
    files = _code_files(sandbox, langs.JAVA, "Task1.java", "class Task1{}", forged,
                        _Store(tmp_path), Task(id="task1", title="t", statement="s"))
    assert [f["name"] for f in files] == ["Task1.java", "Helper.java"]
    assert (tmp_path / "task1" / "Helper.java").read_text() == "class Helper{}"
    # Python's program is its entry: no list at all.
    assert _code_files(sandbox, langs.PYTHON, "task1.py", "", forged, _Store(tmp_path),
                       Task(id="task1", title="t", statement="s")) == []


# ----------------------------------------------------------- end to end

IMAGE = os.environ.get("LABSAGENT_SANDBOX_IMAGE", "")

C_SUM = '#include <stdio.h>\nint main(void){int n,m;printf("Enter n: ");scanf("%d",&n);' \
        'printf("Enter m: ");scanf("%d",&m);printf("Sum = %d\\n",n+m);return 0;}\n'
JAVA_MAIN = ('import java.util.Scanner;\npublic class Task1 { public static void main(String[] a) {'
             ' Scanner s = new Scanner(System.in); System.out.print("Enter word: ");'
             ' System.out.println(new Shout().loud(s.next())); } }\n')
JAVA_HELPER = "public class Shout { String loud(String w) { return w.toUpperCase(); } }\n"


@pytest.mark.skipif(not IMAGE, reason="set LABSAGENT_SANDBOX_IMAGE to an image with the toolchains")
def test_end_to_end_through_the_real_runner(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer

    from labsagent.runner import run_solution
    from labsagent.sandbox.runner import RunnerSandbox

    runs = (tmp_path / "runs").resolve()
    monkeypatch.setenv("RUNNER_TOKEN", "t0ken")
    monkeypatch.setenv("SANDBOX_IMAGE", IMAGE)
    app = _load_runner(runs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    def run(task_name, files, entry, inputs, language):
        task_dir = runs / "20261127-000000_lab07_e2e" / "workspace" / task_name
        task_dir.mkdir(parents=True)
        for name, text in files.items():
            (task_dir / name).write_bytes(text.encode())
        with RunnerSandbox(task_dir, url=url, token="t0ken") as sandbox:
            return run_solution(sandbox, entry, inputs, 30, language=language)

    try:
        c = run("c", {"task1.c": C_SUM, "probe.c": "int main(){return 1;}\n"}, "task1.c",
                ["5", "3"], "c")
        assert c.ok and c.transcript.lines == ["Enter n: 5", "Enter m: 3", "Sum = 8"]
        assert c.sources == ["task1.c"]  # the probe is neither built nor shipped
        assert c.transcript.setup == ("gcc task1.c -o task1",)

        java = run("java", {"Task1.java": JAVA_MAIN, "Shout.java": JAVA_HELPER,
                            "Probe.java": "class Probe {}\n"}, "Task1.java", ["hello"], "java")
        assert java.ok and java.transcript.lines == ["Enter word: hello", "HELLO"]
        assert java.sources == ["Task1.java", "Shout.java"]

        js = run("js", {"task1.js": "const p = require('prompt-sync')();\n"
                        "const n = Number(p('Enter n: '));\nconsole.log(`Square = ${n*n}`);\n"},
                 "task1.js", ["7"], "javascript")
        assert js.ok and js.transcript.lines == ["Enter n: 7", "Square = 49"]

        broken = run("broken", {"task1.cpp": "int main(){ return 0 }\n"}, "task1.cpp", [], "cpp")
        assert broken.compile_error and not broken.ok
        assert "expected" in broken.result.stderr and driver.MARK_COMPILE not in broken.result.stderr

        short = run("short", {"task1.c": C_SUM}, "task1.c", ["5"], "c")
        assert not short.ok and any("more input" in w for w in short.warnings)
    finally:
        server.shutdown()
