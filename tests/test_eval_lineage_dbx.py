import ast
from pathlib import Path

from trialgraph import catalog, databricks, evaluate, lineage, ontology
from conftest import BATCHES

PKG = Path(__file__).resolve().parents[1] / "trialgraph"


def test_reference_is_independent():
    tree = ast.parse((PKG / "reference.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "json", "zipfile", "collections", "pathlib"}, mods


def test_evaluation_passes_on_fixture(con):
    r = evaluate.run(con, [str(b) for b in BATCHES], retrieval_n=50)
    assert all(v["fail"] == 0 for v in r["summary"].values()), [c for c in r["cases"] if not c["pass"]]


def test_column_lineage_traces_gold_to_source(con):
    g = lineage.Graph(lineage.collect(con))
    src = g.sources("gold.fct_trial.enrollment_actual")
    assert any("enrollmentInfo" in s for s in src), src


def test_catalog_documents_every_gold_column(con):
    s = catalog.build(con)["stats"]
    assert s["gold_documented"] == s["gold_columns"]


def test_databricks_translation_roundtrips(con):
    r = databricks.roundtrip(con)
    assert r and all(x["equal"] for x in r), [x for x in r if not x["equal"]]


def test_uc_ddl_tags_pii(con):
    ddl = databricks.uc_ddl(catalog.build(con))
    assert "ALTER TABLE staging.stg_contacts ALTER COLUMN contact_email SET TAGS ('pii' = 'true');" in ddl


def test_ontology_conforms_and_agrees_with_sql(con):
    g = ontology.materialise(con)
    assert ontology.validate(g)["conforms"]
    mesh_id = con.execute("SELECT mesh_id FROM gold.brg_trial_condition GROUP BY 1 ORDER BY count(*) DESC LIMIT 1").fetchone()[0]
    sql = {r[0] for r in con.execute("SELECT DISTINCT nct_id FROM gold.brg_trial_condition WHERE mesh_id = ?", [mesh_id]).fetchall()}
    assert ontology.trials_in_area(g, mesh_id) == sql
