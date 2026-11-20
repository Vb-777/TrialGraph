"""Runs only with TRIALGRAPH_REAL=/path/to/ctg-studies.json."""
from trialgraph import evaluate


def test_real_evaluation(real):
    r = evaluate.run(real["con"], real["raw"])
    assert all(v["fail"] == 0 for v in r["summary"].values()), [c for c in r["cases"] if not c["pass"]]
