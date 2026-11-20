"""The tool contract an agent uses to answer questions from TrialGraph.

This is the interface between the data foundation and any language model. It
is designed around three rules, each of which the evaluation checks:

* Every answer is grounded. Responses carry `citations` (NCT IDs a reader can
  open on ClinicalTrials.gov) and `provenance` (the exact SQL, or the chunk
  IDs, that produced them). An answer without a citation is not an answer.
* Every number is governed. Counts and sums go through the semantic layer, so
  the agent cannot write its own SQL and fan a join out; it can only ask for
  a defined metric, and it is handed that metric's definition with the result.
* Unknown means unknown. Names are resolved by exact lookup against the alias
  table, never by fuzzy similarity. "Not found" is a correct answer; the
  nearest wrong drug is not.

`SPECS` uses a provider-neutral JSON-schema tool format, so the same governed
contract can be adapted to different language-model clients.
"""
from __future__ import annotations

import re

from . import resolve, semantic
from .retrieval import FILTERABLE, ChunkIndex

_NCT = re.compile(r"^NCT\d{8}$")

_WHERE = {
    "type": "object",
    "description": "Filters. Keys are dimensions (see `describe`); values are a value or a list of values.",
    "additionalProperties": {"anyOf": [{"type": ["string", "number", "boolean"]},
                                       {"type": "array", "items": {"type": ["string", "number", "boolean"]}}]},
}

SPECS = [
    {"name": "describe",
     "description": "List the governed metrics and dimensions with their definitions. Call this first.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "metric",
     "description": "Compute a governed metric, optionally grouped and filtered. Never write SQL yourself.",
     "input_schema": {"type": "object", "required": ["metric"], "properties": {
         "metric": {"type": "string"},
         "by": {"type": "array", "items": {"type": "string"}},
         "where": _WHERE}}},
    {"name": "find_trials",
     "description": "List trials matching filters, with the total count.",
     "input_schema": {"type": "object", "properties": {
         "where": _WHERE, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "get_trial",
     "description": "Full governed record for one trial by NCT ID.",
     "input_schema": {"type": "object", "required": ["nct_id"], "properties": {"nct_id": {"type": "string"}}}},
    {"name": "resolve_intervention",
     "description": "Resolve a drug or intervention name (code, generic or brand) to its entity. Exact match only.",
     "input_schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "resolve_sponsor",
     "description": "Resolve a sponsor name to its entity and stated parent. Exact match after normalisation.",
     "input_schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "search_text",
     "description": "Search trial summaries and eligibility text. Filters apply before ranking.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
         "where": {"type": "object", "description": f"Optional: {sorted(FILTERABLE)}"},
         "k": {"type": "integer", "minimum": 1, "maximum": 20}}}},
]


class Tools:
    def __init__(self, con):
        self.con = con
        self.spec = semantic.load()
        self._index = None

    # --------------------------------------------------------------- helpers
    def _rows(self, sql: str, params=None) -> list[dict]:
        cur = self.con.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @staticmethod
    def _err(msg: str) -> dict:
        return {"status": "error", "error": msg, "citations": []}

    def call(self, name: str, args: dict | None = None) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return self._err(f"unknown tool {name!r}")
        try:
            return fn(**(args or {}))
        except (semantic.SemanticError, ValueError, TypeError) as e:
            return self._err(str(e))

    # ----------------------------------------------------------------- tools
    def t_describe(self) -> dict:
        return {"status": "ok", "citations": [],
                "metrics": {k: " ".join(v.get("description", "").split()) for k, v in self.spec["metrics"].items()},
                "dimensions": {k: v.get("description", v["grain"]) for k, v in self.spec["dimensions"].items()}}

    def t_metric(self, metric, by=None, where=None) -> dict:
        sql = semantic.compile(metric, by, where, self.spec)
        rows = self._rows(sql)
        sample = [r["nct_id"] for r in self._rows(
            semantic.trials_sql(where, self.spec) + " ORDER BY b.nct_id LIMIT 10")]
        metrics = [metric] if isinstance(metric, str) else metric
        return {"status": "ok", "rows": rows, "citations": sample,
                "definitions": {m: " ".join(self.spec["metrics"][m].get("description", "").split())
                                for m in metrics},
                "provenance": {"sql": sql}}

    def t_find_trials(self, where=None, limit=10) -> dict:
        sql = semantic.trials_sql(where, self.spec)
        total = self.con.execute(f"SELECT count(*) FROM ({sql}) t").fetchone()[0]
        rows = self._rows(sql + f" ORDER BY b.nct_id LIMIT {int(limit)}")
        return {"status": "ok" if total else "not_found", "total": total, "trials": rows,
                "citations": [r["nct_id"] for r in rows], "provenance": {"sql": sql}}

    def t_get_trial(self, nct_id) -> dict:
        nct = str(nct_id).strip().upper()
        if not _NCT.match(nct):
            return self._err(f"{nct_id!r} is not an NCT ID (NCT followed by 8 digits)")
        rec = self._rows("SELECT f.*, s.sponsor_name AS lead_sponsor, s.group_sponsor_name AS sponsor_group "
                         "FROM gold.fct_trial f LEFT JOIN gold.dim_sponsor s ON s.sponsor_key = f.lead_sponsor_key "
                         "WHERE f.nct_id = ?", [nct])
        if not rec:
            return {"status": "not_found", "nct_id": nct, "citations": []}
        r = rec[0]
        r["conditions_mesh"] = [x["mesh_term"] for x in self._rows(
            "SELECT dc.mesh_term FROM gold.brg_trial_condition b JOIN gold.dim_condition dc "
            "ON dc.mesh_id = b.mesh_id WHERE b.nct_id = ? AND b.relation = 'direct' ORDER BY 1", [nct])]
        r["conditions_text"] = [x["condition_text"] for x in self._rows(
            "SELECT condition_text FROM gold.brg_trial_condition_text WHERE nct_id = ? ORDER BY 1", [nct])]
        r["interventions"] = [x["intervention_name"] for x in self._rows(
            "SELECT di.intervention_name FROM gold.brg_trial_intervention b JOIN gold.dim_intervention di "
            "ON di.intervention_key = b.intervention_key WHERE b.nct_id = ? ORDER BY 1", [nct])]
        r["countries"] = [x["country"] for x in self._rows(
            "SELECT country FROM gold.brg_trial_country WHERE nct_id = ? ORDER BY 1", [nct])]
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
        return {"status": "ok", "trial": r, "citations": [nct]}

    def t_resolve_intervention(self, name) -> dict:
        key = resolve.name_key(str(name))
        hit = self._rows("SELECT intervention_key, intervention_name, confidence FROM silver.intervention_alias "
                         "WHERE alias_key = ?", [key])
        if not hit:
            return {"status": "not_found", "query": name, "citations": [],
                    "note": "No exact match. Not guessing a similar name."}
        h = hit[0]
        aliases = [r["alias_key"] for r in self._rows(
            "SELECT alias_key FROM silver.intervention_alias WHERE intervention_key = ? ORDER BY 1",
            [h["intervention_key"]])]
        n = self.con.execute("SELECT count(DISTINCT nct_id) FROM gold.brg_trial_intervention WHERE intervention_key = ?",
                             [h["intervention_key"]]).fetchone()[0]
        return {"status": "ok", "query": name, "intervention": h["intervention_name"],
                "intervention_key": h["intervention_key"], "match_confidence": h["confidence"],
                "known_names": aliases, "trials": n, "citations": []}

    def t_resolve_sponsor(self, name) -> dict:
        child, _ = resolve.parse_corporate(str(name))
        key = resolve.sponsor_key(child)
        hit = self._rows("SELECT * FROM gold.dim_sponsor WHERE sponsor_key = ?", [key])
        if not hit:
            hit = self._rows("SELECT d.* FROM gold.dim_sponsor d JOIN silver.map_sponsor m "
                             "ON m.sponsor_key = d.sponsor_key WHERE lower(m.sponsor_name_raw) = lower(?) LIMIT 1",
                             [str(name)])
        if not hit:
            return {"status": "not_found", "query": name, "citations": [],
                    "note": "No exact match. Not guessing a similar name."}
        return {"status": "ok", "query": name, "sponsor": hit[0], "citations": []}

    def t_search_text(self, query, where=None, k=5) -> dict:
        if self._index is None:
            self._index = ChunkIndex(self.con)
        hits = self._index.search(query, where, int(k))
        return {"status": "ok" if hits else "not_found", "hits": hits,
                "citations": sorted({h["nct_id"] for h in hits}),
                "provenance": {"chunk_ids": [h["chunk_id"] for h in hits]}}
