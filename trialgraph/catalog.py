"""Build the data catalog: every table, every column, where it came from.

The catalog joins three things that usually live in different tools and drift
apart: the physical schema (read from the warehouse, so it is never stale),
the human documentation (`config/catalog.yml`), and lineage (derived from the
code). Each gold column therefore carries its type, its meaning, its owner,
whether it is personal data, and the exact source JSON paths its value comes
from - which is what an engineer needs before changing it and what an agent
needs before trusting it.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from . import lineage
from .runner import discover

CONFIG = Path(__file__).resolve().parent / "config" / "catalog.yml"
LAYER = {"bronze": "bronze", "staging": "staging", "silver": "silver", "gold": "gold", "meta": "meta"}


def build(con, config: Path = CONFIG) -> dict:
    cfg = yaml.safe_load(config.read_text())
    owner = cfg.get("owner_default")
    docs = cfg.get("tables") or {}
    graph = lineage.Graph(lineage.collect(con))
    deps = {o: sorted(m.deps) for m in discover() for o in m.outputs}

    tables = []
    for s, t in con.execute("SELECT table_schema, table_name FROM information_schema.tables "
                            "WHERE table_schema IN ('bronze','staging','silver','gold','meta') "
                            "AND table_type = 'BASE TABLE' ORDER BY 1, 2").fetchall():
        name = f"{s}.{t}"
        d = docs.get(name, {})
        cdocs = d.get("columns") or {}
        cols = []
        for c, ty in con.execute("SELECT column_name, data_type FROM information_schema.columns "
                                 "WHERE table_schema=? AND table_name=? ORDER BY ordinal_position",
                                 [s, t]).fetchall():
            full = f"{name}.{c}"
            cd = cdocs.get(c, {})
            cols.append({
                "name": c, "type": ty,
                "description": cd.get("description"),
                "pii": bool(cd.get("pii")),
                "inputs": sorted(graph.up.get(full, ())),
                "sources": sorted(x.removeprefix("source:") for x in graph.sources(full)),
            })
        tables.append({
            "name": name, "layer": LAYER.get(s, s),
            "description": d.get("description"), "grain": d.get("grain"),
            "owner": d.get("owner", owner), "tags": d.get("tags", []),
            "row_count": con.execute(f"SELECT count(*) FROM {name}").fetchone()[0],
            "upstream_tables": deps.get(name, []),
            "columns": cols,
        })

    gold_cols = [c for tb in tables if tb["layer"] == "gold" for c in tb["columns"]]
    stats = {
        "tables": len(tables),
        "columns": sum(len(tb["columns"]) for tb in tables),
        "gold_columns": len(gold_cols),
        "gold_documented": sum(1 for c in gold_cols if c["description"]),
        "gold_traced_to_source": sum(1 for c in gold_cols if c["sources"]),
        "lineage_edges": sum(len(v) for v in graph.up.values()),
        "pii_columns": sum(1 for tb in tables for c in tb["columns"] if c["pii"]),
    }
    return {"stats": stats, "tables": tables}


def write(cat: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "catalog.json").write_text(json.dumps(cat, indent=2, default=str))
    lines = ["# TrialGraph data catalog (gold layer)", ""]
    s = cat["stats"]
    lines.append(f"{s['tables']} tables, {s['columns']} columns, {s['lineage_edges']} lineage edges. "
                 f"Gold: {s['gold_documented']}/{s['gold_columns']} columns documented, "
                 f"{s['gold_traced_to_source']}/{s['gold_columns']} traced to a source JSON path.")
    for tb in cat["tables"]:
        if tb["layer"] != "gold":
            continue
        lines += ["", f"## `{tb['name']}`", "", f"{tb['description'] or '_undocumented_'}  ",
                  f"Grain: `{tb['grain']}` · Rows: {tb['row_count']:,} · Owner: {tb['owner']}  ",
                  f"Built from: {', '.join(f'`{u}`' for u in tb['upstream_tables']) or '-'}", "",
                  "| column | type | description | source path(s) |", "|---|---|---|---|"]
        for c in tb["columns"]:
            src = "<br>".join(f"`{x}`" for x in c["sources"]) or "_derived_"
            lines.append(f"| `{c['name']}` | {c['type']} | {c['description'] or '_undocumented_'} | {src} |")
    (out_dir / "catalog.md").write_text("\n".join(lines) + "\n")
