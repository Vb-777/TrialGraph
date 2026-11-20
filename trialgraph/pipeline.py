"""End-to-end build: bronze -> staging -> silver/gold models -> quality -> catalog.

Every stage is re-runnable. Ingest is idempotent (a record already stored with
the same content hash is skipped), and every downstream table is rebuilt from
bronze, so running the pipeline twice on the same input gives the same
warehouse.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb

from . import bronze, catalog, quality, runner, staging


def build(db: str | Path, inputs: list[str | Path] = (), api_condition: str | None = None,
          out_dir: str | Path | None = None, raise_on_error: bool = True) -> dict:
    con = duckdb.connect(str(db))
    report: dict = {"ingest": [], "timings_s": {}}
    try:
        t = time.perf_counter()
        bronze.init(con)
        for p in inputs:
            report["ingest"].append({"source": str(p), **bronze.ingest(con, bronze.load_file(p), source=str(p))})
        if api_condition:
            report["ingest"].append({"source": f"api:{api_condition}",
                                     **bronze.ingest(con, bronze.fetch_api(api_condition), source="api")})
        report["timings_s"]["ingest"] = time.perf_counter() - t

        t = time.perf_counter()
        report["staging"] = staging.from_bronze(con)
        report["timings_s"]["staging"] = time.perf_counter() - t

        t = time.perf_counter()
        report["models"] = runner.run(con)
        report["timings_s"]["models"] = time.perf_counter() - t

        t = time.perf_counter()
        report["quality"] = quality.run(con, raise_on_error=raise_on_error)
        report["timings_s"]["quality"] = time.perf_counter() - t

        cat = catalog.build(con)
        report["catalog_stats"] = cat["stats"]
        if out_dir:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            catalog.write(cat, out)
            (out / "quality.md").write_text(quality.to_markdown(report["quality"]))
    finally:
        con.close()
    return report
