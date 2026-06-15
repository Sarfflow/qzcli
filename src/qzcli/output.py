"""Output rendering. JSON is the default and the source of truth (principle #4).

Every command produces a value; ``emit`` wraps it in a stable envelope:

    success → {"ok": true, "data": <value>}
    error   → {"ok": false, "error": {code, message, hint?, candidates?}}

``--table`` is the only human-facing mode and is a pure view over the same
data — there is never information reachable only through the table.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, Sequence

from .errors import QzError


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def emit_success(data: Any, *, table: bool = False, columns: Optional[Sequence[str]] = None) -> int:
    """Print a success result. Returns process exit code 0."""
    if table:
        _print_table(data, columns)
    else:
        print(_dump({"ok": True, "data": data}))
    return 0


def emit_error(err: QzError, *, table: bool = False) -> int:
    """Print an error envelope to stdout (agent-parseable). Returns exit code 1."""
    if table:
        msg = f"ERROR [{err.code}] {err.message}"
        if err.hint:
            msg += f"\n  hint: {err.hint}"
        if err.candidates:
            msg += "\n  candidates:"
            for c in err.candidates:
                msg += f"\n    - {c}"
        print(msg, file=sys.stderr)
    else:
        print(_dump({"ok": False, "error": err.to_dict()}))
    return 1


def _print_table(data: Any, columns: Optional[Sequence[str]]) -> None:
    """Best-effort table for a list of dicts; falls back to JSON otherwise."""
    rows: list[dict[str, Any]] = []
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        rows = data
    elif isinstance(data, dict):
        # common shapes: {"items": [...]}, {"jobs": [...]}, single record
        for key in ("items", "jobs", "specs", "compute_groups", "images", "projects", "nodes", "rooms"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        if not rows:
            print(_dump(data))
            return
    else:
        print(_dump(data))
        return

    if not rows:
        print("(empty)")
        return

    cols = list(columns) if columns else list(rows[0].keys())
    widths = {c: len(c) for c in cols}
    for r in rows:
        for c in cols:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
