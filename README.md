# TrialGraph

**An AI-ready data foundation for clinical-trials data.** TrialGraph takes the raw
ClinicalTrials.gov registry export and turns it into something an analyst, a
dashboard and an AI agent can all trust to give the *same, correct* answer:
a medallion warehouse with entity resolution, quality gates, column-level
lineage, a governed semantic layer, an OWL/SHACL ontology, and a tool contract
for LLM agents that every answer must cite.

Every number below comes from `bench/report.py` run on a real export: **5,317
interventional heart-failure trials** (registry snapshot updated to 2026-11-19).
Nothing in this README is typed by hand.

```
raw JSON ──► bronze ──► staging ──► silver ───────────────► gold ──► semantic layer ──► tools ──► agent
 (CT.gov)   append-only  declarative  typed, conformed,       star schema,       governed      cite-or-abstain
            hash-dedup   flattening   entity-resolved         bridges, RAG       metrics       contract
            quarantine   (+ PII)      (sponsors, drugs)       chunks             (fan-out-safe)
                 │                                                │
                 └── quality gates · column lineage · catalog ────┴──► OWL/SHACL ontology (MeSH IRIs)
                                                                  └──► Databricks / Unity Catalog export
```

## What it shows, on real data

| Problem in the raw data | What TrialGraph does | Measured |
|---|---|---|
| The same company appears under many spellings, and parent companies are buried in free text | Sponsor keys + parsing of registry-stated corporate relationships | 3,542 raw sponsor names → 3,524 entities; 6 parent links recovered (Corthera→Novartis, Wyeth/Arena→Pfizer, NovaCardia/Acceleron→MSD, Arrow→Teleflex) |
| One drug has a code, a generic and a brand name; and some records are simply wrong | Union-find over `otherNames` with **corroboration tiers**: a link asserted by one trial cannot fuse two established drugs | 6,423 raw intervention names → 5,476 entities; 661 links asserted, 100 corroborated, 414 attached as aliases, **144 rejected** (e.g. one record listing *Norvasc* as another name for eplerenone) |
| "How many trials use Entresto?" | Exact-match alias lookup, never fuzzy | Entresto / LCZ696 → Sacubitril/Valsartan, 83 trials; Farxiga → Dapagliflozin, 74; Jardiance → Empagliflozin, 66; *Notarealumab* → `not_found` |
| A trial maps to several conditions, so joining and summing double-counts | Semantic layer compiles fan-out filters to `EXISTS` and group-bys to distinct pairs | For Heart Failure + Systolic + Diastolic, the obvious join overstates planned enrollment by **5.92%** (622,513 vs 587,705) and actual by 0.5% - while the trial count looks right |
| Contact names, emails and phones are in the raw records | PII stops at staging; a lineage check fails the build if any PII column reaches gold | 1,427 trials list central contacts; 0 PII paths to gold |
| MeSH coding is incomplete | Both MeSH and free-text condition bridges in gold | 819 trials say "heart failure" in text but lack the MeSH descriptor - 509 of them have no MeSH mapping at all |
| Partial dates ("2019-03") | Typed date + explicit precision column | 42.4% of start dates are month-only; 30 missing |
| Outliers dominate sums | Median metric and a population-scale warning | One registry study (NCT06571747) is 74.1% of all actual enrollment; median actual enrollment is 55 |

## Evaluation: checked against an independent implementation

`trialgraph/reference.py` answers the evaluation questions straight from the
raw JSON in plain Python - no DuckDB, no SQL, no imports from the pipeline (a
test enforces that). Agreement is evidence, not a tautology.

| Category | Result on the real export |
|---|---|
| **Agreement** - counts by status, phase, start year, country; termination and results-reporting rates; MeSH-area enrollment incl. the fan-out case; Novartis group | 14 / 14 exact |
| **Grounding** - every NCT ID cited by `find_trials` re-checked against the raw JSON for six multi-filter questions | 6 / 6, zero violations |
| **Abstention** - unknown drug, misspelt drug, unknown sponsor, unknown NCT ID, malformed ID, bad dimension, bad metric | 7 / 7 |
| **Drug resolution** vs a hand-written synonym list | precision **1.00** for all 5 drugs; recall 0.55-0.88 |
| **Known-item retrieval** (BM25, 100 trials, query = title) | hit@1 0.56, hit@5 0.72, MRR 0.63 |

Recall is lower than precision **by design**: the resolver will not fold a
combination arm (*"Liraglutide 1.8 MG + Empagliflozin 25 MG"*) or a free-text
regimen (*"nesiritide, furosemide"*) into a single-drug entity, because a
wrong merge corrupts every downstream count silently while a missed merge
only under-counts visibly. Splitting combinations into component links is
the next step (see Limits).

## Quality gates

33 checks declared in `config/checks.yml`; on the real export **31 pass, 2
warn, 0 fail**. Errors stop the build; warnings are reported. Every check is
proven to fire by fault-injection tests: a duplicated trial, an orphan bridge
row, rows lost between layers, an undocumented gold column, and a gold model
that reads a contact email. Writing those tests found two real gaps - three
bridge tables had no referential check, and lineage crashed on a column the
model did not define - both fixed.

## Governance

* **Catalog**: 33 tables, 194 columns; all 66 gold columns documented; 65
  traced to a source field by column-level lineage (sqlglot), the one
  exception being a deliberate row-existence flag.
* **Lineage**: `gold.fct_trial.enrollment_actual` traces back to
  `protocolSection.designModule.enrollmentInfo.count` in the raw JSON.
* **Ontology**: 119,973 RDF triples using real MeSH IRIs, SHACL-validated
  (conforms); SPARQL and SQL return the same trial sets (Heart Failure direct
  3,769 = 3,769; including sub-concepts 3,968 = 3,968).

## Databricks

`trialgraph/databricks.py` transpiles the 17 SQL models to Databricks SQL with
sqlglot and generates Unity Catalog `COMMENT`/`TAGS` DDL from the catalog,
including `pii` tags. The translation is verified locally by round-tripping
DuckDB → Databricks → DuckDB and executing both against the warehouse:
**17 / 17 models return identical rows**. `databricks.yml` + `databricks/build_notebook.py`
form an Asset Bundle that builds into Unity Catalog and ends with a row-for-row
parity check against the driver-side build.

> **Not yet run on Databricks.** The round-trip proves the translation keeps the
> meaning; only a run on a workspace proves Databricks accepts every statement.

## For agents

`trialgraph/tools.py` exposes seven tools in JSON-schema form (`describe`,
`metric`, `find_trials`, `get_trial`, `resolve_intervention`, `resolve_sponsor`,
`search_text`). The agent cannot write SQL - it can only request governed
metrics - and every result carries citations and provenance (the exact SQL or
chunk IDs). `trialgraph/agent.py` is a minimal tool-use loop that flags any NCT
ID in the final answer that no tool returned. It is tested with a scripted
client and uses an injected model client, keeping the tool-use loop provider-neutral.

## Run it

```bash
pip install -e .[dev]
# On clinicaltrials.gov: search condition "Heart Failure", study type Interventional,
# then Download -> JSON. Or fetch with --api-condition "heart failure" (pip install .[api])
python -m trialgraph build ctg-studies.json --db warehouse.duckdb --out out
python -m trialgraph eval ctg-studies.json --db warehouse.duckdb
python -m trialgraph ask resolve_intervention '{"name": "Entresto"}' --db warehouse.duckdb
python -m trialgraph ask metric '{"metric": "termination_rate", "by": ["sponsor_class"]}' --db warehouse.duckdb
python bench/report.py ctg-studies.json          # regenerates every number above
pytest                                            # 42 tests on the synthetic fixture
TRIALGRAPH_REAL=ctg-studies.json pytest tests/test_real_data.py
```

A full build of the real export took 8.1 seconds in the development environment.

## Data

* **Real**: public ClinicalTrials.gov registry records (US NLM), interventional
  studies matching "heart failure". Not redistributed here; download as above.
* **Synthetic**: `data/fixtures/` is generated by `scripts/make_fixture.py`
  (seeded). It is fake data for tests only - including a deliberate status
  typo, three records with no NCT ID, and a second batch that revises 30
  trials - and no result in this README comes from it.

## Limits

* Combination arms and free-text regimens are not split into component drugs,
  which is why drug recall is 0.55-0.88.
* Business-unit sponsor names (e.g. "Medtronic Cardiac Rhythm and Heart
  Failure") are not rolled up to their parent unless the registry states the
  relationship.
* Retrieval is BM25 only; no embedding model is used.
* The Databricks bundle has not been run on a workspace.

## Layout

```
trialgraph/   bronze.py staging.py resolve.py chunks.py runner.py pipeline.py
              quality.py lineage.py catalog.py semantic.py ontology.py
              retrieval.py tools.py agent.py evaluate.py reference.py databricks.py
              models/{silver,gold}/*.sql   config/*.yml, *.ttl
databricks/   build_notebook.py            databricks.yml (bundle, repo root)
bench/        report.py, results/          tests/  scripts/  data/fixtures/
```

MIT licence.
