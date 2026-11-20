"""An independent reference implementation, for evaluation only.

This module answers the evaluation questions straight from the raw registry
JSON, in plain Python, with no DuckDB, no SQL, and no imports from the rest of
TrialGraph - a test asserts that. The pipeline and this module share nothing
but the specification, so when they agree it is evidence the pipeline is
right, not a tautology; when they disagree, one of them has a bug and the
evaluation says which question exposed it.

Where the question involves judgement - which names count as the same drug -
this module uses a short, hand-written synonym list per drug instead of the
pipeline's learned resolution. The comparison then measures the resolver's
precision and recall against a human answer, rather than checking it against
itself.
"""
from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

VALID_STATUS = {"ACTIVE_NOT_RECRUITING", "COMPLETED", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING",
                "RECRUITING", "SUSPENDED", "TERMINATED", "WITHDRAWN", "AVAILABLE", "NO_LONGER_AVAILABLE",
                "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING", "WITHHELD", "UNKNOWN"}

# Hand-curated synonym lists: the human answer the resolver is graded against.
DRUG_SYNONYMS = {
    "Sacubitril/Valsartan": ["sacubitril", "lcz696", "lcz 696", "entresto"],
    "Dapagliflozin": ["dapagliflozin", "farxiga", "forxiga", "bms-512148"],
    "Empagliflozin": ["empagliflozin", "jardiance"],
    "Spironolactone": ["spironolactone", "aldactone"],
    "Furosemide": ["furosemide", "frusemide", "lasix"],
}


def g(d, path):
    for k in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _read(path: Path) -> list[dict]:
    if path.suffix == ".zip":
        out = []
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                if n.endswith(".json"):
                    out += _unwrap(json.loads(z.read(n)))
        return out
    return _unwrap(json.loads(path.read_text()))


def _unwrap(obj):
    if isinstance(obj, dict) and "studies" in obj:
        return obj["studies"]
    return obj if isinstance(obj, list) else [obj]


def load(paths: list[str | Path]) -> dict[str, dict]:
    """Latest version of each trial, by registry update date; later files win ties."""
    latest: dict[str, tuple[str, int, dict]] = {}
    order = 0
    for p in paths:
        for s in _read(Path(p)):
            order += 1
            nct = g(s, "protocolSection.identificationModule.nctId")
            if not nct:
                continue
            upd = g(s, "protocolSection.statusModule.lastUpdatePostDateStruct.date") or ""
            if nct not in latest or (upd, order) >= latest[nct][:2]:
                latest[nct] = (upd, order, s)
    return {k: v[2] for k, v in latest.items()}


# ---------------------------------------------------------------- attributes
def status(s) -> str:
    v = g(s, "protocolSection.statusModule.overallStatus")
    return v if v in VALID_STATUS else "UNRECOGNISED"


def phase_label(s) -> str:
    ph = set(g(s, "protocolSection.designModule.phases") or [])
    if {"PHASE1", "PHASE2"} <= ph:
        return "PHASE1/PHASE2"
    if {"PHASE2", "PHASE3"} <= ph:
        return "PHASE2/PHASE3"
    for p in ("EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"):
        if p in ph:
            return p
    return "NOT_REPORTED"


def start_year(s):
    d = g(s, "protocolSection.statusModule.startDateStruct.date") or ""
    return int(d[:4]) if len(d) in (7, 10) and d[:4].isdigit() else None


def enrollment(s) -> tuple[int | None, int | None]:
    e = g(s, "protocolSection.designModule.enrollmentInfo") or {}
    c = e.get("count")
    if c is None:
        return None, None
    return (int(c), None) if e.get("type") == "ACTUAL" else ((None, int(c)) if e.get("type") == "ESTIMATED" else (None, None))


def lead_class(s):
    return g(s, "protocolSection.sponsorCollaboratorsModule.leadSponsor.class")


def lead_name(s) -> str:
    return g(s, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name") or ""


def mesh(s, direct_only=False) -> set[str]:
    m = {x["id"] for x in (g(s, "derivedSection.conditionBrowseModule.meshes") or []) if x.get("id")}
    if not direct_only:
        m |= {x["id"] for x in (g(s, "derivedSection.conditionBrowseModule.ancestors") or []) if x.get("id")}
    return m


def mesh_terms(trials: dict) -> dict[str, str]:
    out = {}
    for s in trials.values():
        for k in ("meshes", "ancestors"):
            for x in g(s, f"derivedSection.conditionBrowseModule.{k}") or []:
                out.setdefault(x["id"], x["term"])
    return out


def countries(s) -> set[str]:
    return {x.get("country") for x in (g(s, "protocolSection.contactsLocationsModule.locations") or [])
            if x.get("country")}


def mentions_drug(s, patterns: list[str]) -> bool:
    for i in g(s, "protocolSection.armsInterventionsModule.interventions") or []:
        names = [i.get("name") or ""] + list(i.get("otherNames") or [])
        for n in names:
            low = n.casefold()
            if "placebo" in low:
                continue
            if any(p in low for p in patterns):
                return True
    return False


# ----------------------------------------------------------------- questions
def count_by(trials, fn) -> dict:
    return dict(Counter(fn(s) for s in trials.values()))


def ratio(trials, num, den, group=None) -> dict:
    n, d = Counter(), Counter()
    for s in trials.values():
        k = group(s) if group else "all"
        if den(s):
            d[k] += 1
            if num(s):
                n[k] += 1
    return {k: (n[k] / d[k]) for k in d}


def enrollment_where(trials, pred) -> dict:
    a = p = n = 0
    for s in trials.values():
        if pred(s):
            n += 1
            act, plan = enrollment(s)
            a += act or 0
            p += plan or 0
    return {"trials": n, "actual_enrollment": a, "planned_enrollment": p}


def trials_where(trials, pred) -> set[str]:
    return {k for k, s in trials.items() if pred(s)}


def by_group(trials, fn) -> dict:
    out = defaultdict(set)
    for k, s in trials.items():
        for v in fn(s):
            out[v].add(k)
    return out
