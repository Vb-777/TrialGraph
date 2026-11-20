#!/usr/bin/env python3
"""Generate the SYNTHETIC test fixture.

Nothing in this file is real clinical-trial data. It produces records in the
exact shape of the ClinicalTrials.gov API v2 `studies` endpoint, so the whole
pipeline can be developed and tested without network access, and it plants
the specific kinds of mess real registry data contains so the tests can prove
each one is handled:

* partial dates ("2019-03" as well as "2019-03-15")
* one sponsor written five different ways
* one drug known by its code name, its generic name and its brand name
* estimated and actual enrollment that must never be summed together
* a later batch that revises earlier records (status and enrollment change)
* records with no NCT ID, which must be quarantined rather than dropped
* an invalid status value, which a quality check must catch
* personal contact details, which must never reach the gold layer

MeSH descriptor IDs here were hand-assigned for testing. In real data the
registry supplies them, and the pipeline takes them from the source.

    python3 scripts/make_fixture.py            # writes data/fixtures/
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "fixtures"
SEED = 20261120

# condition text variants -> (mesh id, mesh term, ancestors [(id, term)])
HF_ANC = [("D006331", "Heart Diseases"), ("D002318", "Cardiovascular Diseases")]
CONDITIONS = [
    (["Heart Failure", "heart failure", "Chronic Heart Failure", "HF"],
     ("D006333", "Heart Failure"), HF_ANC, 30),
    (["Heart Failure With Reduced Ejection Fraction", "HFrEF"],
     ("D054143", "Heart Failure, Systolic"), [("D006333", "Heart Failure")] + HF_ANC, 18),
    (["Heart Failure With Preserved Ejection Fraction", "HFpEF"],
     ("D054144", "Heart Failure, Diastolic"), [("D006333", "Heart Failure")] + HF_ANC, 12),
    (["Dilated Cardiomyopathy"],
     ("D002311", "Cardiomyopathy, Dilated"), [("D009202", "Cardiomyopathies")] + HF_ANC, 5),
    (["Atrial Fibrillation"],
     ("D001281", "Atrial Fibrillation"), [("D001145", "Arrhythmias, Cardiac")] + HF_ANC, 8),
    (["Hypertension"],
     ("D006973", "Hypertension"), [("D014652", "Vascular Diseases"), ("D002318", "Cardiovascular Diseases")], 6),
    (["Type 2 Diabetes", "Type 2 Diabetes Mellitus"],
     ("D003924", "Diabetes Mellitus, Type 2"), [("D003920", "Diabetes Mellitus"), ("D004700", "Endocrine System Diseases")], 7),
    (["Chronic Kidney Disease", "CKD"],
     ("D051436", "Renal Insufficiency, Chronic"), [("D051437", "Renal Insufficiency"), ("D007674", "Kidney Diseases")], 6),
]

SPONSORS = [
    # (raw name variants, class, weight)
    (["Novartis Pharmaceuticals", "Novartis", "NOVARTIS PHARMA AG",
      "Novartis Pharmaceuticals Corporation", "Novartis Pharma AG"], "INDUSTRY", 14),
    (["AstraZeneca", "AstraZeneca AB"], "INDUSTRY", 9),
    (["Bayer"], "INDUSTRY", 6),
    (["Boehringer Ingelheim"], "INDUSTRY", 6),
    (["Medtronic", "Medtronic, Inc."], "INDUSTRY", 4),
    (["National Heart, Lung, and Blood Institute (NHLBI)"], "NIH", 5),
    (["Duke University"], "OTHER", 6),
    (["Mayo Clinic"], "OTHER", 5),
    (["University of Oxford"], "OTHER", 4),
]

DRUGS = [
    # (name, otherNames, mesh or None, weight)
    ("LCZ696", ["sacubitril/valsartan", "Entresto"], None, 10),
    ("Sacubitril/Valsartan", ["LCZ696"], None, 6),
    ("Entresto", [], None, 2),
    ("Dapagliflozin", ["Farxiga"], None, 7),
    ("Empagliflozin", ["Jardiance"], None, 6),
    ("Spironolactone", [], ("D013148", "Spironolactone"), 5),
    ("Furosemide", [], ("D005665", "Furosemide"), 4),
    ("Metoprolol", [], ("D008790", "Metoprolol"), 4),
    ("Enalapril", [], ("D004656", "Enalapril"), 5),
]
NON_DRUG = [
    ("DEVICE", "CardioMEMS HF System"),
    ("PROCEDURE", "Cardiac Resynchronization Therapy"),
    ("BEHAVIORAL", "Supervised Exercise Training"),
    ("DEVICE", "Wearable Cardioverter Defibrillator"),
]
COUNTRIES = ["United States", "Germany", "India", "Japan", "United Kingdom",
             "China", "Spain", "Canada", "Brazil", "Italy", "France", "Poland"]
STATUSES = [("COMPLETED", 34), ("RECRUITING", 18), ("ACTIVE_NOT_RECRUITING", 10),
            ("TERMINATED", 9), ("NOT_YET_RECRUITING", 6), ("WITHDRAWN", 4),
            ("UNKNOWN", 6), ("SUSPENDED", 1)]
CLOSED = {"COMPLETED", "TERMINATED", "WITHDRAWN"}


def pick(rng: random.Random, items, wi=-1):
    return rng.choices(items, weights=[it[wi] for it in items], k=1)[0]


def date(rng: random.Random, year: int) -> str:
    m = rng.randint(1, 12)
    return f"{year}-{m:02d}" if rng.random() < 0.4 else f"{year}-{m:02d}-{rng.randint(1, 28):02d}"


def study(rng: random.Random, n: int, update_year: int) -> dict:
    nct = f"NCT{90000000 + n:08d}"
    status = pick(rng, STATUSES)[0]
    start_y = rng.randint(2008, 2025)

    conds = [pick(rng, CONDITIONS)]
    if rng.random() < 0.35:
        conds.append(pick(rng, CONDITIONS))
    seen, cond_text, meshes, ancestors = set(), [], [], {}
    for variants, mesh, anc, _ in conds:
        if mesh[0] in seen:
            continue
        seen.add(mesh[0])
        cond_text.append(rng.choice(variants))
        meshes.append({"id": mesh[0], "term": mesh[1]})
        for a in anc:
            ancestors[a[0]] = a[1]

    is_drug = rng.random() < 0.78
    interventions, int_meshes = [], []
    if is_drug:
        d = pick(rng, DRUGS)
        interventions.append({"type": "DRUG", "name": d[0], "otherNames": d[1],
                              "armGroupLabels": ["Experimental"]})
        if d[2]:
            int_meshes.append({"id": d[2][0], "term": d[2][1]})
        if rng.random() < 0.55:
            interventions.append({"type": "DRUG", "name": "Placebo", "armGroupLabels": ["Placebo"]})
        phases = rng.choice([["PHASE2"], ["PHASE3"], ["PHASE3"], ["PHASE2", "PHASE3"],
                             ["PHASE4"], ["PHASE1"], ["PHASE4"]])
    else:
        t, name = rng.choice(NON_DRUG)
        interventions.append({"type": t, "name": name, "armGroupLabels": ["Intervention"]})
        phases = ["NA"]

    lead_variants, lead_class, _ = pick(rng, SPONSORS)
    if not is_drug and lead_class == "INDUSTRY" and "Medtronic" not in lead_variants[0]:
        lead_variants, lead_class = ["Medtronic", "Medtronic, Inc."], "INDUSTRY"
    lead = {"name": rng.choice(lead_variants), "class": lead_class}
    collaborators = []
    if rng.random() < 0.3:
        c_vars, c_cls, _ = pick(rng, SPONSORS)
        if c_vars[0] != lead_variants[0]:
            collaborators.append({"name": rng.choice(c_vars), "class": c_cls})

    closed = status in CLOSED
    if status == "WITHDRAWN":
        enroll = {"count": 0, "type": "ACTUAL"}
    elif rng.random() < 0.04:
        enroll = {"type": "ESTIMATED"}            # count missing: real records do this
    else:
        base = rng.randint(40, 600) if "PHASE3" not in phases else rng.randint(300, 9000)
        enroll = {"count": base, "type": "ACTUAL" if closed else "ESTIMATED"}

    status_mod = {
        "overallStatus": status,
        "startDateStruct": {"date": date(rng, start_y), "type": "ACTUAL" if start_y < 2025 else "ESTIMATED"},
        "lastUpdatePostDateStruct": {"date": f"{update_year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                                     "type": "ACTUAL"},
        "studyFirstPostDateStruct": {"date": f"{start_y}-{rng.randint(1, 12):02d}-01", "type": "ACTUAL"},
    }
    if closed or rng.random() < 0.5:
        status_mod["primaryCompletionDateStruct"] = {"date": date(rng, min(start_y + rng.randint(1, 5), 2030)),
                                                     "type": "ACTUAL" if closed else "ESTIMATED"}
    if status == "TERMINATED":
        status_mod["whyStopped"] = rng.choice(["Slow enrollment", "Sponsor decision",
                                               "Futility at interim analysis", "Funding"])

    locs = [{"facility": f"Site {rng.randint(1, 999)}", "city": "City",
             "country": c} for c in rng.sample(COUNTRIES, k=rng.randint(1, 5))]

    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct,
                "briefTitle": f"Study of {interventions[0]['name']} in {cond_text[0]}",
                "organization": {"fullName": lead["name"], "class": lead_class},
            },
            "statusModule": status_mod,
            "sponsorCollaboratorsModule": {"leadSponsor": lead, "collaborators": collaborators},
            "descriptionModule": {
                "briefSummary": (f"This is a synthetic record. The study evaluates {interventions[0]['name']} "
                                 f"in adults with {cond_text[0].lower()}. The primary outcome is time to first "
                                 f"hospitalisation for heart failure or cardiovascular death."),
            },
            "conditionsModule": {"conditions": cond_text},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": phases,
                             "designInfo": {"allocation": rng.choice(["RANDOMIZED", "NA"]),
                                            "primaryPurpose": "TREATMENT"},
                             "enrollmentInfo": enroll},
            "armsInterventionsModule": {"interventions": interventions},
            "eligibilityModule": {"eligibilityCriteria": ("Inclusion Criteria:\n* Age 18 years or older\n"
                                                          "* LVEF documented within 6 months\n"
                                                          "Exclusion Criteria:\n* Pregnancy"),
                                  "sex": "ALL", "minimumAge": "18 Years"},
            "contactsLocationsModule": {
                "centralContacts": [{"name": f"Contact {n:04d}", "role": "CONTACT",
                                     "email": f"contact{n:04d}@example.org", "phone": "555-0100"}],
                "locations": locs,
            },
        },
        "derivedSection": {
            "conditionBrowseModule": {"meshes": meshes,
                                      "ancestors": [{"id": k, "term": v} for k, v in ancestors.items()]},
            "interventionBrowseModule": {"meshes": int_meshes, "ancestors": []},
        },
        "hasResults": closed and rng.random() < 0.4,
    }


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    batch1 = [study(rng, n, 2025) for n in range(1, 401)]
    # Planted faults in batch 1.
    batch1[17]["protocolSection"]["statusModule"]["overallStatus"] = "COMPLETE"   # typo
    for i in (40, 41, 42):                                                        # no NCT ID
        del batch1[i]["protocolSection"]["identificationModule"]["nctId"]

    # Batch 2: 30 revisions of batch-1 records plus 20 new studies.
    batch2 = []
    for i in rng.sample(range(100, 400), 30):
        s = json.loads(json.dumps(batch1[i]))
        sm = s["protocolSection"]["statusModule"]
        sm["overallStatus"] = "COMPLETED"
        sm["lastUpdatePostDateStruct"] = {"date": "2026-10-01", "type": "ACTUAL"}
        sm["primaryCompletionDateStruct"] = {"date": "2026-06", "type": "ACTUAL"}
        ei = s["protocolSection"]["designModule"]["enrollmentInfo"]
        if "count" in ei and ei["count"]:
            ei["count"] = max(1, int(ei["count"] * 0.9))
        ei["type"] = "ACTUAL"
        batch2.append(s)
    batch2 += [study(rng, n, 2026) for n in range(401, 421)]

    # Batch 1 as the API's paginated shape, batch 2 as a bare array (the
    # website's download shape), so the loader is tested on both.
    (OUT / "synthetic_batch1.json").write_text(json.dumps({"studies": batch1, "nextPageToken": None}))
    (OUT / "synthetic_batch2.json").write_text(json.dumps(batch2))
    print(f"wrote {len(batch1)} + {len(batch2)} synthetic records to {OUT}")


if __name__ == "__main__":
    main()
