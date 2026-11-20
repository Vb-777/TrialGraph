"""Compile governed metric requests into SQL that cannot fan out.

The failure this module exists to prevent is the commonest wrong number in
analytics. A trial maps to several conditions, several countries, several
interventions. Join the fact to any of those and sum, and every trial is
counted once per matching row: filter to "Heart Failure or Heart Failure,
Systolic" and a trial mapped to both contributes its enrollment twice. The
query runs, the number looks plausible, and it is wrong.

The compiler is correct by construction rather than by care:

1. Fan-out filters become semi-joins (EXISTS), so they decide *whether* a
   trial qualifies and never multiply its rows.
2. Grouping by a fan-out dimension first takes DISTINCT (trial, value) pairs,
   so a trial is counted exactly once per group it belongs to.
3. Every metric then aggregates over trial-grain facts joined to those pairs.

`naive_sql` writes the same request the obvious way, so the size of the error
it avoids can be measured on real data rather than asserted.
"""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent / "config" / "metrics.yml"


class SemanticError(ValueError):
    pass


def load(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def _lit(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _in(expr: str, vals) -> str:
    vals = vals if isinstance(vals, (list, tuple, set)) else [vals]
    if not vals:
        raise SemanticError("empty filter value list")
    return f"{expr} IN ({', '.join(_lit(v) for v in vals)})"


def _agg(m: dict) -> str:
    t = m["type"]
    if t == "count_distinct":
        return f"count(DISTINCT {m['expr']})"
    if t == "sum":
        return f"sum({m['expr']})"
    if t == "median":
        return f"median({m['expr']})"
    if t == "ratio":
        return (f"CAST(sum(CASE WHEN {m['numerator']} THEN 1 ELSE 0 END) AS DOUBLE) / "
                f"NULLIF(sum(CASE WHEN {m['denominator']} THEN 1 ELSE 0 END), 0)")
    raise SemanticError(f"unknown metric type {t!r}")


def compile(metric: str | list[str], by: list[str] | None = None, where: dict | None = None,
            spec: dict | None = None, order: bool = True) -> str:
    spec = spec or load()
    metrics = [metric] if isinstance(metric, str) else list(metric)
    by, where = list(by or []), dict(where or {})
    dims, mdefs = spec["dimensions"], spec["metrics"]
    for m in metrics:
        if m not in mdefs:
            raise SemanticError(f"unknown metric {m!r}; known: {sorted(mdefs)}")
    for d in list(by) + list(where):
        if d not in dims:
            raise SemanticError(f"unknown dimension {d!r}; known: {sorted(dims)}")

    # Trial-grain joins needed by any trial-grain dimension used anywhere.
    joins = []
    for d in list(by) + list(where):
        j = dims[d].get("join")
        if j and spec["joins"][j] not in joins:
            joins.append(spec["joins"][j])

    conds = []
    for d, v in where.items():
        dd = dims[d]
        if dd["grain"] == "trial":
            conds.append(_in(dd["sql"], v))
        else:
            extra = f" AND {dd['where']}" if dd.get("where") else ""
            conds.append(f"EXISTS (SELECT 1 FROM {dd['from']} WHERE {dd['key']} = f.nct_id"
                         f"{extra} AND {_in(dd['sql'], v)})")

    base = ("SELECT f.*" + "".join(f", {dims[d]['sql']} AS __d{i}" for i, d in enumerate(by)
                                   if dims[d]["grain"] == "trial")
            + " FROM gold.fct_trial f " + " ".join(joins)
            + (" WHERE " + " AND ".join(conds) if conds else ""))

    fan = [(i, d) for i, d in enumerate(by) if dims[d]["grain"] == "fanout"]
    sel_dims = [f"b.__d{i} AS {d}" if dims[d]["grain"] == "trial" else f"k.__d{i} AS {d}"
                for i, d in enumerate(by)]
    aggs = [f"{_agg(mdefs[m]).replace('f.', 'b.')} AS {m}" for m in metrics]

    if fan:
        # DISTINCT (trial, value...) pairs: one row per trial per group, never more.
        kparts, kfrom = [], []
        for i, d in fan:
            dd = dims[d]
            alias = f"x{i}"
            extra = f" WHERE {dd['where']}" if dd.get("where") else ""
            kfrom.append(f"(SELECT DISTINCT {dd['key']} AS nct_id, {dd['sql']} AS __d{i} "
                         f"FROM {dd['from']}{extra}) {alias}")
            kparts.append(f"{alias}.__d{i}")
        first = kfrom[0].rsplit(" ", 1)[1]
        kjoin = kfrom[0] + "".join(f" JOIN {k} ON {k.rsplit(' ', 1)[1]}.nct_id = {first}.nct_id"
                                   for k in kfrom[1:])
        keyed = f"SELECT DISTINCT {first}.nct_id, {', '.join(kparts)} FROM {kjoin}"
        sql = (f"WITH b AS ({base}), k AS ({keyed}) "
               f"SELECT {', '.join(sel_dims + aggs)} FROM k JOIN b ON b.nct_id = k.nct_id")
    else:
        sql = f"WITH b AS ({base}) SELECT {', '.join(sel_dims + aggs)} FROM b"

    if by:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(by)))
        if order:
            sql += f" ORDER BY {len(by) + 1} DESC NULLS LAST, 1"
    return sql


def query(con, metric, by=None, where=None, spec=None) -> list[dict]:
    sql = compile(metric, by, where, spec)
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def naive_sql(metric: str, where: dict, spec: dict | None = None) -> str:
    """The obvious query: join everything, filter, aggregate. Fans out."""
    spec = spec or load()
    dims, m = spec["dimensions"], spec["metrics"][metric]
    joins, conds = [], []
    for d, v in where.items():
        dd = dims[d]
        if dd["grain"] == "trial":
            if dd.get("join"):
                joins.append(spec["joins"][dd["join"]])
        else:
            joins.append(f"JOIN {dd['from']} ON {dd['key']} = f.nct_id")
            if dd.get("where"):
                conds.append(dd["where"])
        conds.append(_in(dd["sql"], v))
    return (f"SELECT {_agg(m)} FROM gold.fct_trial f {' '.join(joins)}"
            + (" WHERE " + " AND ".join(conds) if conds else ""))


def trials_sql(where: dict | None = None, spec: dict | None = None) -> str:
    """SQL listing the trials that satisfy `where` - one row per trial, no fan-out."""
    spec = spec or load()
    sql = compile("trials", None, where, spec)
    # Reuse the governed filter logic exactly: same CTE, project trial IDs instead.
    return sql.replace("SELECT count(DISTINCT b.nct_id) AS trials FROM b",
                       "SELECT b.nct_id, b.brief_title, b.overall_status, b.phase_label, b.start_year FROM b")
