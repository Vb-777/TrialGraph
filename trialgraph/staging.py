"""Staging: flatten the nested registry JSON into typed relational tables.

Every staging column is declared here as a JSON path, so the flattening is
data rather than code. Two things follow from that:

* Column-level lineage starts at the source. The catalog can say that
  `gold.fct_trial.enrollment_actual` ultimately comes from
  `protocolSection.designModule.enrollmentInfo.count`, because this spec says
  which path every staging column was read from.
* The same spec runs anywhere Python runs. Locally it feeds DuckDB; on
  Databricks the identical function feeds a Spark DataFrame. Parsing deeply
  nested JSON is done once, in tested Python, and everything downstream is
  plain SQL over flat tables - which is what keeps the SQL portable between
  engines whose JSON functions all differ.

Staging is deliberately unopinionated: strings stay strings, partial dates
stay partial, sponsor names stay as written. Cleaning happens in silver, in
SQL, where each rule is visible and testable.
"""
from __future__ import annotations

import json
from typing import Iterable

from .bulk import create_and_load

ROOT_KEY = ("nct_id", "protocolSection.identificationModule.nctId")

# Each table: list of sources (an array path, or None for one row per study),
# optional constant columns per source, and the columns read from each item.
# A column path of "@" means the array item itself (an array of scalars).
SPEC: dict[str, dict] = {
    "stg_studies": {
        "sources": [{"array": None}],
        "columns": {
            "brief_title": ("protocolSection.identificationModule.briefTitle", "VARCHAR"),
            "overall_status": ("protocolSection.statusModule.overallStatus", "VARCHAR"),
            "why_stopped": ("protocolSection.statusModule.whyStopped", "VARCHAR"),
            "start_date_raw": ("protocolSection.statusModule.startDateStruct.date", "VARCHAR"),
            "start_date_type": ("protocolSection.statusModule.startDateStruct.type", "VARCHAR"),
            "primary_completion_date_raw": ("protocolSection.statusModule.primaryCompletionDateStruct.date", "VARCHAR"),
            "primary_completion_date_type": ("protocolSection.statusModule.primaryCompletionDateStruct.type", "VARCHAR"),
            "first_posted_date_raw": ("protocolSection.statusModule.studyFirstPostDateStruct.date", "VARCHAR"),
            "last_update_posted_raw": ("protocolSection.statusModule.lastUpdatePostDateStruct.date", "VARCHAR"),
            "study_type": ("protocolSection.designModule.studyType", "VARCHAR"),
            "allocation": ("protocolSection.designModule.designInfo.allocation", "VARCHAR"),
            "primary_purpose": ("protocolSection.designModule.designInfo.primaryPurpose", "VARCHAR"),
            "enrollment_count": ("protocolSection.designModule.enrollmentInfo.count", "INTEGER"),
            "enrollment_type": ("protocolSection.designModule.enrollmentInfo.type", "VARCHAR"),
            "has_results": ("hasResults", "BOOLEAN"),
            "brief_summary": ("protocolSection.descriptionModule.briefSummary", "VARCHAR"),
            "eligibility_criteria": ("protocolSection.eligibilityModule.eligibilityCriteria", "VARCHAR"),
            "minimum_age": ("protocolSection.eligibilityModule.minimumAge", "VARCHAR"),
            "sex": ("protocolSection.eligibilityModule.sex", "VARCHAR"),
        },
    },
    "stg_phases": {
        "sources": [{"array": "protocolSection.designModule.phases"}],
        "columns": {"phase": ("@", "VARCHAR")},
    },
    "stg_conditions": {
        "sources": [{"array": "protocolSection.conditionsModule.conditions"}],
        "ordinal": True,
        "columns": {"condition_text": ("@", "VARCHAR")},
    },
    "stg_condition_mesh": {
        "sources": [
            {"array": "derivedSection.conditionBrowseModule.meshes", "const": {"relation": "direct"}},
            {"array": "derivedSection.conditionBrowseModule.ancestors", "const": {"relation": "ancestor"}},
        ],
        "columns": {"mesh_id": ("id", "VARCHAR"), "mesh_term": ("term", "VARCHAR"),
                    "relation": ("=const", "VARCHAR")},
    },
    "stg_interventions": {
        "sources": [{"array": "protocolSection.armsInterventionsModule.interventions"}],
        "ordinal": True,
        "columns": {"intervention_type": ("type", "VARCHAR"),
                    "intervention_name": ("name", "VARCHAR"),
                    "other_names": ("otherNames", "VARCHAR|join")},
    },
    "stg_intervention_mesh": {
        "sources": [
            {"array": "derivedSection.interventionBrowseModule.meshes", "const": {"relation": "direct"}},
            {"array": "derivedSection.interventionBrowseModule.ancestors", "const": {"relation": "ancestor"}},
        ],
        "columns": {"mesh_id": ("id", "VARCHAR"), "mesh_term": ("term", "VARCHAR"),
                    "relation": ("=const", "VARCHAR")},
    },
    "stg_sponsors": {
        "sources": [
            {"array": "protocolSection.sponsorCollaboratorsModule.leadSponsor", "single": True,
             "const": {"role": "lead"}},
            {"array": "protocolSection.sponsorCollaboratorsModule.collaborators",
             "const": {"role": "collaborator"}},
        ],
        "columns": {"sponsor_name_raw": ("name", "VARCHAR"), "sponsor_class": ("class", "VARCHAR"),
                    "role": ("=const", "VARCHAR")},
    },
    "stg_locations": {
        "sources": [{"array": "protocolSection.contactsLocationsModule.locations"}],
        "ordinal": True,
        "columns": {"facility": ("facility", "VARCHAR"), "city": ("city", "VARCHAR"),
                    "country": ("country", "VARCHAR")},
    },
    "stg_contacts": {
        "sources": [{"array": "protocolSection.contactsLocationsModule.centralContacts"}],
        "ordinal": True,
        "columns": {"contact_name": ("name", "VARCHAR"), "contact_role": ("role", "VARCHAR"),
                    "contact_email": ("email", "VARCHAR"), "contact_phone": ("phone", "VARCHAR")},
    },
}

BRONZE_COLUMNS = {"batch_id": "VARCHAR", "ingested_at": "TIMESTAMP"}


def _get(d, path: str):
    if path == "@":
        return d
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _cast(v, typ: str):
    if v is None:
        return None
    base, _, mod = typ.partition("|")
    if mod == "join":
        return "|".join(str(x) for x in v) if isinstance(v, list) and v else None
    if base == "INTEGER":
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if base == "BOOLEAN":
        return bool(v)
    if base == "VARCHAR":
        return v if isinstance(v, str) else json.dumps(v)
    return v


def columns(table: str) -> list[tuple[str, str]]:
    """Ordered (name, sql type) for a staging table."""
    spec = SPEC[table]
    cols = [("nct_id", "VARCHAR")]
    if spec.get("ordinal"):
        cols.append(("ordinal", "INTEGER"))
    cols += [(n, t.partition("|")[0]) for n, (_, t) in spec["columns"].items()]
    if table == "stg_studies":
        cols += list(BRONZE_COLUMNS.items())
    return cols


def flatten(study: dict, meta: dict | None = None) -> dict[str, list[tuple]]:
    """One study -> rows for every staging table."""
    nct = _get(study, ROOT_KEY[1])
    out: dict[str, list[tuple]] = {}
    for table, spec in SPEC.items():
        rows = []
        for src in spec["sources"]:
            if src["array"] is None:
                items = [study]
            else:
                v = _get(study, src["array"])
                items = [v] if src.get("single") and v else (v or [])
            for i, item in enumerate(items):
                row = [nct]
                if spec.get("ordinal"):
                    row.append(i)
                for name, (path, typ) in spec["columns"].items():
                    if path == "=const":
                        row.append(src["const"][name])
                    else:
                        row.append(_cast(_get(item, path), typ))
                if table == "stg_studies":
                    row += [(meta or {}).get("batch_id"), (meta or {}).get("ingested_at")]
                rows.append(tuple(row))
        out[table] = rows
    return out


def lineage_edges() -> list[dict]:
    """Source JSON path -> staging column, for the catalog."""
    edges = []
    for table, spec in SPEC.items():
        edges.append({"to": f"staging.{table}.nct_id", "from": f"source:{ROOT_KEY[1]}"})
        for name, (path, _) in spec["columns"].items():
            for src in spec["sources"]:
                if path == "=const":
                    # A constant column is decided by which source array the
                    # row came from - "direct" iff it was read from `meshes` -
                    # so its true source is that array, not the literal.
                    frm = "source:" + src["array"] + ("" if src.get("single") else "[*]")
                else:
                    base = "" if src["array"] is None else src["array"] + ("." if src.get("single") else "[*].")
                    frm = "source:" + (base.rstrip(".") if path == "@" else base + path)
                    if path == "@":
                        frm = "source:" + src["array"] + "[*]"
                edges.append({"to": f"staging.{table}.{name}", "from": frm})
    return edges


def build(con, studies: Iterable[tuple[dict, dict]]) -> dict[str, int]:
    """(study, bronze meta) pairs -> staging tables. Rebuilt in full each run."""
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    buffers: dict[str, list[tuple]] = {t: [] for t in SPEC}
    for study, meta in studies:
        for t, rows in flatten(study, meta).items():
            buffers[t].extend(rows)
    return {t: create_and_load(con, f"staging.{t}", columns(t), rows) for t, rows in buffers.items()}


def from_bronze(con) -> dict[str, int]:
    """Flatten the latest version of every trial in bronze."""
    rows = con.execute("SELECT payload, batch_id, ingested_at FROM bronze.v_latest").fetchall()
    return build(con, ((json.loads(p), {"batch_id": b, "ingested_at": t}) for p, b, t in rows))
