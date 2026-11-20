"""Export the warehouse definition to Databricks: SQL models, Unity Catalog
governance DDL, and a run manifest for the bundle's notebook.

The SQL models are written once, in DuckDB SQL, and transpiled with sqlglot.
A transpiler is only trustworthy if checked, so `roundtrip` translates each
model DuckDB -> Databricks -> DuckDB and executes both versions against the
local warehouse: equal results (as multisets, both directions) mean the
translation preserved the meaning on real data. That proves the translation
is faithful; it does not prove Databricks accepts every statement - only a
run on Databricks does, and the README says so.
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlglot

from . import runner


def transpile(sql: str, pretty: bool = True) -> str:
    return sqlglot.transpile(sql, read="duckdb", write="databricks", pretty=pretty)[0]


def roundtrip(con) -> list[dict]:
    out = []
    for m in runner.order(runner.discover()):
        if m.fn is not None:
            continue
        back = sqlglot.transpile(transpile(m.sql, pretty=False), read="databricks", write="duckdb")[0]
        a = con.execute(f"SELECT count(*) FROM (({m.sql}) EXCEPT ALL ({back}))").fetchone()[0]
        b = con.execute(f"SELECT count(*) FROM (({back}) EXCEPT ALL ({m.sql}))").fetchone()[0]
        n = con.execute(f"SELECT count(*) FROM ({m.sql})").fetchone()[0]
        out.append({"model": m.name, "rows": n, "only_original": a, "only_roundtrip": b, "equal": a == b == 0})
    return out


def _q(s) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def uc_ddl(catalog: dict) -> str:
    """COMMENT and TAG statements from the generated catalog, for Unity Catalog."""
    lines = ["-- Generated from catalog.json. Run after the tables exist.", ""]
    for t in catalog["tables"]:
        if t["layer"] not in ("silver", "gold", "staging"):
            continue
        name = t["name"]
        if t.get("description"):
            lines.append(f"COMMENT ON TABLE {name} IS {_q(t['description'])};")
        tags = {"owner": t.get("owner") or "", "layer": t["layer"]}
        if t.get("grain"):
            tags["grain"] = t["grain"]
        lines.append(f"ALTER TABLE {name} SET TAGS ({', '.join(f'{_q(k)} = {_q(v)}' for k, v in tags.items())});")
        for c in t["columns"]:
            if c.get("description"):
                lines.append(f"ALTER TABLE {name} ALTER COLUMN {c['name']} COMMENT {_q(c['description'])};")
            if c.get("pii"):
                lines.append(f"ALTER TABLE {name} ALTER COLUMN {c['name']} SET TAGS ('pii' = 'true');")
        lines.append("")
    return "\n".join(lines)


def export(out_dir: str | Path, catalog: dict | None = None) -> dict:
    out = Path(out_dir)
    (out / "sql").mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, m in enumerate(runner.order(runner.discover())):
        if m.fn is not None:
            manifest.append({"step": i, "kind": "python", "name": m.name, "outputs": m.outputs})
            continue
        fname = f"{i:02d}_{m.name}.sql"
        (out / "sql" / fname).write_text(transpile(m.sql) + "\n")
        manifest.append({"step": i, "kind": "sql", "name": m.name, "file": f"sql/{fname}"})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if catalog:
        (out / "uc_governance.sql").write_text(uc_ddl(catalog))
    return {"models": len(manifest), "sql": sum(1 for x in manifest if x["kind"] == "sql")}
