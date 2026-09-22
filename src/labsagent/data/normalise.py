"""Turning a workbook into the CSV every task actually reads.

WHY CONVERT AT ALL, NOW THAT openpyxl IS INSTALLED. Two reasons, and neither is
about being able to open the file:

1. COST. The workbook is copied into every task's workspace, and each task then
   pays to parse it again. `Online Retail.xlsx` is 23 MB of zipped XML that
   takes pandas the better part of a minute; the same data as CSV is a
   `read_csv` away. Converting once, at acquire time, is the difference between
   paying that price once and paying it once per task per attempt.

2. WHAT GETS HANDED IN. The submitted `.py` says `pd.read_csv("online_retail.csv")`
   and runs anywhere pandas does. `pd.read_excel` quietly depends on an engine
   the student may not have -- which is the exact failure this whole module
   exists because of, moved from our machine to theirs.

STREAMING, NOT `pd.read_excel`. Reading 500k rows into a DataFrame to write them
straight back out costs minutes and gigabytes for no benefit. `read_only=True`
gives openpyxl's streaming reader, and rows go to `csv.writer` as they arrive,
so memory is flat regardless of how big the workbook is.

CONVERSION IS AN OPTIMISATION, NEVER A RUN-KILLER. Every failure returns an
empty list and the caller keeps the workbook it already had. A dataset that
arrives slowly is better than a run that does not start.
"""

from __future__ import annotations

import csv
from pathlib import Path

#: How many sheets of one workbook become CSVs. A model workbook with twelve
#: tabs is usually one table and eleven of working; the prompt can carry a few
#: named files, not a directory listing.
MAX_SHEETS = 3


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() else "_" for c in str(name)).strip("_")
    return cleaned.lower() or "sheet"


def _write_sheet(sheet, target: Path) -> bool:
    """One sheet to one CSV. False when the sheet held nothing."""
    wrote = 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in sheet.iter_rows(values_only=True):
            # A trailing run of empty cells is how a workbook pads its used
            # range; writing them produces a CSV with phantom columns.
            if row and all(cell is None for cell in row):
                continue
            writer.writerow(["" if cell is None else cell for cell in row])
            wrote += 1
    if wrote:
        return True
    target.unlink(missing_ok=True)
    return False


def to_csv(path: Path, dest_dir: Path, *, max_sheets: int = MAX_SHEETS) -> list[Path]:
    """Every readable sheet of a workbook, as CSV files in `dest_dir`.

    One sheet is named after the workbook; several are suffixed with the sheet
    name, so `sales.xlsx` becomes `sales.csv` or `sales__q1.csv` and friends.
    Returns [] if anything at all goes wrong -- see the module docstring.
    """
    path, dest_dir = Path(path), Path(dest_dir)
    try:
        import openpyxl
    except ImportError:
        return []

    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 -- a corrupt workbook is just "no CSVs"
        return []

    written: list[Path] = []
    try:
        sheets = [s for s in book.worksheets][:max_sheets]
        multiple = len(sheets) > 1
        dest_dir.mkdir(parents=True, exist_ok=True)
        for sheet in sheets:
            stem = path.stem if not multiple else f"{path.stem}__{_slug(sheet.title)}"
            target = dest_dir / f"{_slug(stem)}.csv"
            try:
                if _write_sheet(sheet, target):
                    written.append(target)
            except Exception:  # noqa: BLE001 -- one bad sheet is not the workbook
                target.unlink(missing_ok=True)
                continue
    except Exception:  # noqa: BLE001
        for target in written:
            target.unlink(missing_ok=True)
        return []
    finally:
        try:
            book.close()
        except Exception:  # noqa: BLE001
            pass

    return written
