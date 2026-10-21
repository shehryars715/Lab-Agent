"""Hardening for input from strangers: runner validation, scrubbing, zip bombs,
DNS rebinding, and upload caps. Offline: no Docker, no network, no model.
"""

from __future__ import annotations

import importlib.util
import io
import os
import socket
import sys
import zipfile
from pathlib import Path

import pytest

from labsagent.data import sources
from labsagent.errors import DataError, IngestError
from labsagent.ingest import docx_reader
from labsagent.sandbox.runner import scrub

ROOT = Path(__file__).resolve().parents[1]


def _load_runner(runs_dir: Path):
    """runner/app.py is not a package; load it with RUNS_DIR pointed at tmp."""
    os.environ["RUNS_DIR"] = str(runs_dir)
    spec = importlib.util.spec_from_file_location("labsagent_runner_app", ROOT / "runner" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("labsagent_runner_app", None)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner(tmp_path):
    runs = (tmp_path / "runs").resolve()
    task = runs / "20261021-090000_lab03_abc" / "workspace" / "task1"
    task.mkdir(parents=True)
    return _load_runner(runs), runs, task


GOOD_ARGV = ["python", "_labsagent_shim.py", "task1.py", "_labsagent_inputs.txt"]


def test_runner_accepts_the_one_shape(runner):
    app, _runs, task = runner
    task_dir, argv, timeout = app.validate({"task_dir": str(task), "argv": GOOD_ARGV, "timeout_s": 30})
    assert task_dir == os.path.normpath(str(task)) and argv == GOOD_ARGV and timeout == 30
    cells = ["python", "_labsagent_shim.py", "--cells", "task1.py", "_labsagent_inputs.txt"]
    assert app.validate({"task_dir": str(task), "argv": cells})[1] == cells


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "-c", "id"],
        ["python", "-c", "print(1)"],
        ["python", "_labsagent_shim.py", "/etc/passwd", "in.txt"],
        ["python", "_labsagent_shim.py", "../other/task1.py", "in.txt"],
        ["python", "_labsagent_shim.py", "task1.py"],
        ["python", "_labsagent_shim.py", "-m", "x", "y"],
        ["python", "_labsagent_shim.py", "a\\b.py", "in.txt"],
    ],
)
def test_runner_refuses_any_other_command(runner, argv):
    app, _runs, task = runner
    with pytest.raises(app.Refused):
        app.validate({"task_dir": str(task), "argv": argv})


def test_runner_refuses_folders_that_are_not_a_task_workspace(runner, tmp_path):
    app, runs, task = runner
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    uploads = runs / "_web_uploads" / "workspace" / "x"
    uploads.mkdir(parents=True)
    for bad in (
        str(outside),
        str(runs),
        str(task.parent),
        str(uploads),
        str(task / ".." / ".." / ".." / ".." / "elsewhere"),
        "relative/path",
        str(task / "missing"),
    ):
        with pytest.raises(app.Refused):
            app.validate({"task_dir": bad, "argv": GOOD_ARGV})


def test_runner_refuses_a_symlinked_task_folder(runner, tmp_path):
    app, runs, _task = runner
    target = tmp_path / "secret"
    target.mkdir()
    link = runs / "20261021-090000_lab03_abc" / "workspace" / "task2"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    with pytest.raises(app.Refused):
        app.validate({"task_dir": str(link), "argv": GOOD_ARGV})


def test_runner_bounds_the_timeout(runner):
    app, _runs, task = runner
    for timeout in (0, 10_000, "30"):
        with pytest.raises(app.Refused):
            app.validate({"task_dir": str(task), "argv": GOOD_ARGV, "timeout_s": timeout})


def test_scrub_removes_links_and_keeps_files(tmp_path):
    (tmp_path / "keep.py").write_text("print(1)")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "out.csv").write_text("a\n1\n")
    try:
        (tmp_path / "chart.png").symlink_to(tmp_path.parent / "someone-elses-report.docx")
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    removed = scrub(tmp_path)
    assert removed == ["chart.png"]
    assert (tmp_path / "keep.py").exists() and (tmp_path / "sub" / "out.csv").exists()


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def test_a_zip_bomb_is_stopped_while_unpacking(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "MAX_ARCHIVE_BYTES", 1_000_000)
    archive = tmp_path / "data.zip"
    archive.write_bytes(_zip({"a.csv": b"x,y\n" + b"0,0\n" * 400_000}))
    assert archive.stat().st_size < 50_000  # a small file...
    with pytest.raises(DataError, match="unpacks to more than"):
        sources._from_zip(archive, tmp_path / "out", origin="upload", ref="data.zip", taken=set())
    assert not any((tmp_path / "out").glob("_*_unpacked"))  # ...and nothing left behind


def test_a_docx_bomb_is_refused_before_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_reader, "MAX_DOCX_UNPACKED_BYTES", 1_000_000)
    bomb = tmp_path / "lab.docx"
    bomb.write_bytes(_zip({"word/document.xml": b" " * 5_000_000}))
    with pytest.raises(IngestError, match="far more than"):
        docx_reader.read_manual(bomb)


def test_the_connection_checks_the_address_it_connects_to(monkeypatch):
    """DNS rebinding: public on the first lookup, metadata on the second."""
    answers = iter(["93.184.216.34", "169.254.169.254"])

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    connected = []
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: connected.append(addr))
    with pytest.raises(DataError, match="169.254.169.254"):
        sources.from_url("http://rebind.example/data.csv", Path("unused"))
    assert connected == []  # never connected to either answer


def test_a_data_upload_over_the_cap_is_refused_without_reading_it_all(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    monkeypatch.setattr(app_module, "MAX_DATA_BYTES", 1_000_000)
    monkeypatch.setattr(app_module, "UPLOADS_ROOT", tmp_path / "uploads")
    started = []
    monkeypatch.setattr(app_module.threading, "Thread", lambda *a, **k: started.append(k) or _NoThread())
    client = TestClient(app_module.app)
    response = client.post(
        "/api/runs",
        data={"instructions": "Task 1: print hello"},
        files=[("datasets", ("big.csv", b"a,b\n" + b"1,2\n" * 400_000, "text/csv"))],
    )
    assert response.status_code == 413
    assert not list((tmp_path / "uploads").rglob("big.csv"))
    assert started == []


class _NoThread:
    def start(self) -> None:
        pass
