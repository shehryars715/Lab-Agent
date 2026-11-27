"""Harness v2, Phase 5: deliverables for programs that are not Python.

The danger this guards: a C task's code written into `task1.py`, a Java class
renamed so it no longer compiles, a helper file left out of the zip, a C++
notebook announcing a Python kernel. Python's deliverables must not move.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from labsagent.blocks import blocks_for
from labsagent.emit import REGISTRY, EmitContext
from labsagent.emit.files import FilesEmitter
from labsagent.models import LabSpec, Task, TaskOutcome
from labsagent.package.notebook import build_notebook


def _outcome(task_id="task1", language="java", files=None, code="class Task1 {}"):
    task = Task(id=task_id, title="Bank account", statement="Write a BankAccount class.",
                language=language)
    return TaskOutcome(task=task, status="passed", code_text=code, code_files=files or [])


JAVA_FILES = [
    {"name": "Task1.java", "text": "public class Task1 { public static void main(String[] a) {} }"},
    {"name": "BankAccount.java", "text": "public class BankAccount {}"},
]


def test_a_several_file_program_is_one_code_block_per_file_tagged_with_its_language():
    blocks = blocks_for(_outcome(files=JAVA_FILES))
    code = [b for b in blocks if b.kind == "code"]
    assert [(b.title, b.lang) for b in code] == [("Task1.java", "java"), ("BankAccount.java", "java")]
    # Python: the one block, as always.
    py = TaskOutcome(task=Task(id="task1", title="t", statement="s"), status="passed", code_text="x=1")
    [only] = [b for b in blocks_for(py) if b.kind == "code"]
    assert only.lang == "python" and only.title == ""


def _ctx(tmp_path, outcomes, formats=("files",)):
    spec = LabSpec(lab_number="4", title="Lab 04", tasks=[o.task for o in outcomes])
    return EmitContext(spec=spec, outcomes=outcomes, out_dir=tmp_path)


def test_task_files_keep_every_name_and_put_the_header_in_the_languages_comments(tmp_path):
    ctx = _ctx(tmp_path, [_outcome(files=JAVA_FILES)])
    written = FilesEmitter().emit(ctx)
    names = sorted(p.name for p in written)
    assert names == ["BankAccount.java", "Task1.java"]  # renaming would break javac
    entry = next(p for p in written if p.name == "Task1.java").read_text()
    assert entry.startswith("// Bank account") and "# " not in entry.splitlines()[0]
    assert next(p for p in written if p.name == "BankAccount.java").read_text() == (
        "public class BankAccount {}"
    )
    keys = sorted(FilesEmitter.key_for(p) for p in written)
    assert keys == ["code:task1", "code:task1/BankAccount.java"]


def test_python_task_files_are_unchanged(tmp_path):
    py = TaskOutcome(task=Task(id="task2", title="Sum", statement="Add."), status="passed",
                     code_text="print(1)")
    [path] = FilesEmitter().emit(_ctx(tmp_path, [py]))
    assert path.suffix == ".py" and path.parent.name == "tasks"
    assert path.read_text().startswith("# Sum")
    assert FilesEmitter.key_for(path) == "code:task2"


def test_the_zip_carries_every_file_of_the_program(tmp_path):
    ctx = _ctx(tmp_path, [_outcome(files=JAVA_FILES)])
    [archive] = REGISTRY["zip"].emit(ctx)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert {"code/task1/Task1.java", "code/task1/BankAccount.java"} <= names


def test_a_one_file_c_task_without_a_code_path_is_zipped_as_c(tmp_path):
    ctx = _ctx(tmp_path, [_outcome(language="c", code="int main(){}")])
    [archive] = REGISTRY["zip"].emit(ctx)
    with zipfile.ZipFile(archive) as zf:
        assert "code/task1.c" in zf.namelist()


def test_a_py_never_carries_another_languages_code(tmp_path):
    ctx = _ctx(tmp_path, [_outcome(language="cpp", code="int main(){}")])
    [path] = REGISTRY["py"].emit(ctx)
    text = path.read_text()
    assert "int main" not in text and "Written in C++: see its own file." in text


def test_a_cpp_notebook_has_a_cpp_kernel_and_a_mixed_one_stays_python():
    spec = LabSpec(lab_number="4", title="L", tasks=[])
    cpp = build_notebook(spec, [_outcome(language="cpp", code="int main(){}")])
    assert cpp.metadata["kernelspec"]["name"] == "xcpp17"
    mixed = build_notebook(spec, [
        _outcome(language="cpp", code="int main(){}"),
        TaskOutcome(task=Task(id="task2", title="t", statement="s"), status="passed", code_text="x=1"),
    ])
    assert mixed.metadata["kernelspec"]["name"] == "python3"


def test_a_py_request_for_a_lab_with_no_python_means_the_task_files():
    from web.server.pipeline import code_formats

    c_lab = LabSpec(lab_number="7", title="L", tasks=[Task(id="task1", title="t", statement="s",
                                                           language="c")])
    assert code_formats(["docx", "py"], c_lab) == ["docx", "files"]
    py_lab = LabSpec(lab_number="7", title="L", tasks=[Task(id="task1", title="t", statement="s")])
    assert code_formats(["docx", "py"], py_lab) == ["docx", "py"]
    theory = LabSpec(lab_number="7", title="L", tasks=[Task(id="task1", title="t", statement="s",
                                                            needs_code=False)])
    assert code_formats(["py"], theory) == ["py"]


def test_json_round_trip_of_the_notebook_is_valid(tmp_path):
    nb = build_notebook(LabSpec(lab_number="4", title="L", tasks=[]),
                        [_outcome(language="c", code="int main(){}")])
    assert json.loads(json.dumps(nb))["metadata"]["language_info"]["name"] == "c"
    assert Path(tmp_path).exists()
