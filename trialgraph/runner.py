"""Run the silver and gold models in dependency order.

SQL models are plain SELECT statements in `models/<schema>/<name>.sql`. The
runner reads which tables each one selects from by parsing it, so the run
order is derived from the code rather than maintained by hand beside it - a
hand-kept order is correct on the day it is written and wrong the first time
someone adds a join. Python models (entity resolution, chunking) declare their
inputs and outputs explicitly, because there is no SQL to parse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Callable

import sqlglot
from sqlglot import exp

from . import chunks, resolve

MODELS_DIR = Path(__file__).resolve().parent / "models"
DIALECT = "duckdb"


@dataclass
class Model:
    name: str                       # primary output, e.g. "gold.fct_trial"
    outputs: list[str]
    deps: set[str]
    sql: str | None = None
    fn: Callable | None = None
    path: Path | None = None
    meta: dict = field(default_factory=dict)


def sql_dependencies(sql: str) -> set[str]:
    tree = sqlglot.parse_one(sql, read=DIALECT)
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    out = set()
    for t in tree.find_all(exp.Table):
        if t.name in ctes and not t.db:
            continue
        out.add(f"{t.db}.{t.name}" if t.db else t.name)
    return out


def discover() -> list[Model]:
    models = []
    for path in sorted(MODELS_DIR.glob("*/*.sql")):
        name = f"{path.parent.name}.{path.stem}"
        sql = path.read_text()
        models.append(Model(name, [name], sql_dependencies(sql), sql=sql, path=path))
    models.append(Model("silver.map_sponsor", ["silver.map_sponsor", "silver.map_intervention",
                                               "silver.intervention_alias", "meta.rejected_links"],
                        {"staging.stg_sponsors", "staging.stg_interventions"}, fn=resolve.build))
    models.append(Model("gold.rag_chunks", ["gold.rag_chunks"], {"staging.stg_studies"}, fn=chunks.build))
    return models


def order(models: list[Model]) -> list[Model]:
    producer = {o: m for m in models for o in m.outputs}
    graph = {m.name: {producer[d].name for d in m.deps if d in producer} for m in models}
    by_name = {m.name: m for m in models}
    return [by_name[n] for n in TopologicalSorter(graph).static_order()]


def run(con, models: list[Model] | None = None) -> dict:
    models = order(models or discover())
    report = {"order": [], "python": {}}
    for m in models:
        schema = m.name.split(".")[0]
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        if m.fn is not None:
            report["python"][m.name] = m.fn(con)
        else:
            con.execute(f"CREATE OR REPLACE TABLE {m.name} AS {m.sql}")
        report["order"].append(m.name)
    return report
