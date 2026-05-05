"""Convert Postgres-array-as-string fields in a DBeaver JSON export to
proper JSON arrays.

DBeaver's "Export → JSON" for Postgres array columns serializes them as
their Postgres text representation (e.g. ``'{Employment,"Mental
Health"}'``) rather than as JSON arrays (``["Employment", "Mental
Health"]``). This script normalizes those columns in-place.

It also strips DBeaver's wrapping when the JSON file is a single-key
dict whose key is the entire SQL query text (which DBeaver does for
result sets that didn't have an explicit alias).

Usage:
    python3 scripts/fixture/normalize_dbeaver_export.py \\
        path/to/raw_export.json path/to/services.json

Read-only on the input. Writes a new file at the output path.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Columns that production stores as text[] / varchar[] arrays. DBeaver
# exports these as Postgres array literals like '{a,b,"c d"}'.
ARRAY_COLUMNS = ("service_taxonomies", "also_available", "languages_spoken")


def _parse_pg_array(s: str) -> list[str]:
    """Parse a Postgres array literal like ``{Employment,"Mental Health"}``
    into a Python list of strings.

    Handles:
      - empty array: ``{}`` → ``[]``
      - bare identifiers: ``{a,b,c}`` → ``['a', 'b', 'c']``
      - quoted strings: ``{"a","b c","d,e"}`` → ``['a', 'b c', 'd,e']``
      - mixed: ``{a,"b c",d}``
      - escaped quotes inside strings: ``{"a\\"b"}`` → ``['a"b']``
    """
    if not s or not s.startswith("{") or not s.endswith("}"):
        # Not a Postgres array literal — leave it alone.
        return [s] if s else []
    inner = s[1:-1]
    if not inner:
        return []
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        if inner[i] == '"':
            # Quoted element. Walk until the next unescaped quote.
            i += 1  # skip opening quote
            buf: list[str] = []
            while i < n:
                if inner[i] == "\\" and i + 1 < n:
                    buf.append(inner[i + 1])
                    i += 2
                elif inner[i] == '"':
                    i += 1  # skip closing quote
                    break
                else:
                    buf.append(inner[i])
                    i += 1
            out.append("".join(buf))
            # skip the comma after, if any
            if i < n and inner[i] == ",":
                i += 1
        else:
            # Bare element. Walk until next comma.
            j = inner.find(",", i)
            if j == -1:
                out.append(inner[i:])
                break
            out.append(inner[i:j])
            i = j + 1
    return out


def _normalize_value(col: str, val: Any) -> Any:
    if col not in ARRAY_COLUMNS:
        return val
    if val is None:
        return None
    if isinstance(val, list):
        # Already JSON-shaped; leave alone.
        return val
    if isinstance(val, str):
        return _parse_pg_array(val)
    # Unexpected — leave alone but flag.
    return val


def _unwrap_dbeaver_export(d: Any) -> list[dict]:
    """If the JSON is a single-key dict whose value is a list of records,
    return that list. Otherwise return ``d`` unchanged (assuming it's
    already a list).

    DBeaver names the result set after the SQL query when there's no
    table alias, producing structures like
    ``{<huge_sql_string>: [...rows...]}``.
    """
    if isinstance(d, list):
        return d
    if isinstance(d, dict) and len(d) == 1:
        only_value = next(iter(d.values()))
        if isinstance(only_value, list):
            return only_value
    raise ValueError(
        f"Unexpected JSON shape — expected list of rows or single-key "
        f"wrapper dict. Got: {type(d).__name__}"
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    raw = json.loads(in_path.read_text())
    rows = _unwrap_dbeaver_export(raw)
    print(f"Loaded {len(rows)} rows from {in_path}")

    # Normalize each row's array columns.
    converted_count = {col: 0 for col in ARRAY_COLUMNS}
    for r in rows:
        for col in ARRAY_COLUMNS:
            old = r.get(col)
            new = _normalize_value(col, old)
            if isinstance(old, str) and isinstance(new, list):
                converted_count[col] += 1
                r[col] = new

    print("\nConverted Postgres-array strings → JSON arrays:")
    for col, n in converted_count.items():
        print(f"  {col:<22} {n:>4} rows converted")

    out_path.write_text(json.dumps(rows, indent=2))
    print(f"\nWrote {out_path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
