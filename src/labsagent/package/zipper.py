"""Build the submission archive.

    Lab03_<roll_no>.zip
      Lab03_Report.docx
      code/         task1.py, ...
      screenshots/  task1_output.png, ...

Screenshots are duplicated (they are also embedded in the DOCX) so a grader can
check them independently.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from labsagent.models import TaskOutcome


def build_submission(
    zip_path: Path,
    report_path: Path,
    outcomes: list[TaskOutcome],
) -> Path:
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(report_path, arcname=report_path.name)

        for outcome in outcomes:
            if outcome.code_path and Path(outcome.code_path).exists():
                zf.write(outcome.code_path, arcname=f"code/{Path(outcome.code_path).name}")
            elif outcome.code_text:
                zf.writestr(f"code/{outcome.task.id}.py", outcome.code_text)

            for shot in outcome.screenshot_paths:
                shot = Path(shot)
                if shot.exists():
                    zf.write(shot, arcname=f"screenshots/{shot.name}")

    return zip_path
