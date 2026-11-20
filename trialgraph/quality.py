"""Run declarative data-quality checks and produce a scorecard.

Checks are data (`config/checks.yml`), and each one compiles to a query that
counts *failing rows*, so a result is never just pass or fail: it says how
many rows broke the rule and shows a few of them. A count is what tells you
whether a failure is a stray record or a broken pipeline.

Severity decides consequences. `error` checks guard properties downstream
consumers rely on - uniqueness, referential integrity, no personal data in
gold - and fail the build. `warn` checks report problems that live in the
source registry itself, such as completion dates before start dates, which
the pipeline should surface but cannot fix.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import lineage

CONFIG = Path(__file__).resolve().parent / "config"


class QualityError(RuntimeError):
    def __init__(self, failures: list[dict]):
        self.failures = failures
        super().__init__(f"{len(failures)} error-severity check(s) failed: "
                         + ", ".join(f["id"] for f in failures))


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _has(con, table: str, col: str) -> bool:
    s, t = table.split(".")
    return bool(con.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=? "
                            "AND table_name=? AND column_name=?", [s, t, col]).fetchone()[0])


def compile_check(table: str, c: dict) -> tuple[str, str]:
    """Check -> (id, SQL returning the failing rows)."""
    t = c["type"]
    if t == "not_null":
        return f"{table}.{c['column']}.not_null", f"SELECT * FROM {table} WHERE {c['column']} IS NULL"
    if t == "unique":
        cols = ", ".join(c["columns"])
        return (f"{table}.{'+'.join(c['columns'])}.unique",
                f"SELECT {cols}, count(*) AS n FROM {table} GROUP BY {cols} HAVING count(*) > 1")
    if t == "accepted_values":
        vals = ", ".join(_q(v) for v in c["values"])
        return (f"{table}.{c['column']}.accepted_values",
                f"SELECT * FROM {table} WHERE {c['column']} IS NOT NULL AND {c['column']} NOT IN ({vals})")
    if t == "regex":
        return (f"{table}.{c['column']}.regex",
                f"SELECT * FROM {table} WHERE {c['column']} IS NOT NULL "
                f"AND NOT regexp_matches({c['column']}, {_q(c['pattern'])})")
    if t == "relationship":
        tt, tc = c["to"].rsplit(".", 1)
        return (f"{table}.{c['column']}.relationship",
                f"SELECT s.* FROM {table} s LEFT JOIN {tt} p ON p.{tc} = s.{c['column']} "
                f"WHERE s.{c['column']} IS NOT NULL AND p.{tc} IS NULL")
    if t == "expression":
        return f"{table}.{c['name']}", f"SELECT * FROM {table} WHERE NOT ({c['sql']})"
    raise ValueError(f"unknown check type {t!r}")


def _result(cid, severity, failing, sample, why=None, kind="row"):
    status = "pass" if failing == 0 else ("fail" if severity == "error" else "warn")
    return {"id": cid, "severity": severity, "failing": failing, "status": status,
            "sample": sample, "why": why, "kind": kind}


def run(con, config: Path = CONFIG / "checks.yml", catalog: Path = CONFIG / "catalog.yml",
        raise_on_error: bool = True) -> dict:
    spec = yaml.safe_load(config.read_text())
    cat = yaml.safe_load(catalog.read_text())
    results = []

    for block in spec:
        if "table" in block:
            table = block["table"]
            for c in block["checks"]:
                cid, sql = compile_check(table, c)
                failing = con.execute(f"SELECT count(*) FROM ({sql}) f").fetchone()[0]
                sample = []
                if failing and _has(con, table, "nct_id") and c["type"] != "unique":
                    sample = [r[0] for r in con.execute(f"SELECT nct_id FROM ({sql}) f ORDER BY 1 LIMIT 5").fetchall()]
                elif failing:
                    sample = [list(r) for r in con.execute(f"SELECT * FROM ({sql}) f LIMIT 5").fetchall()]
                results.append(_result(cid, c["severity"], failing, sample, c.get("why")))

        elif "reconcile" in block:
            r = block["reconcile"]
            left = con.execute(r["left"]).fetchone()[0]
            right = con.execute(r["right"]).fetchone()[0]
            results.append(_result(f"reconcile.{r['name']}", r["severity"], abs(left - right),
                                   [{"left": left, "right": right}], r.get("why"), "reconcile"))

        elif block.get("governance") == "pii_lineage":
            graph = lineage.Graph(lineage.collect(con))
            pii = [f"{t}.{c}" for t, td in cat["tables"].items()
                   for c, cd in (td.get("columns") or {}).items() if cd.get("pii")]
            v = lineage.pii_violations(graph, pii)
            results.append(_result("governance.pii_lineage", block["severity"], len(v), v[:5],
                                   block.get("why"), "governance"))

        elif block.get("governance") == "gold_documented":
            documented = {f"{t}.{c}" for t, td in cat["tables"].items()
                          for c, cd in (td.get("columns") or {}).items() if cd.get("description")}
            actual = [f"gold.{t}.{c}" for t, c in con.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='gold' "
                "ORDER BY 1, 2").fetchall()]
            missing = [c for c in actual if c not in documented]
            results.append(_result("governance.gold_documented", block["severity"], len(missing), missing[:5],
                                   block.get("why"), "governance"))

    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "warn", "fail")}
    card = {"summary": summary, "checks": results}
    errors = [r for r in results if r["status"] == "fail"]
    if errors and raise_on_error:
        raise QualityError(errors)
    return card


def to_markdown(card: dict) -> str:
    s = card["summary"]
    lines = [f"**{s['pass']} pass · {s['warn']} warn · {s['fail']} fail**", "",
             "| check | severity | failing | status |", "|---|---|---:|---|"]
    for r in card["checks"]:
        lines.append(f"| `{r['id']}` | {r['severity']} | {r['failing']} | {r['status']} |")
    return "\n".join(lines)
