"""Column-level lineage from source JSON path to gold column.

Three sources of edges, one graph:

* staging   - declared in `staging.SPEC`: which JSON path fed which column
* python    - declared beside each Python model (`resolve.LINEAGE`, `chunks.LINEAGE`)
* SQL       - derived by parsing each model with sqlglot, so it cannot drift
              from the code the way hand-written lineage does

This is *value* lineage: it follows the expressions that compute a column's
value. Columns used only to filter or join (a WHERE clause, an ON condition)
shape which rows exist but do not flow into values, and are recorded
separately at table level. The distinction is what lets
`trial_contact_flag.has_central_contact` be derived from whether a contact row
exists without counting as a descendant of the contact's name or email.
"""
from __future__ import annotations

from collections import defaultdict, deque

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as sqlglot_lineage

from . import chunks, resolve, staging
from .runner import DIALECT, discover


def schema_of(con) -> dict:
    out: dict = {}
    for s, t, c, ty in con.execute(
            "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
            "ORDER BY table_schema, table_name, ordinal_position").fetchall():
        out.setdefault(s, {}).setdefault(t, {})[c] = ty
    return out


def sql_edges(name: str, sql: str, schema: dict) -> list[dict]:
    s, t = name.split(".")
    edges = []
    for col in schema.get(s, {}).get(t, {}):
        try:
            node = sqlglot_lineage(col, sql, schema=schema, dialect=DIALECT)
        except SqlglotError:
            # The table has a column the model's SQL does not produce (added by
            # hand, or stale). It gets no edges, so it shows up as untraced.
            continue
        for leaf in node.walk():
            if leaf.downstream:
                continue
            src = leaf.source
            if isinstance(src, exp.Table) and src.name:
                frm = f"{src.db}.{src.name}.{leaf.name.split('.')[-1]}"
            else:
                frm = "constant:" + leaf.name.split(".")[-1]
            edges.append({"from": frm, "to": f"{name}.{col}"})
    return edges


def collect(con) -> list[dict]:
    schema = schema_of(con)
    edges = staging.lineage_edges()
    edges += [{"from": a, "to": b} for a, b in resolve.LINEAGE + chunks.LINEAGE]
    for m in discover():
        if m.sql:
            edges += sql_edges(m.name, m.sql, schema)
    seen, out = set(), []
    for e in edges:
        k = (e["from"], e["to"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


class Graph:
    def __init__(self, edges: list[dict]):
        self.up: dict[str, set[str]] = defaultdict(set)
        self.down: dict[str, set[str]] = defaultdict(set)
        for e in edges:
            self.up[e["to"]].add(e["from"])
            self.down[e["from"]].add(e["to"])

    def _walk(self, start: str, nbrs: dict) -> set[str]:
        seen, q = set(), deque([start])
        while q:
            for n in nbrs.get(q.popleft(), ()):
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return seen

    def upstream(self, col: str) -> set[str]:
        return self._walk(col, self.up)

    def downstream(self, col: str) -> set[str]:
        return self._walk(col, self.down)

    def sources(self, col: str) -> set[str]:
        """The original JSON paths a column ultimately comes from."""
        return {n for n in self.upstream(col) if n.startswith("source:")}


def pii_violations(graph: Graph, pii_columns: list[str], protected_prefix: str = "gold.") -> list[dict]:
    out = []
    for p in pii_columns:
        for d in sorted(graph.downstream(p)):
            if d.startswith(protected_prefix):
                out.append({"pii_column": p, "reaches": d})
    return out
