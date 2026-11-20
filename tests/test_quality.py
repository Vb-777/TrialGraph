"""Fault injection: each check must fire on the fault it exists to catch.
A check that has never been seen to fail has not been shown to work."""
import pytest

from trialgraph import lineage, quality
from trialgraph.runner import Model


def _status(card, cid):
    return next(c for c in card["checks"] if c["id"] == cid)


def test_clean_build_has_no_errors(built):
    for r in built["reports"]:
        assert r["quality"]["summary"]["fail"] == 0


def test_bad_status_is_flagged_not_dropped(con, built):
    # the fixture carries a "COMPLETE" typo; it must be kept and surfaced
    assert con.execute("SELECT count(*) FROM silver.trial WHERE overall_status = 'UNRECOGNISED' "
                       "AND overall_status_raw = 'COMPLETE'").fetchone()[0] == 1
    c = _status(built["reports"][-1]["quality"], "silver.trial.overall_status.accepted_values")
    assert c["status"] == "warn" and c["failing"] == 1


def test_duplicate_trial_fails(scratch):
    scratch.execute("INSERT INTO gold.fct_trial SELECT * FROM gold.fct_trial LIMIT 1")
    with pytest.raises(quality.QualityError) as e:
        quality.run(scratch)
    assert any("unique" in f["id"] for f in e.value.failures)


def test_orphan_bridge_row_fails(scratch):
    scratch.execute("INSERT INTO gold.brg_trial_country VALUES ('NCT00000000', 'Atlantis')")
    card = quality.run(scratch, raise_on_error=False)
    assert any(c["status"] == "fail" and "brg_trial_country" in c["id"] for c in card["checks"])


def test_reconcile_catches_lost_rows(scratch):
    scratch.execute("DELETE FROM silver.trial WHERE nct_id IN (SELECT nct_id FROM silver.trial LIMIT 2)")
    card = quality.run(scratch, raise_on_error=False)
    assert any(c["id"].startswith("reconcile.") and c["status"] == "fail" for c in card["checks"])


def test_undocumented_gold_column_fails(scratch):
    scratch.execute("ALTER TABLE gold.fct_trial ADD COLUMN mystery INTEGER")
    card = quality.run(scratch, raise_on_error=False)
    c = _status(card, "governance.gold_documented")
    assert c["status"] == "fail" and "gold.fct_trial.mystery" in c["sample"]


def test_pii_reaching_gold_fails(scratch, monkeypatch):
    leak = "SELECT c.nct_id, c.contact_email FROM staging.stg_contacts c"
    scratch.execute(f"CREATE TABLE gold.contact_leak AS {leak}")
    real = lineage.discover
    monkeypatch.setattr(lineage, "discover",
                        lambda: real() + [Model("gold.contact_leak", ["gold.contact_leak"],
                                                {"staging.stg_contacts"}, sql=leak)])
    card = quality.run(scratch, raise_on_error=False)
    c = _status(card, "governance.pii_lineage")
    assert c["status"] == "fail"
    assert {"pii_column": "staging.stg_contacts.contact_email", "reaches": "gold.contact_leak.contact_email"} in c["sample"]


def test_pii_does_not_reach_gold_today(con):
    g = lineage.Graph(lineage.collect(con))
    assert lineage.pii_violations(g, ["staging.stg_contacts.contact_email",
                                      "staging.stg_contacts.contact_name",
                                      "staging.stg_contacts.contact_phone"]) == []
