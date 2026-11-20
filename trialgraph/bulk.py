"""Bulk-load Python rows into DuckDB without a row-at-a-time insert.

`executemany` costs one round trip per row: 60,000 location rows took most of
two minutes. Writing the rows once as newline-delimited JSON and letting
DuckDB read them back with an explicit schema does the same work in well under
a second, and keeps the project free of a pyarrow or pandas dependency.
The explicit column types matter: without them DuckDB would infer types from
the data, and a column that happens to be all NULL in one load would come back
with a different type than in the next.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def create_and_load(con, table: str, cols: list[tuple[str, str]], rows: list[tuple]) -> int:
    con.execute(f"CREATE OR REPLACE TABLE {table} ("
                + ", ".join(f"{n} {t}" for n, t in cols) + ")")
    if not rows:
        return 0
    names = [n for n, _ in cols]
    fd, path = tempfile.mkstemp(suffix=".ndjson")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(dict(zip(names, r)), default=_default))
                fh.write("\n")
        spec = "{" + ", ".join(f"'{n}': '{t}'" for n, t in cols) + "}"
        con.execute(f"INSERT INTO {table} SELECT {', '.join(names)} FROM "
                    f"read_json('{path}', format='newline_delimited', columns={spec})")
    finally:
        os.unlink(path)
    return len(rows)
