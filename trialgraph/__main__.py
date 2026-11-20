"""Command line: python -m trialgraph <command> ...

  build    ingest files (and/or the CT.gov API), rebuild, check, catalog
  eval     score the tool layer against the independent reference
  ask      call one tool and print the JSON result
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trialgraph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("inputs", nargs="*", help=".json / .jsonl / .zip from ClinicalTrials.gov")
    b.add_argument("--db", default="warehouse.duckdb")
    b.add_argument("--api-condition", help="fetch interventional studies for this condition from the v2 API")
    b.add_argument("--out", default="out")

    e = sub.add_parser("eval")
    e.add_argument("raw", nargs="+", help="the same raw files the warehouse was built from")
    e.add_argument("--db", default="warehouse.duckdb")
    e.add_argument("--json", action="store_true")

    a = sub.add_parser("ask")
    a.add_argument("tool")
    a.add_argument("args", nargs="?", default="{}", help="JSON object of tool arguments")
    a.add_argument("--db", default="warehouse.duckdb")

    ns = ap.parse_args(argv)
    if ns.cmd == "build":
        from . import pipeline
        from .quality import QualityError
        try:
            r = pipeline.build(ns.db, ns.inputs, ns.api_condition, ns.out)
        except QualityError as err:
            print(f"quality gate failed: {err}", file=sys.stderr)
            return 2
        for i in r["ingest"]:
            print(f"ingest {i['source']}: {i['received']} received, {i['inserted']} new, "
                  f"{i['unchanged']} unchanged, {i['quarantined']} quarantined")
        s = r["quality"]["summary"]
        print(f"quality: {s}")
        print(f"catalog: {r['catalog_stats']}")
        print(f"wrote {ns.out}/catalog.md, {ns.out}/quality.md")
        return 0

    import duckdb
    con = duckdb.connect(ns.db, read_only=True)
    if ns.cmd == "eval":
        from . import evaluate
        r = evaluate.run(con, ns.raw)
        if ns.json:
            print(json.dumps(r, indent=2, default=str))
        else:
            for c in r["cases"]:
                print(("PASS " if c["pass"] else "FAIL ") + c["category"].ljust(11) + c["id"])
            print(r["summary"])
        return 0 if all(v["fail"] == 0 for v in r["summary"].values()) else 1
    if ns.cmd == "ask":
        from .tools import Tools
        print(json.dumps(Tools(con).call(ns.tool, json.loads(ns.args)), indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
