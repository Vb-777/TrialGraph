import duckdb

from trialgraph import bronze, pipeline
from conftest import BATCHES


def test_quarantine_and_counts(built):
    first, second = built["reports"]
    assert first["ingest"][0] == {**first["ingest"][0], "received": 400, "inserted": 397, "quarantined": 3}
    assert second["ingest"][0]["inserted"] == 50


def test_quarantine_reconciles(con):
    raw = con.execute("SELECT count(*) FROM bronze.raw_studies").fetchone()[0]
    q = con.execute("SELECT count(*) FROM bronze.quarantine").fetchone()[0]
    assert raw + q == 450  # every record received is stored or quarantined, none lost
    assert q == 3


def test_reingest_is_idempotent(built, tmp_path):
    db = tmp_path / "w.duckdb"
    pipeline.build(db, BATCHES)
    con = duckdb.connect(str(db))
    before = con.execute("SELECT count(*) FROM bronze.raw_studies").fetchone()[0]
    r = bronze.ingest(con, bronze.load_file(BATCHES[1]), source="again")
    after = con.execute("SELECT count(*) FROM bronze.raw_studies").fetchone()[0]
    assert r["inserted"] == 0 and r["unchanged"] == 50 and before == after


def test_latest_version_wins(con):
    # batch 2 revises 30 trials; silver keeps exactly one row per trial
    n, d = con.execute("SELECT count(*), count(DISTINCT nct_id) FROM silver.trial").fetchone()
    assert n == d == con.execute("SELECT count(DISTINCT nct_id) FROM bronze.raw_studies").fetchone()[0]
    revised = con.execute("SELECT count(*) FROM (SELECT nct_id FROM bronze.raw_studies "
                          "GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
    assert revised == 30


def test_rebuild_is_deterministic(built, tmp_path):
    a, b = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    pipeline.build(a, BATCHES)
    pipeline.build(b, BATCHES)
    ca, cb = duckdb.connect(str(a)), duckdb.connect(str(b))
    for t in ("gold.fct_trial", "gold.dim_intervention", "gold.dim_sponsor", "gold.brg_trial_condition"):
        cols = ", ".join(r[0] for r in ca.execute(f"DESCRIBE {t}").fetchall() if r[0] not in ("ingested_at", "batch_id"))
        assert ca.execute(f"SELECT {cols} FROM {t} ORDER BY ALL").fetchall() == \
               cb.execute(f"SELECT {cols} FROM {t} ORDER BY ALL").fetchall(), t
