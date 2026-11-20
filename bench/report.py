"""Reproduce every number quoted in the README from a raw export.

    python bench/report.py path/to/ctg-studies.json [--out bench/results]

Builds a fresh warehouse in a temp dir, then measures. Nothing in the README is
typed by hand; if the data changes, rerun this and copy the table.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trialgraph import databricks, evaluate, ontology, pipeline, semantic  # noqa: E402
from trialgraph.tools import Tools  # noqa: E402

HF = "D006333"


def one(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="+")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    ns = ap.parse_args(argv)
    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    db = Path(tempfile.mkdtemp()) / "warehouse.duckdb"

    t = time.perf_counter()
    rep = pipeline.build(db, ns.raw, out_dir=out)
    R = {"build_seconds": round(time.perf_counter() - t, 1),
         "stage_seconds": {k: round(v, 1) for k, v in rep["timings_s"].items()},
         "ingest": rep["ingest"], "quality": rep["quality"]["summary"], "catalog": rep["catalog_stats"]}
    res = rep["models"]["python"]["silver.map_sponsor"]
    R["resolution"] = {k: res[k] for k in ("sponsor_raw_names", "sponsor_entities", "sponsors_with_parent",
                                            "intervention_raw_names", "intervention_entities", "links_asserted",
                                            "links_corroborated", "links_alias_attached", "links_rejected")}
    con = duckdb.connect(str(db), read_only=True)

    # ---- data profile
    R["profile"] = {
        "trials": one(con, "SELECT count(*) FROM gold.fct_trial"),
        "start_month_precision_pct": round(100 * one(con, "SELECT avg(CASE WHEN start_date_precision='month' THEN 1 ELSE 0 END) FROM silver.trial"), 1),
        "start_date_missing": one(con, "SELECT count(*) FROM silver.trial WHERE start_date IS NULL"),
        "trials_with_central_contacts": one(con, "SELECT count(*) FROM gold.fct_trial WHERE has_central_contact"),
        "parent_links": [list(r) for r in con.execute(
            "SELECT sponsor_name, parent_sponsor_name FROM gold.dim_sponsor WHERE parent_sponsor_key IS NOT NULL ORDER BY 1").fetchall()],
    }

    # ---- entity lookup
    T = Tools(con)
    R["lookups"] = {}
    for name in ("Entresto", "LCZ696", "Farxiga", "Jardiance", "Notarealumab"):
        r = T.call("resolve_intervention", {"name": name})
        R["lookups"][name] = {k: r.get(k) for k in ("status", "intervention", "trials", "match_confidence")}

    # ---- fan-out
    terms = dict(con.execute("SELECT mesh_id, mesh_term FROM gold.dim_condition").fetchall())
    ids = [i for i in (HF, "D054143", "D054144") if i in terms]
    where = {"condition_direct": [terms[i] for i in ids]}
    fan = {}
    for m in ("trials", "actual_enrollment", "planned_enrollment"):
        safe = semantic.query(con, m, where=where)[0][m]
        naive = one(con, semantic.naive_sql(m, where))
        fan[m] = {"governed": safe, "naive_join": naive,
                  "inflation_pct": round(100 * (naive - safe) / safe, 2) if safe else None}
    R["fanout"] = {"filter": [terms[i] for i in ids], **fan}

    # ---- distribution
    top = con.execute("SELECT nct_id, enrollment_actual FROM gold.fct_trial ORDER BY enrollment_actual DESC NULLS LAST LIMIT 1").fetchone()
    total = one(con, "SELECT sum(enrollment_actual) FROM gold.fct_trial")
    R["enrollment"] = {"largest_trial": top[0], "largest_actual": top[1], "share_pct": round(100 * top[1] / total, 1),
                       "median_actual": semantic.query(con, "median_actual_enrollment")[0]["median_actual_enrollment"]}

    # ---- MeSH vs free text
    mesh = {r[0] for r in con.execute("SELECT DISTINCT nct_id FROM gold.brg_trial_condition WHERE mesh_id = ?", [HF]).fetchall()}
    text = {r[0] for r in con.execute("SELECT DISTINCT nct_id FROM gold.brg_trial_condition_text "
                                      "WHERE condition_text_lower LIKE '%heart failure%'").fetchall()}
    nomesh = {r[0] for r in con.execute("SELECT nct_id FROM gold.fct_trial f WHERE NOT EXISTS "
                                        "(SELECT 1 FROM gold.brg_trial_condition b WHERE b.nct_id=f.nct_id)").fetchall()}
    R["mesh_vs_text"] = {"both": len(mesh & text), "text_only": len(text - mesh),
                         "text_only_no_mesh_at_all": len((text - mesh) & nomesh), "mesh_only": len(mesh - text)}

    # ---- ontology parity
    t = time.perf_counter()
    g = ontology.materialise(con)
    v = ontology.validate(g)
    R["ontology"] = {"triples": len(g), "conforms": v["conforms"], "seconds": round(time.perf_counter() - t, 1),
                     "hf_direct_sparql": len(ontology.trials_in_area(g, HF, direct_only=True)),
                     "hf_direct_sql": one(con, "SELECT count(DISTINCT nct_id) FROM gold.brg_trial_condition WHERE mesh_id=? AND relation='direct'", [HF]),
                     "hf_area_sparql": len(ontology.trials_in_area(g, HF)),
                     "hf_area_sql": one(con, "SELECT count(DISTINCT nct_id) FROM gold.brg_trial_condition WHERE mesh_id=?", [HF])}

    # ---- a governed question
    R["termination_rate_by_class"] = {r["sponsor_class"]: round(100 * r["termination_rate"], 1)
                                      for r in semantic.query(con, ["termination_rate", "trials"], ["sponsor_class"])
                                      if r["trials"] >= 100 and r["termination_rate"] is not None}

    # ---- evaluation and translation
    ev = evaluate.run(con, ns.raw)
    R["evaluation"] = ev["summary"]
    R["evaluation_detail"] = {c["id"]: {"expected": c["expected"], "got": c["got"], "note": c["note"]}
                              for c in ev["cases"] if c["category"] in ("resolution", "retrieval")}
    rt = databricks.roundtrip(con)
    R["databricks_roundtrip"] = {"models": len(rt), "equal": sum(x["equal"] for x in rt)}

    (out / "report.json").write_text(json.dumps(R, indent=2, default=str))
    (out / "evaluation.json").write_text(json.dumps(ev, indent=2, default=str))
    print(json.dumps(R, indent=2, default=str))


if __name__ == "__main__":
    main()
