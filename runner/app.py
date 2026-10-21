"""The runner sidecar: the only process that may start containers.

    app --POST /run {task_dir, argv, timeout_s}--> runner --docker.sock--> container

WHY IT EXISTS. Starting a container needs the Docker socket, and whoever holds
the socket is root on the host. The app parses untrusted .docx and .pdf files
from strangers, so it must not hold it. This service holds it instead and
accepts exactly one request shape. Every limit that matters (no network, the
memory and process caps, the read-only system, the user, the mounts) is FIXED
here; nothing in the request can loosen it. A compromised app can ask for a
program to run in a task folder, which it could already do; it cannot ask for a
privileged container or a host mount.

WHAT IS VALIDATED, because this is the security boundary:
  - task_dir is an existing <RUNS_DIR>/<run>/workspace/<task> directory, with
    no symlink anywhere on the way;
  - argv is `python _labsagent_shim.py [--cells] <file> <file>`, where each
    file is a relative path with no `..` and no backslash;
  - the token matches (compare_digest), and the body is small.

Standard library plus the `docker` SDK, on purpose: less code to trust.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

TOKEN = os.environ.get("RUNNER_TOKEN", "")
RUNS_DIR = Path(os.environ.get("RUNS_DIR", "/srv/labsagent/runs"))
IMAGE = os.environ.get("SANDBOX_IMAGE", "labsagent-app")
PORT = int(os.environ.get("RUNNER_PORT", "7000"))
MEMORY = os.environ.get("PROGRAM_MEMORY", "384m")
PROGRAM_USER = os.environ.get("PROGRAM_USER", "1000:1000")
MAX_CONCURRENT = int(os.environ.get("RUNNER_MAX_CONCURRENT", "1"))

OUTPUT_CAP_BYTES = 2_000_000  # same as labsagent.sandbox.base
MAX_TIMEOUT_S = 300
MAX_BODY_BYTES = 64 * 1024
FILE_SIZE_CAP = 200 * 1024 * 1024  # RLIMIT_FSIZE: one file a program may write
SHIM = "_labsagent_shim.py"
LABEL = "labsagent.run"

# What a program starts with. Nothing from this process's environment, which
# holds RUNNER_TOKEN.
PROGRAM_ENV = {
    "PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "MPLBACKEND": "Agg",
    "MPLCONFIGDIR": "/tmp/mpl",
    "XDG_CACHE_HOME": "/tmp/cache",
    "PYTHONUTF8": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "LANG": "C.UTF-8",
}

_ARG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- /]{0,199}$")
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_client = None


def client():
    # Imported here, not at the top, so `validate` can be tested on a machine
    # without the Docker SDK (the app's own test suite does exactly that).
    import docker

    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


class Refused(ValueError):
    """A request outside the one shape this service runs."""


def validate(body: dict) -> tuple[str, list[str], int]:
    task_dir = body.get("task_dir")
    argv = body.get("argv")
    timeout = body.get("timeout_s", 30)
    if not isinstance(task_dir, str) or not os.path.isabs(task_dir):
        raise Refused("task_dir must be an absolute path")
    normal = os.path.normpath(task_dir)
    # realpath == normpath means no component on the way is a symlink.
    if os.path.realpath(normal) != normal or not os.path.isdir(normal):
        raise Refused("task_dir is not a plain existing directory")
    try:
        parts = Path(normal).relative_to(RUNS_DIR.resolve()).parts
    except ValueError:
        raise Refused("task_dir is outside the runs directory") from None
    if len(parts) != 3 or parts[1] != "workspace" or parts[0].startswith(("_", ".")):
        raise Refused("task_dir must be <run>/workspace/<task>")

    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise Refused("argv must be a list of strings")
    rest = argv[2:]
    if rest[:1] == ["--cells"]:
        rest = rest[1:]
    if argv[:2] != ["python", SHIM] or len(rest) != 2:
        raise Refused("argv must be: python _labsagent_shim.py [--cells] <file> <file>")
    for arg in rest:
        path = PurePosixPath(arg)
        if not _ARG.match(arg) or path.is_absolute() or ".." in path.parts:
            raise Refused(f"argument {arg!r} is not a plain relative file name")

    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= MAX_TIMEOUT_S:
        raise Refused(f"timeout_s must be between 1 and {MAX_TIMEOUT_S}")
    return normal, list(argv), int(timeout)


def run(task_dir: str, argv: list[str], timeout: int) -> dict:
    import docker
    from docker.types import LogConfig, Ulimit

    started = time.monotonic()
    container = client().containers.create(
        IMAGE,
        command=argv,
        entrypoint=[],
        working_dir="/work",
        user=PROGRAM_USER,
        environment=PROGRAM_ENV,
        network_mode="none",
        mem_limit=MEMORY,
        memswap_limit=MEMORY,  # equal to the memory limit: no swap
        pids_limit=128,
        nano_cpus=1_000_000_000,
        read_only=True,
        tmpfs={"/tmp": "rw,size=64m,mode=1777"},
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        ulimits=[
            Ulimit(name="fsize", soft=FILE_SIZE_CAP, hard=FILE_SIZE_CAP),
            Ulimit(name="nofile", soft=256, hard=256),
        ],
        volumes={task_dir: {"bind": "/work", "mode": "rw"}},
        init=True,
        labels={LABEL: "1"},
        # No log driver: output reaches us through attach only, so a flood
        # is never written to the host's disk either.
        log_config=LogConfig(type="none"),
        stdin_open=False,
        tty=False,
    )
    out, err = bytearray(), bytearray()
    over = threading.Event()
    try:
        # Attached BEFORE start, so not a byte of early output is missed.
        stream = container.attach(stdout=True, stderr=True, stream=True, demux=True, logs=False)

        def pump() -> None:
            total = 0
            try:
                for chunk_out, chunk_err in stream:
                    for chunk, buffer in ((chunk_out, out), (chunk_err, err)):
                        if not chunk:
                            continue
                        room = OUTPUT_CAP_BYTES - total
                        buffer.extend(chunk[: max(room, 0)])
                        total += len(chunk)
                        if total > OUTPUT_CAP_BYTES and not over.is_set():
                            over.set()
                            try:
                                container.kill()
                            except docker.errors.APIError:
                                pass
                            return
            except Exception:  # noqa: BLE001 -- the stream ends when the container does
                return

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        container.start()

        timed_out = False
        exit_code = -1
        try:
            exit_code = int(container.wait(timeout=timeout).get("StatusCode", -1))
        except Exception:  # noqa: BLE001 -- requests' ReadTimeout: the program is still going
            timed_out = not over.is_set()
            try:
                container.kill()
            except docker.errors.APIError:
                pass
        reader.join(timeout=5)
        truncated = over.is_set()
        stderr = err.decode("utf-8", errors="replace")
        if timed_out:
            stderr += f"\n[timed out after {timeout}s]"
        elif exit_code == 137 and not truncated:
            stderr += "\n[stopped: the program ran out of memory]"
        return {
            "exit_code": -1 if (timed_out or truncated) else exit_code,
            "stdout": out.decode("utf-8", errors="replace"),
            "stderr": stderr,
            "duration_s": time.monotonic() - started,
            "timed_out": timed_out,
            "truncated": truncated,
        }
    finally:
        try:
            container.remove(force=True)
        except docker.errors.APIError:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "labsagent-runner"

    def _reply(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"ok": True, "image": IMAGE})
        else:
            self._reply(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self._reply(404, {"detail": "not found"})
            return
        given = self.headers.get("X-Runner-Token", "")
        if not TOKEN or not hmac.compare_digest(given.encode(), TOKEN.encode()):
            self._reply(401, {"detail": "bad token"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(413, {"detail": "body missing or too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            task_dir, argv, timeout = validate(body if isinstance(body, dict) else {})
        except (ValueError, Refused) as exc:
            self._reply(400, {"detail": str(exc)})
            return
        with _slots:
            try:
                result = run(task_dir, argv, timeout)
            except Exception as exc:  # noqa: BLE001 -- reported as infrastructure
                self._reply(503, {"detail": f"{type(exc).__name__}: {exc}"[:500]})
                return
        self._reply(200, result)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("runner: " + (fmt % args) + "\n")


def sweep() -> None:
    """Remove containers a previous runner left behind (a crash mid-run)."""
    import docker

    for leftover in client().containers.list(all=True, filters={"label": LABEL}):
        try:
            leftover.remove(force=True)
        except docker.errors.APIError:
            pass


def main() -> None:
    if not TOKEN:
        sys.exit("RUNNER_TOKEN is not set; refusing to start without it")
    sweep()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"runner: listening on :{PORT}, image {IMAGE}, runs {RUNS_DIR}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
