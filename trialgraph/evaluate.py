"""Evaluate the agent-facing tool layer against the independent reference.

Five things are measured, because a grounding layer can fail in five ways:

1. agreement  - governed metrics equal the reference, exactly
2. resolution - drug entity resolution vs a hand-written synonym list:
                precision and recall, reported, not assumed
3. grounding  - every trial a tool cites satisfies the question's filters,
                checked against the raw JSON, not against the warehouse
4. abstention - unknown drugs, sponsors and IDs return not_found or error,
                never a plausible-looking wrong answer
5. retrieval  - known-item search: given a trial's title, does its own
                summary come back first?
"""
from __future__ import annotations

import random

from . import reference as R
from . import semantic
from .tools import Tools

HF = "D006333"


def _close(a, b, tol=1e-9) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def _case(cid, cat, expected, got, ok, note=None) -> dict:
    return {"id": cid, "category": cat, "expected": expected, "got": got, "pass": bool(ok), "note": note}


def run(con, raw_paths: list[str], retrieval_n: int = 100, seed: int = 7) -> dict:
    T = Tools(con)
    ref = R.load(raw_paths)
    terms = R.mesh_terms(ref)
    cases: list[dict] = []

    def metric(m, by=None, where=None):
        r = T.call("metric", {"metric": m, "by": by, "where": where})
        if r["status"] != "ok":
            raise AssertionError(r)
        return r["rows"]

    # ------------------------------------------------------- 1. agreement
    exp = len(ref)
    got = metric("trials")[0]["trials"]
    cases.append(_case("total_trials", "agreement", exp, got, exp == got))

    for dim, fn in (("status", R.status), ("phase", R.phase_label), ("start_year", R.start_year)):
        exp = {k: v for k, v in R.count_by(ref, fn).items()}
        got = {r[dim]: r["trials"] for r in metric("trials", [dim])}
        cases.append(_case(f"trials_by_{dim}", "agreement", exp, got, exp == got))

    exp = R.ratio(ref, lambda s: R.status(s) == "COMPLETED" and bool(s.get("hasResults")),
                  lambda s: R.status(s) == "COMPLETED")["all"]
    got = metric("results_reporting_rate")[0]["results_reporting_rate"]
    cases.append(_case("results_reporting_rate", "agreement", exp, got, _close(exp, got)))

    exp = R.ratio(ref, lambda s: R.status(s) == "TERMINATED",
                  lambda s: R.status(s) in ("COMPLETED", "TERMINATED"))["all"]
    got = metric("termination_rate")[0]["termination_rate"]
    cases.append(_case("termination_rate", "agreement", exp, got, _close(exp, got)))

    # MeSH area, including descendants
    exp = R.enrollment_where(ref, lambda s: HF in R.mesh(s))
    row = metric(["trials", "actual_enrollment", "planned_enrollment"], where={"condition": terms[HF]})[0]
    got = {k: row[k] for k in exp}
    cases.append(_case("enrollment_hf_area", "agreement", exp, got, exp == got))

    # The fan-out case: three overlapping descriptors, direct mapping only
    ids = [i for i in (HF, "D054143", "D054144") if i in terms]
    exp = R.enrollment_where(ref, lambda s: bool(R.mesh(s, direct_only=True) & set(ids)))
    where = {"condition_direct": [terms[i] for i in ids]}
    row = metric(["trials", "actual_enrollment", "planned_enrollment"], where=where)[0]
    got = {k: row[k] for k in exp}
    naive = {m: con.execute(semantic.naive_sql(m, where)).fetchone()[0]
             for m in ("trials", "actual_enrollment", "planned_enrollment")}
    cases.append(_case("enrollment_hf_direct_fanout", "agreement", exp, got, exp == got,
                       note={"naive_join_result": naive}))

    top = R.by_group(ref, R.countries)
    for country in sorted(top, key=lambda c: -len(top[c]))[:5]:
        exp = len(top[country])
        got = metric("trials", where={"country": country})[0]["trials"]
        cases.append(_case(f"trials_in_{country.replace(' ', '_')}", "agreement", exp, got, exp == got))

    exp = len(R.trials_where(ref, lambda s: "novartis" in R.lead_name(s).casefold()))
    got = metric("trials", where={"sponsor_group": "Novartis"})[0]["trials"]
    cases.append(_case("novartis_group_lead_trials", "agreement", exp, got, exp == got,
                       note="reference: lead sponsor text mentions Novartis, which also catches "
                            "'... a member of the Novartis group of companies'"))

    # ------------------------------------------------------- 2. resolution
    for drug, pats in R.DRUG_SYNONYMS.items():
        human = R.trials_where(ref, lambda s, p=pats: R.mentions_drug(s, p))
        r = T.call("resolve_intervention", {"name": drug})
        if r["status"] != "ok":
            cases.append(_case(f"resolve_{drug}", "resolution", len(human), 0, False, "not resolved"))
            continue
        got = {x[0] for x in con.execute(
            "SELECT DISTINCT nct_id FROM gold.brg_trial_intervention WHERE intervention_key = ?",
            [r["intervention_key"]]).fetchall()}
        tp = len(got & human)
        prec = tp / len(got) if got else 0.0
        rec = tp / len(human) if human else 0.0
        missed = sorted(human - got)[:5]
        extra = sorted(got - human)[:5]
        cases.append(_case(f"resolve_{drug}", "resolution", len(human), len(got), prec >= 0.95,
                           note={"precision": round(prec, 4), "recall": round(rec, 4),
                                 "missed_examples": missed, "extra_examples": extra}))

    # ------------------------------------------------------- 3. grounding
    probes = [
        ({"status": "RECRUITING", "phase": "PHASE3"},
         lambda s: R.status(s) == "RECRUITING" and R.phase_label(s) == "PHASE3"),
        ({"condition": terms[HF], "status": "TERMINATED"},
         lambda s: HF in R.mesh(s) and R.status(s) == "TERMINATED"),
        ({"intervention": "Sacubitril/Valsartan"},
         lambda s: R.mentions_drug(s, R.DRUG_SYNONYMS["Sacubitril/Valsartan"])),
        ({"country": "India", "phase": "PHASE4"},
         lambda s: "India" in R.countries(s) and R.phase_label(s) == "PHASE4"),
        ({"sponsor_class": "INDUSTRY", "status_group": "stopped"},
         lambda s: R.lead_class(s) == "INDUSTRY" and R.status(s) in ("TERMINATED", "WITHDRAWN", "SUSPENDED")),
        ({"start_year": 2020, "has_results": True},
         lambda s: R.start_year(s) == 2020 and bool(s.get("hasResults"))),
    ]
    for where, pred in probes:
        r = T.call("find_trials", {"where": where, "limit": 50})
        cited = r["citations"]
        bad = [c for c in cited if c not in ref or not pred(ref[c])]
        cases.append(_case(f"grounded:{where}", "grounding", 0, len(bad), not bad and r["total"] > 0,
                           note={"cited": len(cited), "total": r["total"], "violations": bad[:5]}))

    # ------------------------------------------------------- 4. abstention
    for name, args, want in [
        ("resolve_intervention", {"name": "Notarealumab"}, "not_found"),
        ("resolve_intervention", {"name": "sacubitri"}, "not_found"),
        ("resolve_sponsor", {"name": "Acme Imaginary Pharma"}, "not_found"),
        ("get_trial", {"nct_id": "NCT99999999"}, "not_found"),
        ("get_trial", {"nct_id": "heart failure"}, "error"),
        ("metric", {"metric": "trials", "where": {"no_such_dimension": 1}}, "error"),
        ("metric", {"metric": "made_up_metric"}, "error"),
    ]:
        got = T.call(name, args)["status"]
        cases.append(_case(f"abstain:{name}:{list(args.values())[0]}", "abstention", want, got, got == want))

    # ------------------------------------------------------- 5. retrieval
    rng = random.Random(seed)
    pool = sorted(k for k, s in ref.items() if g_summary(s) and g_title(s))
    sample = rng.sample(pool, min(retrieval_n, len(pool)))
    ranks = []
    for nct in sample:
        r = T.call("search_text", {"query": g_title(ref[nct]), "k": 10})
        order = []
        for h in r.get("hits", []):
            if h["nct_id"] not in order:
                order.append(h["nct_id"])
        ranks.append(order.index(nct) + 1 if nct in order else None)
    hit1 = sum(1 for x in ranks if x == 1) / len(ranks)
    hit5 = sum(1 for x in ranks if x and x <= 5) / len(ranks)
    mrr = sum(1 / x for x in ranks if x) / len(ranks)
    cases.append(_case("known_item_retrieval", "retrieval", None,
                       {"hit@1": round(hit1, 3), "hit@5": round(hit5, 3), "mrr": round(mrr, 3), "n": len(ranks)},
                       hit5 >= 0.5))

    summary = {}
    for c in cases:
        s = summary.setdefault(c["category"], {"pass": 0, "fail": 0})
        s["pass" if c["pass"] else "fail"] += 1
    return {"summary": summary, "cases": cases}


def g_title(s):
    return R.g(s, "protocolSection.identificationModule.briefTitle")


def g_summary(s):
    return R.g(s, "protocolSection.descriptionModule.briefSummary")
