"""The web layer's half of dataset support: upload, routing, and the pause.

Offline. The upload route is exercised through Starlette's TestClient, which
calls the app in-process -- no server, no port, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.server.pipeline import RESERVED_KEYS, _acquire, answered_notes, dataset_refs_in

IRIS = "sepal_length,species\n5.1,setosa\n4.9,setosa\n"


# --- reading a dataset out of any answer ------------------------------------


def test_a_url_typed_into_the_agents_own_question_is_found():
    """The agent writes its own questions, so the key is unpredictable.

    `briefing.py` has always listed "which CSV?" as a good question. Before
    this, the reply reached the solver as prose and nothing downloaded.
    """
    found = dataset_refs_in({"which_dataset": "use https://example.com/sales.csv"})
    assert found == ["https://example.com/sales.csv"]


def test_a_kaggle_slug_is_found_among_ordinary_words():
    assert dataset_refs_in({"data_q": "the one at uciml/iris please"}) == ["uciml/iris"]


def test_trailing_punctuation_is_stripped():
    found = dataset_refs_in({"q": "take https://example.com/a.csv."})
    assert found == ["https://example.com/a.csv"]


def test_an_ordinary_answer_yields_nothing():
    assert dataset_refs_in({"q": "use numpy, not plain Python"}) == []


def test_reserved_keys_are_not_scanned():
    """Identity and the structural answers are consumed elsewhere."""
    assert "datasets" in RESERVED_KEYS
    assert dataset_refs_in({"datasets": "uciml/iris", "name": "a/b"}) == []


def test_the_dataset_answer_is_structural_not_prose():
    """It becomes files, so repeating it to the solver as text is noise."""
    questions = [{"key": "datasets", "label": "Data to use"}]
    assert answered_notes(questions, {"datasets": "uciml/iris"}) == ""


# --- what the acquisition step decides to fetch -----------------------------


class _Job:
    """The two methods `_acquire` uses, recording what it published."""

    def __init__(self):
        self.published = []

    def phase(self, key, label):
        self.published.append({"type": "phase", "key": key, "label": label})

    def publish(self, payload):
        self.published.append(payload)

    def kinds(self):
        return [p.get("type") for p in self.published]


class _Settings:
    max_dataset_bytes = 100 * 1024 * 1024
    kaggle_username = ""
    kaggle_key = ""
    convert_excel_to_csv = True


class _Store:
    def __init__(self, path: Path):
        self.data_dir = path


@pytest.fixture
def csv(tmp_path: Path) -> Path:
    path = tmp_path / "sales.csv"
    path.write_text(IRIS, encoding="utf-8")
    return path


def _run(tmp_path, **kwargs):
    from labsagent.intent import Intent

    job = _Job()
    acquired = _acquire(
        job,
        uploads=kwargs.get("uploads", []),
        answers=kwargs.get("answers", {}),
        intent=kwargs.get("intent", Intent()),
        store=_Store(tmp_path / "data"),
        settings=_Settings(),
    )
    # `_acquire` reports what it managed AND what it did not; these tests are
    # almost all about the former, so it is unpacked here once.
    return job, acquired.datasets


def test_nothing_to_fetch_costs_nothing(tmp_path: Path):
    """A lab with no data must not get a phase change or a narration line."""
    job, datasets = _run(tmp_path)
    assert datasets == []
    assert job.published == []


def test_an_upload_is_resolved_and_profiled(tmp_path: Path, csv: Path):
    job, datasets = _run(tmp_path, uploads=[csv])
    assert [d.name for d in datasets] == ["sales.csv"]
    assert "sepal_length" in datasets[0].preview
    assert "data_ready" in job.kinds()


def test_clearing_the_field_means_no_data(tmp_path: Path, csv: Path):
    """An empty answer is an instruction, not a missing one.

    Same rule the artifacts field follows: the agent proposes, you redirect.
    """
    _, datasets = _run(tmp_path, uploads=[csv], answers={"datasets": ""})
    assert datasets == []


def test_the_answer_overrides_what_the_manual_named(tmp_path: Path, csv: Path):
    from labsagent.intent import Intent

    _, datasets = _run(
        tmp_path,
        uploads=[csv],
        intent=Intent(datasets=["uciml/iris"]),
        answers={"datasets": "sales.csv"},
    )
    assert [d.name for d in datasets] == ["sales.csv"], "no Kaggle call was made"


def test_an_attached_name_in_the_answer_maps_back_to_its_path(tmp_path: Path, csv: Path):
    """The pause shows "sales.csv"; only the server knows where that file is."""
    _, datasets = _run(tmp_path, uploads=[csv], answers={"datasets": "sales.csv"})
    assert datasets and datasets[0].path.exists()


def test_a_failure_is_narrated_and_the_run_continues(tmp_path: Path, csv: Path):
    job, datasets = _run(
        tmp_path, uploads=[csv], answers={"datasets": "sales.csv http://127.0.0.1/x.csv"}
    )
    assert [d.name for d in datasets] == ["sales.csv"], "the good one still landed"
    assert "data_failed" in job.kinds()
    said = " ".join(p.get("text", "") for p in job.published)
    assert "could not get" in said


# --- the upload route -------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from web.server import app as app_module

    # Nothing may actually run: these tests are about what the ROUTE accepts.
    started = {}

    class _Thread:
        def __init__(self, target=None, kwargs=None, **_):
            started["kwargs"] = kwargs or {}

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", _Thread)
    monkeypatch.setattr(app_module, "UPLOADS_ROOT", tmp_path / "uploads")
    return TestClient(app_module.app), started


def test_the_students_name_survives_an_attachment(client):
    """A real bug this change fixes.

    `name` was the form field AND the local the filename was assigned to, so
    every run started with a file attached filed its report under the document
    instead of under whoever sent it.
    """
    http, started = client
    response = http.post(
        "/api/runs",
        data={"instructions": "do it", "name": "Ayesha", "cms_id": "22F-1234"},
        files={"manual": ("lab03_manual.md", b"# Lab 03\nPrint the sum.", "text/markdown")},
    )
    assert response.status_code == 202
    assert started["kwargs"]["profile_seed"]["name"] == "Ayesha"


def test_a_csv_rides_along_with_the_manual(client):
    http, started = client
    response = http.post(
        "/api/runs",
        data={"instructions": ""},
        files=[
            ("manual", ("lab.md", b"# Lab\nCluster it.", "text/markdown")),
            ("datasets", ("sales.csv", IRIS.encode(), "text/csv")),
        ],
    )
    assert response.status_code == 202
    paths = started["kwargs"]["dataset_paths"]
    assert [p.name for p in paths] == ["sales.csv"]
    assert paths[0].read_text(encoding="utf-8") == IRIS


def test_data_can_arrive_with_a_pasted_lab_and_no_file(client):
    """The upload directory used to exist only when a manual was attached."""
    http, started = client
    response = http.post(
        "/api/runs",
        data={"instructions": "Cluster the attached data into 2 groups."},
        files=[("datasets", ("sales.csv", IRIS.encode(), "text/csv"))],
    )
    assert response.status_code == 202
    assert started["kwargs"]["manual_path"] is None
    assert [p.name for p in started["kwargs"]["dataset_paths"]] == ["sales.csv"]


def test_an_executable_is_not_a_dataset(client):
    http, _ = client
    response = http.post(
        "/api/runs",
        data={"instructions": "x"},
        files=[("datasets", ("evil.exe", b"MZ", "application/octet-stream"))],
    )
    assert response.status_code == 415
    assert "as data" in response.json()["detail"]


def test_a_traversal_aimed_at_dotenv_is_refused_outright(client):
    """Two guards, and the suffix one fires first.

    `Path("../../.env").name` is ".env", which has no suffix at all -- so it is
    not in DATA_SUFFIXES and never reaches the disk. The directory-stripping
    below is the second line, not the only one.
    """
    http, _ = client
    response = http.post(
        "/api/runs",
        data={"instructions": "x"},
        files=[("datasets", ("../../.env", IRIS.encode(), "text/csv"))],
    )
    assert response.status_code == 415


def test_a_traversing_filename_lands_inside_the_job_folder(client, tmp_path):
    """A name that IS valid data still loses its directory component."""
    http, started = client
    response = http.post(
        "/api/runs",
        data={"instructions": "x"},
        files=[("datasets", ("../../escaped.csv", IRIS.encode(), "text/csv"))],
    )
    assert response.status_code == 202
    (landed,) = started["kwargs"]["dataset_paths"]
    assert landed.name == "escaped.csv"
    assert (tmp_path / "uploads") in landed.parents
    assert not (tmp_path / "escaped.csv").exists()


def test_too_many_data_files_is_refused(client):
    from web.server.app import MAX_DATA_FILES

    http, _ = client
    files = [("manual", ("lab.md", b"# Lab", "text/markdown"))]
    files += [
        ("datasets", (f"d{i}.csv", IRIS.encode(), "text/csv"))
        for i in range(MAX_DATA_FILES + 1)
    ]
    response = http.post("/api/runs", data={"instructions": ""}, files=files)
    assert response.status_code == 413


def test_no_datasets_still_works(client):
    http, started = client
    response = http.post(
        "/api/runs",
        data={"instructions": "Print the sum of two numbers."},
    )
    assert response.status_code == 202
    assert started["kwargs"]["dataset_paths"] == []


def test_a_url_is_not_swapped_for_a_same_named_upload(tmp_path: Path, csv: Path):
    """Mapping every reference by its last path segment would resolve
    "https://example.com/sales.csv" to the attached sales.csv -- a DIFFERENT
    file, substituted silently. Only a bare name may be looked up."""
    job, datasets = _run(
        tmp_path,
        uploads=[csv],
        answers={"datasets": "https://example.invalid/sales.csv"},
    )
    assert datasets == [], "the upload must not stand in for the URL"
    assert "data_failed" in job.kinds()


# --- a filename with a space in it ------------------------------------------
#
# Every test above uses "sales.csv". That is why this survived: the answer is
# offered joined by ", " and was split on commas AND spaces alike, so
# "Online Retail.xlsx, https://..." became three references, all of which
# failed, and the lab was solved against no data at all.


@pytest.fixture
def spaced(tmp_path: Path) -> Path:
    path = tmp_path / "Online Retail.xlsx"
    path.write_text(IRIS, encoding="utf-8")
    return path


def test_a_filename_with_spaces_survives_the_answer(tmp_path: Path, spaced: Path):
    job, datasets = _run(
        tmp_path,
        uploads=[spaced],
        answers={"datasets": "Online Retail.xlsx, https://example.invalid/other.csv"},
    )
    names = [d.name for d in datasets]
    assert "Online_Retail.xlsx" in names or "Online Retail.xlsx" in names, names
    attempted = [p["ref"] for p in job.published if p.get("type") == "data_fetching"]
    assert len(attempted) == 2, f"two references, not three: {attempted}"
    assert "Online" not in attempted


def test_a_whole_path_with_spaces_is_one_reference(tmp_path: Path, spaced: Path):
    """No comma to split on, and spaces that are not separators."""
    _, datasets = _run(tmp_path, answers={"datasets": str(spaced)})
    assert len(datasets) == 1


def test_references_on_separate_lines_are_not_split_further(tmp_path: Path, spaced: Path):
    job, _ = _run(
        tmp_path,
        uploads=[spaced],
        answers={"datasets": "Online Retail.xlsx\nuciml/iris"},
    )
    attempted = [p["ref"] for p in job.published if p.get("type") == "data_fetching"]
    assert len(attempted) == 2, attempted
