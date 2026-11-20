# Databricks notebook source
# MAGIC %md
# MAGIC # TrialGraph build on Databricks
# MAGIC
# MAGIC **Untested on Databricks.** The SQL here is transpiled from the DuckDB models
# MAGIC and its translation is verified locally by a round-trip test; whether every
# MAGIC statement runs on your workspace is only known once this notebook has run.
# MAGIC
# MAGIC Plan: the Python stages (ingest, flattening, entity resolution, chunking) run
# MAGIC on the driver - 5k trials fit in memory with room to spare - and their tables are
# MAGIC published to Unity Catalog as Delta. The silver and gold SQL models then run in
# MAGIC Spark SQL, in the dependency order the runner derives, and a parity check
# MAGIC compares every Spark table against the driver-side build row for row.

# COMMAND ----------
import json, os, sys, tempfile
dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("raw_path", "/Volumes/main/trialgraph/raw/ctg-studies.json")
CATALOG = dbutils.widgets.get("catalog")
RAW = dbutils.widgets.get("raw_path")

repo = os.path.abspath(os.path.join(os.getcwd(), ".."))
sys.path.insert(0, repo)
from trialgraph import catalog as tg_catalog, databricks as tg_dbx, pipeline, runner
import duckdb

# COMMAND ----------
# 1. Python stages on the driver, into a local DuckDB file.
local = os.path.join(tempfile.mkdtemp(), "warehouse.duckdb")
report = pipeline.build(local, [RAW])
print(report["ingest"], report["quality"]["summary"])
con = duckdb.connect(local, read_only=True)

# COMMAND ----------
# 2. Publish bronze, staging and Python-model outputs to Unity Catalog.
spark.sql(f"USE CATALOG {CATALOG}")
for s in ("bronze", "staging", "silver", "gold", "meta"):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {s}")

python_outputs = [o for m in runner.discover() if m.fn is not None for o in m.outputs]
tables = [f"{s}.{t}" for s, t in con.execute(
    "SELECT table_schema, table_name FROM information_schema.tables "
    "WHERE table_schema IN ('bronze', 'staging')").fetchall()] + python_outputs
for t in tables:
    pdf = con.execute(f"SELECT * FROM {t}").df()
    spark.createDataFrame(pdf).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(t)
    print("published", t, len(pdf))

# COMMAND ----------
# 3. SQL models in Spark, in dependency order.
out = tempfile.mkdtemp()
tg_dbx.export(out, tg_catalog.build(con))
for step in json.load(open(os.path.join(out, "manifest.json"))):
    if step["kind"] == "sql":
        sql = open(os.path.join(out, step["file"])).read()
        spark.sql(f"CREATE OR REPLACE TABLE {step['name']} AS {sql}")
        print("built", step["name"])

# COMMAND ----------
# 4. Unity Catalog comments, tags and PII tags from the generated catalog.
for stmt in open(os.path.join(out, "uc_governance.sql")).read().split(";\n"):
    stmt = "\n".join(l for l in stmt.splitlines() if not l.startswith("--")).strip()
    if stmt:
        spark.sql(stmt)

# COMMAND ----------
# 5. Parity: every Spark-built model must equal the driver-side build.
bad = []
for step in json.load(open(os.path.join(out, "manifest.json"))):
    if step["kind"] != "sql":
        continue
    a = spark.table(step["name"])
    b = spark.createDataFrame(con.execute(f"SELECT * FROM {step['name']}").df())
    diff = a.exceptAll(b.select(a.columns)).count() + b.select(a.columns).exceptAll(a).count()
    print(step["name"], "rows", a.count(), "diff", diff)
    if diff:
        bad.append(step["name"])
assert not bad, f"Spark and DuckDB disagree on: {bad}"
