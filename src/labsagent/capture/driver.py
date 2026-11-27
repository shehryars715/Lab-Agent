"""The pseudo-terminal driver. Runs INSIDE the sandbox; imports nothing from labsagent.

    python _labsagent_run.py _labsagent_job.json

The job is {"language", "entry", "inputs", "timeout"}. This file -- not the app,
not the runner -- turns a language key into commands, from the fixed templates
below (Harness v2, D6: a request names what to run, never how). A job that
names anything else is refused.

WHY A TERMINAL. Python's shim makes input() echo at the moment it is consumed.
No such hook exists for scanf, cin, Scanner or Node, and stdio reads ahead in
buffers, so a per-language hook would echo at the wrong moment. A terminal
does the echo itself: the line discipline prints what is typed, when it is
typed. So the program runs on a pty, and each input line is typed only when the
program is WAITING for it -- which the kernel says exactly:

  * a thread of the program is blocked in read() on the terminal, and
  * the terminal's input queue is empty (everything typed was consumed), and
  * the output has been quiet for a moment (the prompt was drained first).

Event loops (Node's readline) wait in epoll, not read, so a second rule
accepts "the MAIN thread is in epoll/poll/select, nothing is running, quiet a
little longer". sleep() is deliberately not waiting: an OS lab that sleeps
between prompts must not get its input early. (Proven by the Phase 1 spike:
16/16 programs, also under CPU contention. Learning/HARNESS-V2.md.)

WHAT COMES BACK. stdout is what the SCREEN shows (`screen()`: Node draws its
line editor with cursor codes, which a terminal interprets and so must we).
stderr is the program's stderr, followed by marker lines the app strips:

  @@LABSAGENT-COMPILE-FAILED@@         the build failed; its output is above
  @@LABSAGENT-BUILD-OUTPUT@@ <json>    a successful build's warnings (agent only)
  @@LABSAGENT-SOURCES@@ <json>         the files the program was built from
  __LABSAGENT_INPUT_EXHAUSTED__:<n>    it asked for more input than it was given
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

EXIT_INPUT_EXHAUSTED = 42
EXIT_BAD_JOB = 2
SENTINEL_EXHAUSTED = "__LABSAGENT_INPUT_EXHAUSTED__"
MARK_COMPILE = "@@LABSAGENT-COMPILE-FAILED@@"
MARK_BUILD = "@@LABSAGENT-BUILD-OUTPUT@@"
MARK_SOURCES = "@@LABSAGENT-SOURCES@@"
OUTPUT_CAP = 2_000_000
BUILD_TIMEOUT_S = 60
BUILD_DIR = ".lab"
#: A value that presses Ctrl-D instead of typing a line.
EOF_KEY = "^D"

_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}$")

#: language -> (build argv | None, run argv). {entry}, {stem}, {sources}, {main}
#: are filled from the job; nothing else from the job reaches a command line.
TEMPLATES = {
    "c": (["gcc", "-std=gnu17", "-O0", "-o", BUILD_DIR + "/{stem}", "{sources}", "-lm",
           "-pthread"], ["./" + BUILD_DIR + "/{stem}"]),
    "cpp": (["g++", "-std=gnu++17", "-O0", "-o", BUILD_DIR + "/{stem}", "{sources}",
             "-pthread"], ["./" + BUILD_DIR + "/{stem}"]),
    "java": (["javac", "-J-Xmx192m", "-J-XX:+UseSerialGC", "-J-XX:TieredStopAtLevel=1",
              "-verbose", "-encoding", "UTF-8", "-sourcepath", ".", "-d", BUILD_DIR + "/classes",
              "{entry}"],
             ["java", "-Xmx192m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
              "-XX:-UsePerfData", "-cp", BUILD_DIR + "/classes", "{main}"]),
    "javascript": (None, ["node", "--max-old-space-size=192", "{entry}"]),
}

# syscall numbers: blocked reading, and blocked waiting on an event loop.
_SYSCALLS = {
    "x86_64": ({0, 17, 19}, {7, 23, 232, 270, 271, 281, 441}),
    "aarch64": ({63, 65, 67}, {22, 72, 73, 441}),
}


class BadJob(ValueError):
    """A job outside the one shape this driver runs."""


def load_job(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        job = json.load(fh)
    if not isinstance(job, dict):
        raise BadJob("the job must be an object")
    language, entry = job.get("language"), job.get("entry")
    if language not in TEMPLATES:
        raise BadJob(f"unknown language {language!r}")
    if not isinstance(entry, str) or not _NAME.match(entry) or ".." in entry:
        raise BadJob("entry must be a plain file name in the working directory")
    inputs = job.get("inputs") or []
    if not isinstance(inputs, list) or len(inputs) > 500 or not all(
        isinstance(v, str) and len(v) <= 4000 and "\0" not in v for v in inputs
    ):
        raise BadJob("inputs must be a short list of short strings")
    timeout = job.get("timeout", 30)
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 240:
        raise BadJob("timeout must be between 1 and 240 seconds")
    return {"language": language, "entry": entry, "inputs": inputs, "timeout": int(timeout)}


# --------------------------------------------------------------- the sources

_QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
_REQUIRE = re.compile(r"""(?:require\(\s*|from\s+|import\s+)['"](\.{1,2}/[^'"]+)['"]""")


def _read(name: str) -> str:
    try:
        with open(name, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def c_sources(entry: str) -> tuple[list[str], list[str]]:
    """(files to compile, every file the program is made of) for C/C++.

    Ship what the run used (D8): the entry, every header it includes with
    quotes (recursively), and the .c/.cpp beside each such header. A stray
    probe.c with its own main() is neither compiled nor shipped -- compiling
    it would fail the link with two mains.
    """
    stem_suffixes = (".c",) if entry.endswith(".c") else (".cpp", ".cc", ".cxx")
    compile_ = [entry]
    seen = [entry]
    queue = [entry]
    while queue:
        name = queue.pop(0)
        for header in _QUOTED_INCLUDE.findall(_read(name)):
            if not _NAME.match(header) or header in seen or not os.path.isfile(header):
                continue
            seen.append(header)
            queue.append(header)
            base = os.path.splitext(header)[0]
            for suffix in stem_suffixes:
                partner = base + suffix
                if partner not in seen and os.path.isfile(partner):
                    seen.append(partner)
                    compile_.append(partner)
                    queue.append(partner)
    return compile_, seen


def js_sources(entry: str) -> list[str]:
    seen, queue = [entry], [entry]
    while queue:
        for ref in _REQUIRE.findall(_read(queue.pop(0))):
            name = os.path.normpath(ref)
            for candidate in (name, name + ".js", name + ".cjs", name + ".mjs"):
                if _NAME.match(candidate) and candidate not in seen and os.path.isfile(candidate):
                    seen.append(candidate)
                    queue.append(candidate)
                    break
    return seen


def java_sources(verbose: str) -> list[str]:
    """The .java files javac actually parsed, from its -verbose output.

    Two shapes (JDK 21, seen in the container): the file named on the command
    line is `SimpleFileObject[/work/Task1.java]`; one found through -sourcepath
    is `DirectoryFileObject[.:Helper.java]` -- directory, colon, relative path.
    A file nothing uses (a probe) is never parsed, so it is never shipped.
    """
    found: list[str] = []
    for match in re.finditer(r"\[parsing started \w+\[([^\]]+\.java)\]", verbose):
        inner = match.group(1)
        name = inner.split(":", 1)[1] if ":" in inner else os.path.basename(inner)
        if name not in found:
            found.append(name)
    return found


def _java_main(entry: str) -> str:
    stem = os.path.splitext(entry)[0]
    package = re.search(r"^\s*package\s+([\w.]+)\s*;", _read(entry), re.M)
    return f"{package.group(1)}.{stem}" if package else stem


# ------------------------------------------------------------------- the build

def build(language: str, entry: str) -> tuple[bool, list[str], str]:
    """(ok, sources, build output). Compiles nothing for an interpreted language."""
    import subprocess

    template = TEMPLATES[language][0]
    if template is None:
        return True, js_sources(entry), ""
    os.makedirs(BUILD_DIR, exist_ok=True)
    if language in ("c", "cpp"):
        compile_, sources = c_sources(entry)
    else:
        compile_, sources = [entry], [entry]
    stem = os.path.splitext(entry)[0]
    argv: list[str] = []
    for part in template:
        if part == "{sources}":
            argv.extend(compile_)
        else:
            argv.append(part.format(entry=entry, stem=stem))
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, sources, f"the build did not finish in {BUILD_TIMEOUT_S} s"
    except OSError as exc:
        return False, sources, f"the compiler could not start: {exc}"
    output = done.stdout + done.stderr
    if language == "java":
        sources = java_sources(output) or sources
        # -verbose is for us; the student and the agent see only real messages.
        output = "\n".join(
            line for line in output.splitlines() if not line.startswith("[")
        ).strip()
    return done.returncode == 0, sources, output.strip()


# ---------------------------------------------------------------- the screen

_ESCAPE = re.compile(
    r"\x1b\[([0-9;?]*)[ -/]*([@-~])"      # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC, dropped
    r"|\x1b([78c])"                         # save / restore / reset
    r"|\x1b[()][0-9A-Za-z]"                 # character set, dropped
)


def screen(raw: str) -> str:
    """What a terminal SHOWS for `raw` -- not the bytes that were sent to it.

    Node's readline and prompt-sync draw their own line editor with cursor codes
    ("ESC[1G ESC[0J Enter name: ESC[13G Ali"); a terminal interprets them
    invisibly, and so must a transcript. A VT100 subset is enough: CR, LF, BS,
    TAB, cursor moves (CHA CUF CUB CUU CUD CNL CPL CUP), erase in line and in
    display, save and restore. Colour (SGR) is passed through for the renderer,
    which interprets it already; anything else is dropped. A full clear (ESC[2J,
    ESC c) keeps only what follows it: that is what the screen shows.
    """
    lines: list[list[list[str]]] = [[]]
    tails: dict[int, str] = {}
    row = col = 0
    saved = (0, 0)
    sgr = ""

    def goto(target: int) -> None:
        nonlocal row
        row = max(0, target)
        while len(lines) <= row:
            lines.append([])

    pos = 0
    while pos < len(raw):
        match = _ESCAPE.match(raw, pos)
        if match:
            pos = match.end()
            params, final, esc = match.group(1), match.group(2), match.group(3)
            if esc == "7":
                saved = (row, col)
            elif esc == "8":
                goto(saved[0])
                col = saved[1]
            elif esc == "c":
                lines, tails, row, col = [[]], {}, 0, 0
            if final is None or (params or "").startswith("?"):
                continue
            nums = [int(p) if p.isdigit() else 0 for p in (params or "").split(";")]
            n = nums[0] or 1
            if final == "m":
                sgr += match.group(0)
            elif final == "G":
                col = n - 1
            elif final == "C":
                col += n
            elif final == "D":
                col = max(0, col - n)
            elif final == "A":
                goto(row - n)
            elif final in "BE":
                goto(row + n)
                if final == "E":
                    col = 0
            elif final == "F":
                goto(row - n)
                col = 0
            elif final in "Hf":
                goto(n - 1)
                col = (nums[1] if len(nums) > 1 and nums[1] else 1) - 1
            elif final == "K":
                line = lines[row]
                if nums[0] == 0:
                    del line[col:]
                elif nums[0] == 1:
                    for i in range(min(col + 1, len(line))):
                        line[i] = ["", " "]
                else:
                    line.clear()
            elif final == "J":
                if nums[0] == 0:
                    del lines[row][col:]
                    del lines[row + 1:]
                elif nums[0] in (2, 3):
                    lines, tails, row = [[]], {}, 0
            elif final == "s":
                saved = (row, col)
            elif final == "u":
                goto(saved[0])
                col = saved[1]
            continue
        ch = raw[pos]
        pos += 1
        if ch == "\n":
            if sgr:
                tails[row] = tails.get(row, "") + sgr
                sgr = ""
            goto(row + 1)
        elif ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            col = (col // 8 + 1) * 8
        elif ch < " " or ch == "\x7f":
            continue
        else:
            line = lines[row]
            while len(line) < col:
                line.append(["", " "])
            cell = [sgr, ch]
            if col < len(line):
                line[col] = cell
            else:
                line.append(cell)
            sgr = ""
            col += 1
    if sgr:
        tails[row] = tails.get(row, "") + sgr
    rendered = [
        "".join(style + ch for style, ch in line).rstrip(" ") + tails.get(index, "")
        for index, line in enumerate(lines)
    ]
    while rendered and not rendered[-1]:
        rendered.pop()
    return "\n".join(rendered) + ("\n" if rendered else "")


# ------------------------------------------------------------------- the run

def _session_tasks(sid: int):
    """(pid, tid, state, syscall fields) for every thread in the session."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            if int(fields[3]) != sid:
                continue
            for tid in os.listdir(f"/proc/{name}/task"):
                try:
                    with open(f"/proc/{name}/task/{tid}/stat") as fh:
                        state = fh.read().rsplit(")", 1)[1].split()[0]
                    with open(f"/proc/{name}/task/{tid}/syscall") as fh:
                        call = fh.read().split()
                except OSError:
                    continue
                found.append((int(name), int(tid), state, call))
        except (OSError, IndexError, ValueError):
            continue
    return found


def _is_tty_fd(pid: int, fd: int) -> bool:
    try:
        link = os.readlink(f"/proc/{pid}/fd/{fd}")
    except OSError:
        return False
    return link.startswith("/dev/pts/") or link in ("/dev/tty", "/dev/console")


def awaiting_input(sid: int, quiet_s: float, reads, waits) -> bool:
    """Is the program waiting for a person to type? See the module docstring."""
    tasks = _session_tasks(sid)
    loop_wait = False
    for pid, tid, state, call in tasks:
        # Running, or in uninterruptible disk I/O (loading modules, say): busy.
        busy = state in ("R", "D")
        if not call or call[0] in ("running", "-1"):
            if busy:
                return False
            continue
        try:
            number = int(call[0])
        except ValueError:
            continue
        if number in reads and len(call) > 1 and _is_tty_fd(pid, int(call[1], 16)):
            return quiet_s >= 0.03
        if busy:
            return False
        # AN EVENT LOOP WAITS ON ITS MAIN THREAD. Node's helper threads sit in
        # epoll from the start, while the main thread is still loading modules;
        # counting any thread typed the input before the prompt was printed.
        if number in waits and tid == pid:
            loop_wait = True
    return loop_wait and quiet_s >= 0.1


def run(argv: list[str], inputs: list[str], timeout: int) -> tuple[int, str, str, bool]:
    """(exit code, screen text, stderr, exhausted) for one program on a pty."""
    import fcntl
    import platform
    import select
    import signal
    import struct
    import subprocess
    import termios

    reads, waits = _SYSCALLS.get(platform.machine(), _SYSCALLS["x86_64"])
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    attrs = termios.tcgetattr(slave)
    attrs[0] |= termios.ICRNL
    attrs[1] |= termios.OPOST | termios.ONLCR
    attrs[3] |= termios.ECHO | termios.ICANON | termios.ISIG
    termios.tcsetattr(slave, termios.TCSANOW, attrs)

    proc = subprocess.Popen(
        argv, stdin=slave, stdout=slave, stderr=subprocess.PIPE,
        start_new_session=True,
        # The terminal becomes the program's controlling terminal, so a program
        # that opens /dev/tty (prompt-sync does) finds it.
        preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
    )
    out, err = bytearray(), bytearray()
    open_fds = {master, proc.stderr.fileno()}
    pending = list(inputs)
    keys: list[bytes] = []
    exhausted = timed_out = truncated = False
    started = last_output = time.monotonic()
    exited_at = None

    def kill() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass

    while open_fds:
        ready, _, _ = select.select(list(open_fds), [], [], 0.02)
        now = time.monotonic()
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""  # EIO: every holder of the terminal has closed it
            if not chunk:
                open_fds.discard(fd)
                continue
            (out if fd == master else err).extend(chunk)
            last_output = now
            if len(out) + len(err) > OUTPUT_CAP and not truncated:
                truncated = True
                kill()
        if now - started > timeout:
            timed_out = True
            kill()
            break
        if proc.poll() is not None:
            if exited_at is None:
                exited_at = now
                os.close(slave)
                slave = None
            if now - exited_at > 0.5:  # a fork lab's orphans get a short grace
                kill()
                if not ready:
                    break
            continue
        if ready:
            continue
        queued = struct.unpack("i", fcntl.ioctl(slave, termios.FIONREAD, b"\0" * 4))[0]
        if queued or not awaiting_input(proc.pid, now - last_output, reads, waits):
            continue
        # TYPE LIKE A PERSON. In the normal (canonical) mode the kernel holds a
        # typed line until Enter, so writing it whole IS typing it. A program
        # that switched the terminal to raw mode reads keystrokes, and
        # prompt-sync takes any multi-byte read for an arrow key -- so in raw
        # mode one key goes in per wait.
        raw_mode = not termios.tcgetattr(slave)[3] & termios.ICANON
        if keys:
            os.write(master, keys.pop(0))
        elif pending:
            value = pending.pop(0)
            # NO AUTOMATIC CTRL-D. glibc's EOF is sticky: after it every scanf
            # returns at once, so a program short of input would run on with
            # garbage and exit 0. EOF is pressed only when the inputs say so.
            typed = b"\x04" if value == EOF_KEY else (value + "\r").encode()
            if raw_mode and len(typed) > 1:
                keys = [typed[i:i + 1] for i in range(1, len(typed))]
                typed = typed[:1]
            os.write(master, typed)
        else:
            exhausted = True
            kill()
            break
        last_output = time.monotonic()

    code = proc.wait()
    if slave is not None:
        os.close(slave)
    os.close(master)
    stderr = err.decode("utf-8", "replace")
    if timed_out:
        stderr += f"\n[timed out after {timeout}s]"
        code = -1
    elif truncated:
        code = -1
    elif not exhausted and code in (-9, 137):
        # We kill with SIGKILL too, but only on the paths above or for running
        # out of input; an unexplained SIGKILL is the kernel's OOM killer.
        stderr += "\n[stopped: the program ran out of memory]"
    return code, screen(out.decode("utf-8", "replace")), stderr, exhausted


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _labsagent_run.py <job.json>", file=sys.stderr)
        return EXIT_BAD_JOB
    if os.name != "posix":
        print("this driver needs a POSIX system (it runs programs on a pseudo-terminal)",
              file=sys.stderr)
        return EXIT_BAD_JOB
    try:
        job = load_job(argv[1])
    except (OSError, ValueError) as exc:
        print(f"bad job: {exc}", file=sys.stderr)
        return EXIT_BAD_JOB
    language, entry = job["language"], job["entry"]
    if not os.path.isfile(entry):
        print(f"{entry}: no such file", file=sys.stderr)
        return 1
    ok, sources, output = build(language, entry)
    if not ok:
        sys.stderr.write(output + f"\n{MARK_COMPILE}\n")
        sys.stderr.write(f"{MARK_SOURCES} {json.dumps(sources)}\n")
        return 1
    stem = os.path.splitext(entry)[0]
    command = [
        part.format(entry=entry, stem=stem, main=_java_main(entry) if language == "java" else stem)
        for part in TEMPLATES[language][1]
    ]
    code, text, stderr, exhausted = run(command, job["inputs"], job["timeout"])
    sys.stdout.write(text)
    sys.stdout.flush()
    sys.stderr.write(stderr)
    if output:
        sys.stderr.write(f"\n{MARK_BUILD} {json.dumps(output[:4000])}\n")
    sys.stderr.write(f"\n{MARK_SOURCES} {json.dumps(sources)}\n")
    if exhausted:
        sys.stderr.write(f"\n{SENTINEL_EXHAUSTED}:{len(job['inputs'])}\n")
        return EXIT_INPUT_EXHAUSTED
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
