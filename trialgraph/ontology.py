"""Materialise the gold layer as an RDF knowledge graph and validate it.

The warehouse and the graph answer the same questions, so they must agree.
Two independent checks hold them to that:

* SHACL shapes (`config/shapes.ttl`) validate the graph's structure - one NCT
  ID per trial in the right format, one status, conditions that are real MeSH
  IRIs, never both actual and planned enrollment.
* A SPARQL count of trials in a MeSH area must equal the SQL count from
  `gold.dim_condition` for the same descriptor. Two query languages, two
  engines, one number.

What the graph adds over the tables is the hierarchy as a first-class
relation: `tg:studiesCondition` is a subproperty of `tg:withinConditionArea`,
so "trials in the heart-failure area" is one property path, and the
difference between that, direct mapping and free-text matching is measurable.
"""
from __future__ import annotations

from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SKOS

CONFIG = Path(__file__).resolve().parent / "config"
TG = Namespace("https://github.com/Vb-777/TrialGraph/ontology#")
ID = Namespace("https://github.com/Vb-777/TrialGraph/id/")
MESH = Namespace("http://id.nlm.nih.gov/mesh/")


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "x"


def materialise(con, limit: int | None = None) -> Graph:
    g = Graph()
    g.bind("tg", TG), g.bind("mesh", MESH), g.bind("skos", SKOS), g.bind("id", ID)
    g.parse(CONFIG / "ontology.ttl")

    lim = f" ORDER BY nct_id LIMIT {int(limit)}" if limit else ""
    trials = con.execute("SELECT nct_id, brief_title, overall_status, phase_label, start_date, "
                         "enrollment_actual, enrollment_planned, lead_sponsor_key FROM gold.fct_trial" + lim).fetchall()
    keep = {t[0] for t in trials}

    for nct, title, status, phase, start, actual, planned, lead in trials:
        t = ID[f"trial/{nct}"]
        g.add((t, RDF.type, TG.ClinicalTrial))
        g.add((t, TG.nctId, Literal(nct, datatype=XSD.string)))
        if title:
            g.add((t, TG.title, Literal(title)))
        g.add((t, TG.hasStatus, ID[f"status/{status}"]))
        if phase and phase != "NOT_REPORTED":
            g.add((t, TG.inPhase, ID[f"phase/{_slug(phase)}"]))
        if start:
            g.add((t, TG.startDate, Literal(start, datatype=XSD.date)))
        if actual is not None:
            g.add((t, TG.actualEnrollment, Literal(int(actual), datatype=XSD.integer)))
        if planned is not None:
            g.add((t, TG.plannedEnrollment, Literal(int(planned), datatype=XSD.integer)))
        if lead:
            g.add((t, TG.hasLeadSponsor, ID[f"org/{_slug(lead)}"]))

    for status, in con.execute("SELECT DISTINCT overall_status FROM gold.fct_trial").fetchall():
        g.add((ID[f"status/{status}"], RDF.type, TG.Status))
        g.add((ID[f"status/{status}"], RDFS.label, Literal(status)))
    for phase, in con.execute("SELECT DISTINCT phase_label FROM gold.fct_trial "
                              "WHERE phase_label <> 'NOT_REPORTED'").fetchall():
        g.add((ID[f"phase/{_slug(phase)}"], RDF.type, TG.Phase))
        g.add((ID[f"phase/{_slug(phase)}"], RDFS.label, Literal(phase)))

    for mesh_id, term in con.execute("SELECT mesh_id, mesh_term FROM gold.dim_condition").fetchall():
        c = MESH[mesh_id]
        g.add((c, RDF.type, TG.Condition))
        g.add((c, SKOS.prefLabel, Literal(term)))
    for nct, mesh_id, rel in con.execute("SELECT nct_id, mesh_id, relation FROM gold.brg_trial_condition").fetchall():
        if nct in keep:
            g.add((ID[f"trial/{nct}"], TG.studiesCondition if rel == "direct" else TG.withinConditionArea,
                   MESH[mesh_id]))

    for key, name, pkey in con.execute("SELECT sponsor_key, sponsor_name, parent_sponsor_key "
                                       "FROM gold.dim_sponsor").fetchall():
        o = ID[f"org/{_slug(key)}"]
        g.add((o, RDF.type, TG.Organization))
        g.add((o, RDFS.label, Literal(name)))
        if pkey:
            g.add((o, TG.subsidiaryOf, ID[f"org/{_slug(pkey)}"]))
    for nct, key, role in con.execute("SELECT nct_id, sponsor_key, role FROM gold.brg_trial_sponsor "
                                      "WHERE role = 'collaborator'").fetchall():
        if nct in keep:
            g.add((ID[f"trial/{nct}"], TG.hasCollaborator, ID[f"org/{_slug(key)}"]))

    for key, name in con.execute("SELECT intervention_key, intervention_name FROM gold.dim_intervention").fetchall():
        i = ID[f"intervention/{_slug(key)}"]
        g.add((i, RDF.type, TG.Intervention))
        g.add((i, RDFS.label, Literal(name)))
    for nct, key in con.execute("SELECT nct_id, intervention_key FROM gold.brg_trial_intervention").fetchall():
        if nct in keep:
            g.add((ID[f"trial/{nct}"], TG.evaluatesIntervention, ID[f"intervention/{_slug(key)}"]))

    for nct, country in con.execute("SELECT nct_id, country FROM gold.brg_trial_country").fetchall():
        if nct in keep:
            cy = ID[f"country/{_slug(country)}"]
            g.add((cy, RDF.type, TG.Country))
            g.add((cy, RDFS.label, Literal(country)))
            g.add((ID[f"trial/{nct}"], TG.conductedIn, cy))
    return g


def validate(g: Graph) -> dict:
    from pyshacl import validate as shacl
    shapes = Graph().parse(CONFIG / "shapes.ttl")
    conforms, report_graph, text = shacl(g, shacl_graph=shapes, inference="none", advanced=True)
    SH = Namespace("http://www.w3.org/ns/shacl#")
    results = list(report_graph.subjects(RDF.type, SH.ValidationResult))
    return {"conforms": bool(conforms), "violations": len(results), "text": text if not conforms else ""}


def trials_in_area(g: Graph, mesh_id: str, direct_only: bool = False) -> set[str]:
    path = "tg:studiesCondition" if direct_only else "(tg:studiesCondition|tg:withinConditionArea)"
    q = f"""PREFIX tg: <{TG}>
            SELECT DISTINCT ?nct WHERE {{ ?t {path} <{MESH[mesh_id]}> ; tg:nctId ?nct . }}"""
    return {str(r.nct) for r in g.query(q)}
