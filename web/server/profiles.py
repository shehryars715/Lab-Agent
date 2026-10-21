"""Your name and CMS ID, kept on the server and tied to your account.

They used to live only in the browser (localStorage). That was right for one
person on one machine, and wrong once there are accounts: a student signs in
on a lab computer and expects their details to follow them, and two students
on one computer must not inherit each other's name on a cover page.

Two stores behind one pair of functions:
  - supabase mode: the `profiles` table, through supa.rpc.
  - open / password mode: one small JSON file per user under the runs folder
    (`_profiles/<user>.json`), so localhost works with no database.
Only the four fields that reach a cover page are kept.
"""

from __future__ import annotations

import json
from pathlib import Path

from labsagent.config import load_settings

from . import auth, supa

FIELDS = ("name", "cms_id", "section", "program")
MAX_LEN = 120


def clean(values: dict) -> dict[str, str]:
    """Only the known fields, trimmed and bounded."""
    return {
        key: str(values.get(key) or "").strip()[:MAX_LEN]
        for key in FIELDS
        if str(values.get(key) or "").strip()
    }


def _file(user_id: str) -> Path:
    return load_settings().runs_dir / "_profiles" / f"{user_id}.json"


def get(user_id: str) -> dict[str, str]:
    empty = {key: "" for key in FIELDS}
    if auth.mode() == "supabase":
        row = supa.rpc("get_profile", p_user_id=user_id) or {}
        if isinstance(row, list):
            row = row[0] if row else {}
        return {**empty, **{k: row.get(k) or "" for k in FIELDS}}
    path = _file(user_id)
    try:
        return {**empty, **clean(json.loads(path.read_text(encoding="utf-8")))}
    except (OSError, ValueError):
        return empty


def save(user_id: str, values: dict) -> dict[str, str]:
    """Merge `values` into the profile. An empty field leaves the old value."""
    merged = {**get(user_id), **clean(values)}
    if auth.mode() == "supabase":
        supa.rpc("save_profile", p_user_id=user_id, **{f"p_{k}": merged[k] for k in FIELDS})
        return merged
    path = _file(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged), encoding="utf-8")
    return merged
