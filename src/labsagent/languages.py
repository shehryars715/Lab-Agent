"""Every language the harness can write and run, stated once (Harness v2, D1).

WHY A REGISTRY. "Python" used to be assumed in about twenty places: the solver
prompt, the file the solver is told to write, how the run is chosen, how a
program is run, how a missing library is spotted, what the report fences its
code as, the notebook's kernel, the command drawn in a screenshot. Adding a
language meant finding all of them. Now each place asks the task's `Language`,
and a language is one entry below -- the engine stays the same and reads data
(rule: flexibility belongs in the data a stable engine reads, not in its
control flow).

WHAT THIS MODULE IS NOT. It does not say how to COMPILE or RUN anything. Those
commands live in the sandbox driver (`capture/driver.py`), which runs inside the
container and owns its own fixed templates -- a request names a language, never
an argv (D6). A test holds the two key sets equal. What lives here is what the
APP needs: the file to write, the prompt, what a student would have typed (for
the screenshot), and how to read the toolchain's complaints.

AVAILABILITY IS SEPARATE FROM EXISTENCE. All five languages are described here,
but a language is offered only when the sandbox can run it
(`Settings.sandbox_languages`): Python always; the others only where the runner
uses the image that carries their toolchains. So the registry can grow ahead of
the infrastructure without the gate promising what cannot run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Every third-party Python library the solver is promised. Nothing else is
#: installed as far as a task is concerned -- a package that happens to be
#: importable as a dependency of the harness itself is not a promise. (Moved
#: here from capabilities.py, which re-exports it, so the registry can read it
#: without an import cycle.) A test imports every entry.
PYTHON_LIBRARIES = (
    "numpy", "pandas", "matplotlib", "seaborn", "scipy", "scikit-learn", "openpyxl",
)

#: The default, and the only language every backend can run.
DEFAULT = "python"


@dataclass(frozen=True)
class Language:
    """One language: what the app needs to know about it."""

    key: str
    #: What a student is told: "C++", not "cpp".
    display: str
    #: Spellings ingest or a student may use, lowercase.
    aliases: tuple[str, ...]
    #: The suffix of the file the solver is told to write.
    suffix: str
    #: Every suffix that is part of a program in this language (headers too).
    sources: tuple[str, ...]
    #: Markdown fence / highlighter name.
    fence: str
    #: The .ipynb `kernelspec` and `language_info` for a notebook of this code.
    kernelspec: dict = field(default_factory=dict)
    language_info: dict = field(default_factory=dict)
    #: How typed input reaches the program: "shim" (Python's input() echo) or
    #: "pty" (a pseudo-terminal; the terminal echoes, as for a person).
    echo: str = "pty"
    #: `# %%` sections can be run one by one (Python only: one module, exec).
    sections: bool = False
    #: A build step runs before every run (shown as its own command line).
    compiled: bool = False
    #: Third-party libraries promised beyond the standard library.
    libraries: tuple[str, ...] = ()
    #: The Java rule: the public class must be named like its file.
    capitalised_entry: bool = False

    def entry(self, task_id: str) -> str:
        """The file the solver is told to write: task1.py, task1.c, Task1.java."""
        stem = task_id[:1].upper() + task_id[1:] if self.capitalised_entry else task_id
        return f"{stem}{self.suffix}"

    def is_source(self, name: str) -> bool:
        return Path(name).suffix.lower() in self.sources

    def commands(self, entry: str, platform: str = "windows") -> tuple[list[str], str]:
        """(build commands, run command) as a student would TYPE them.

        For the screenshot only -- the real commands are the driver's. The
        flags a student never types (-std, -O0, memory limits) are left out;
        the compiler, the file and the program are the same.
        """
        name = Path(entry).name
        stem = Path(entry).stem
        win = platform == "windows"
        if self.key == "python":
            return [], f"{'python' if win else 'python3'} {name}"
        if self.key in ("c", "cpp"):
            compiler = "gcc" if self.key == "c" else "g++"
            return [f"{compiler} {name} -o {stem}"], (f".\\{stem}.exe" if win else f"./{stem}")
        if self.key == "java":
            return [f"javac {name}"], f"java {stem}"
        return [], f"node {name}"

    def missing_dependency(self, stderr: str, workdir) -> str | None:
        """The library a run could not find, if it is truly absent here.

        The moment a solver learns a library is missing is the moment it used
        to start writing a stand-in for it; `run_solution` turns this into a
        warning at exactly that moment. A file the agent wrote itself is not
        missing from the environment.
        """
        text = stderr or ""
        if self.key == "python":
            match = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", text)
            if not match:
                return None
            top = match.group(1).split(".")[0]
            if (Path(workdir) / f"{top}.py").exists() or (Path(workdir) / top).is_dir():
                return None
            import importlib.util

            try:
                if importlib.util.find_spec(top) is not None:
                    return None
            except (ImportError, ValueError):
                pass
            return top
        if self.key in ("c", "cpp"):
            match = re.search(r"fatal error: ([\w./+-]+): No such file or directory", text)
            if not match or (Path(workdir) / match.group(1)).exists():
                return None
            return match.group(1)
        if self.key == "java":
            match = re.search(r"error: package ([\w.]+) does not exist", text)
            return match.group(1) if match else None
        match = re.search(r"Cannot find module '([^']+)'", text)
        if not match or match.group(1).startswith((".", "/")):
            return None  # a file of its own, not a library
        return match.group(1)

    def library_available(self, name: str) -> bool:
        """Is a library the manual names available? Standard library counts.

        Python is a CLOSED list (promised or stdlib). The C family and Java
        are the other way round: their standard libraries (and POSIX, for C)
        are large, and what a lab names is usually part of them -- so only
        what is known NOT to be here is refused. Fails open: an unknown name
        becomes the solver's missing-dependency guard's problem, which is the
        honest backstop.
        """
        raw = re.sub(r"\s+", " ", str(name or "").strip().lower())
        if not raw:
            return True
        if self.key == "python":
            # ONE implementation of Python's rule, and it is the gate's own.
            from labsagent.capabilities import library_available

            return library_available(name)
        if self.key in ("c", "cpp"):
            return not any(bad in raw for bad in _C_MISSING)
        if self.key == "java":
            return not any(bad in raw for bad in _JAVA_MISSING)
        head = raw.split("/")[0]
        return head in _NODE_BUILTINS or head.removeprefix("node:") in _NODE_BUILTINS \
            or head in self.libraries


#: C/C++ libraries that are NOT here: Windows/Turbo-C headers, graphics, GUI
#: and the big third-party libraries. Everything in glibc and POSIX is.
_C_MISSING = (
    "conio", "graphics.h", "bgi", "windows.h", "dos.h", "glut", "opengl", "sdl", "sfml",
    "ncurses", "curses", "boost", "opencv", "qt", "gtk", "mpi",
)
#: Java libraries that are NOT here: GUI toolkits beyond headless, database
#: drivers and anything fetched by Maven/Gradle.
_JAVA_MISSING = (
    "javafx", "jdbc", "mysql", "postgres", "junit", "spring", "lombok", "gson", "jackson",
    "apache", "hibernate", "maven", "gradle", "selenium",
)
_NODE_BUILTINS = frozenset({
    "assert", "buffer", "child_process", "crypto", "events", "fs", "os", "path", "process",
    "readline", "stream", "string_decoder", "timers", "url", "util", "zlib", "worker_threads",
    "http", "https", "net", "dns",
})


PYTHON = Language(
    key="python",
    display="Python",
    aliases=("python", "python3", "python 3", "py", "none", ""),
    suffix=".py",
    sources=(".py",),
    fence="python",
    kernelspec={"display_name": "Python 3", "language": "python", "name": "python3"},
    language_info={"name": "python"},
    echo="shim",
    sections=True,
    libraries=PYTHON_LIBRARIES,
)
C = Language(
    key="c",
    display="C",
    aliases=("c", "ansi c", "c89", "c99", "c11", "c17", "gcc"),
    suffix=".c",
    sources=(".c", ".h"),
    fence="c",
    kernelspec={"display_name": "C", "language": "c", "name": "c"},
    language_info={"name": "c"},
    compiled=True,
)
CPP = Language(
    key="cpp",
    display="C++",
    aliases=("c++", "cpp", "cplusplus", "c plus plus", "cxx", "c++11", "c++14", "c++17", "g++"),
    suffix=".cpp",
    sources=(".cpp", ".cc", ".cxx", ".h", ".hpp"),
    fence="cpp",
    kernelspec={"display_name": "C++17", "language": "C++17", "name": "xcpp17"},
    language_info={"name": "c++"},
    compiled=True,
)
JAVA = Language(
    key="java",
    display="Java",
    aliases=("java", "jdk", "java 8", "java 11", "java 17", "java 21"),
    suffix=".java",
    sources=(".java",),
    fence="java",
    kernelspec={"display_name": "Java", "language": "java", "name": "java"},
    language_info={"name": "java"},
    compiled=True,
    capitalised_entry=True,
)
JAVASCRIPT = Language(
    key="javascript",
    display="JavaScript",
    aliases=("javascript", "js", "node", "node.js", "nodejs", "ecmascript"),
    suffix=".js",
    sources=(".js", ".cjs", ".mjs"),
    fence="javascript",
    kernelspec={"display_name": "JavaScript (Node.js)", "language": "javascript",
                "name": "javascript"},
    language_info={"name": "javascript"},
    libraries=("prompt-sync",),
)

REGISTRY: dict[str, Language] = {lang.key: lang for lang in (PYTHON, C, CPP, JAVA, JAVASCRIPT)}
_BY_ALIAS = {alias: lang for lang in REGISTRY.values() for alias in (lang.key, *lang.aliases)}


def resolve(name: str | None) -> Language | None:
    """"C++" / "cpp" / "c plus plus" -> CPP. None for a language not here at all."""
    raw = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return _BY_ALIAS.get(raw)


def of(task) -> Language:
    """The language a task is written in. Unknown or missing means Python --
    every manifest written before the field existed is a Python run."""
    return resolve(getattr(task, "language", None)) or PYTHON


def enabled(keys) -> tuple[Language, ...]:
    """The languages a backend can actually run, Python always first."""
    chosen = [PYTHON]
    for key in keys or ():
        lang = resolve(key)
        if lang is not None and lang not in chosen:
            chosen.append(lang)
    return tuple(chosen)
