"""Shared fixtures. The default suite runs on the synthetic fixture only - it
needs no network and no download. Tests marked `real` run on the
ClinicalTrials.gov export when TRIALGRAPH_REAL points at it."""
import os
import shutil
from pathlib import Path

import duckdb
import pytest

from trialgraph import pipeline

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "data" / "fixtures"
BATCHES = [FIX / "synthetic_batch1.json", FIX / "synthetic_batch2.json"]


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("wh")
    db = d / "warehouse.duckdb"
    reports = [pipeline.build(db, [b], out_dir=d / "out") for b in BATCHES]
    return {"db": db, "dir": d, "reports": reports}


@pytest.fixture
def con(built):
    c = duckdb.connect(str(built["db"]), read_only=True)
    yield c
    c.close()


@pytest.fixture
def scratch(built, tmp_path):
    """A writable copy of the built warehouse, for fault injection."""
    p = tmp_path / "copy.duckdb"
    shutil.copy(built["db"], p)
    c = duckdb.connect(str(p))
    yield c
    c.close()


REAL = os.environ.get("TRIALGRAPH_REAL")


@pytest.fixture(scope="session")
def real(tmp_path_factory):
    if not REAL or not Path(REAL).exists():
        pytest.skip("set TRIALGRAPH_REAL to a ClinicalTrials.gov export to run real-data tests")
    db = tmp_path_factory.mktemp("real") / "warehouse.duckdb"
    pipeline.build(db, [REAL])
    c = duckdb.connect(str(db), read_only=True)
    yield {"con": c, "raw": [REAL]}
    c.close()
