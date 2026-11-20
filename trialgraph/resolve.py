"""Entity resolution: decide when two strings in the registry are the same thing.

Registry data is typed by people. The same sponsor appears as "Novartis",
"Novartis Pharmaceuticals" and "NOVARTIS PHARMA AG"; the same drug as its
development code, its generic name and its brand. Any count by sponsor or by
drug that skips this step silently splits one entity into several and every
downstream metric is wrong in a way no query will reveal.

Two different methods, because the problems are different:

Sponsors - rule-based keys. Case-fold, drop parentheticals and punctuation,
then strip *trailing* legal-form tokens ("Pharmaceuticals", "AG", "Inc").
Trailing only: stripping those words anywhere would turn "Pharmaceutical
Research Associates" into "Research Associates". An override file handles what
rules cannot, such as an acronym standing for a full institute name.

Drugs - learned from the source. Each registry intervention lists its own
`otherNames`, and every name in one record refers to the same product. Taking
the union of those links across all records (union-find) groups synonyms the
data itself asserts, with no external dictionary. The risk is over-merging
through a shared generic phrase - "standard of care" listed as an other-name
of two different products would fuse them - so placebo and comparator phrases
are excluded from linking, and unusually large groups are reported for review
rather than trusted.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .bulk import create_and_load

CONFIG = Path(__file__).resolve().parent / "config" / "aliases.yml"

LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "plc", "ag", "ab", "sa", "gmbh", "bv", "nv", "kk", "spa", "srl", "oy",
    "pharmaceuticals", "pharmaceutical", "pharma", "pharmaceutica",
}
LINKABLE_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}
LARGE_GROUP = 8


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def sponsor_key(name: str) -> str:
    s = name.casefold().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = s.split()
    while len(toks) > 1 and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    return " ".join(toks)


_DOSE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|milligrams?|mcg|micrograms?|ug|µg|g|grams?|ml|iu|units?|mmol|meq)\b(?:\s*/\s*(?:day|d|kg|ml|h|hr|dose))?",
    re.I)
_FORM = {"tablet", "tablets", "tab", "tabs", "cap", "caps", "pill", "pills", "capsule", "capsules", "injection", "infusion", "oral", "iv",
         "intravenous", "po", "sc", "subcutaneous", "film-coated", "solution", "daily", "once",
         "twice", "bid", "qd", "od", "dose"}
# Words that name a class or a regimen rather than a product. A record whose
# name contains one of these can belong to a group but must not link two.
CLASS_WORDS = {"therapy", "therapies", "treatment", "drugs", "drug", "medications", "medication",
               "agent", "agents", "blocker", "blockers", "inhibitor", "inhibitors", "inhibition",
               "antagonist", "antagonists", "diuretic", "diuretics", "standard", "usual", "care",
               "regimen", "management", "approved", "conventional", "control", "group", "arm",
               "cohort", "neprilysin", "arb", "arbs", "ace", "acei", "sglt2i"}
MIN_SUPPORT = 2
# Separators that list several things in one name: "X and Y", "X, Y", "X prior to Y".
# A slash is not here - "sacubitril/valsartan" is one fixed-dose product.
_LIST = re.compile(r",|;|\bplus\b|\band\b|\bwith\b|\bvs\.?\b|\bversus\b|\bor\b|"
                   r"\bprior to\b|\bfollowed by\b|\bthen\b|\bafter\b|\bbefore\b")


def name_key(name: str) -> str:
    """Product key: the same product written with a dose, a form or a bracket matches."""
    s = name.casefold().strip()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = _DOSE.sub(" ", s)
    s = re.sub(r"\s+-\s+", "/", s)          # "sacubitril - valsartan" names a combination
    s = re.sub(r"\s*[/+]\s*", "/", s)
    toks = [t for t in re.sub(r"[^\w/\-\s]", " ", s).split()]
    while len(toks) > 1 and toks[-1] in _FORM:
        toks.pop()
    out = " ".join(toks).strip(" -/")
    return out or name.casefold().strip()


def is_stop(key: str, stop: set[str]) -> bool:
    return "placebo" in key or key in stop


def can_bridge(raw: str, key: str, stop: set[str]) -> bool:
    """May this name join two groups? Only if it names exactly one product."""
    if is_stop(key, stop):
        return False
    low = raw.casefold()
    if _LIST.search(re.sub(r"\([^)]*\)", " ", low)):
        return False
    return not (set(re.split(r"[\s/\-]+", key)) & CLASS_WORDS)


# ------------------------------------------------------------------ sponsors
# The registry sometimes writes corporate parentage into the sponsor name:
# "Wyeth is now a wholly owned subsidiary of Pfizer". These patterns pull the
# registered entity and its parent apart. They are deliberately narrow:
# "North Carolina Division of Medical Assistance" contains "division of" and is
# simply a name, so "division of", "part of" and "affiliate of" are not patterns.
_PARENT_PATTERNS = [
    re.compile(r"^(?P<child>.+?)\s*\(\s*(?:a\s+)?(?:wholly[- ]owned\s+)?subsidiary of\s+(?P<parent>[^)]+)\)\s*$", re.I),
    re.compile(r"^(?P<child>.+?),?\s+(?:is\s+(?:now\s+)?)?(?:a\s+)?(?:wholly[- ]owned\s+)?"
               r"subsidiary of\s+(?P<parent>.+)$", re.I),
    re.compile(r"^(?P<child>.+?),?\s+a member of the\s+(?P<parent>.+?)\s+group of companies\b.*$", re.I),
]
_MERGED = re.compile(r"^.*\bmerged with\b.+?\bto form\s+(?P<child>.+)$", re.I)


def parse_corporate(raw: str) -> tuple[str, str | None]:
    """Raw sponsor text -> (registered entity text, parent text or None)."""
    m = _MERGED.match(raw)
    if m:
        return m.group("child").strip(" ."), None
    for pat in _PARENT_PATTERNS:
        m = pat.match(raw)
        if m:
            child = re.sub(r"\s*\([^)]*\)", "", m.group("child")).strip(" ,.")
            return child, m.group("parent").strip(" ,.")
    return raw, None


def resolve_sponsors(rows: list[tuple[str, str]], cfg: dict) -> list[tuple]:
    """(raw name, class) -> (raw, key, display name, class, parent key, parent name)."""
    overrides = {sponsor_key(k): v for k, v in (cfg.get("sponsor_keys") or {}).items()}
    display = cfg.get("sponsor_display") or {}
    parent_overrides = [(k.casefold(), v) for k, v in (cfg.get("sponsor_parents") or {}).items()]
    by_key: dict[str, Counter] = defaultdict(Counter)
    cls_by_key: dict[str, Counter] = defaultdict(Counter)
    keyed = []
    parent_of: dict[str, tuple[str, str]] = {}
    for raw, cls in rows:
        child, parent = parse_corporate(raw)
        k = sponsor_key(child)
        k = overrides.get(k, k)
        by_key[k][child] += 1
        cls_by_key[k][cls or "UNKNOWN"] += 1
        keyed.append((raw, k))
        if parent:
            pk = next((v for pfx, v in parent_overrides if parent.casefold().startswith(pfx)), None)
            if pk is None:
                pk = sponsor_key(parent.split("(")[0])
                pk = overrides.get(pk, pk)
            parent_of[k] = (pk, parent)
    # Most frequent spelling wins, then shortest, then alphabetical: deterministic.
    name_for = {k: display.get(k) or sorted(c.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
                for k, c in by_key.items()}
    cls_for = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in cls_by_key.items()}

    def parent(k: str) -> tuple[str | None, str | None]:
        if k not in parent_of:
            return None, None
        pk, ptext = parent_of[k]
        return pk, name_for.get(pk) or display.get(pk) or ptext.split("(")[0].strip(" ,.")

    return [(raw, k, name_for[k], cls_for[k], *parent(k)) for raw, k in sorted(set(keyed))]


# ------------------------------------------------------------- interventions
class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def resolve_interventions(rows: list[tuple[str, str, str, str | None]], cfg: dict,
                          min_support: int | None = None) -> tuple[list[tuple], dict]:
    """(nct_id, type, name, '|'-joined other names) -> map rows, plus a report.

    Map row: (raw name, name key, intervention key, display name, type, group size, is_placebo).

    Union-find is transitive, so a single wrong assertion fuses two whole groups:
    one record listing Norvasc as another name for eplerenone, and every
    downstream count treats an antihypertensive and a mineralocorticoid
    antagonist as the same drug. So links are accepted in two tiers.

    Corroborated links - asserted by at least `min_support` different trials -
    are accepted outright. Data-entry errors almost never repeat.

    Single-trial links are accepted only when they attach a pure alias: a name
    that never appears as any trial's primary intervention name (a brand, a code
    name). Such a link can only grow one entity, never fuse two. A single-trial
    link between two names that are each used as primary names elsewhere is
    rejected, because that is precisely the shape of every false merge found in
    the real data. And an alias that single-trial links would attach to two
    different entities is ambiguous, and attaches to neither.

    Every rejected link is returned with its trial IDs, so the decision can be
    reviewed rather than trusted.
    """
    min_support = cfg.get("min_link_support", MIN_SUPPORT) if min_support is None else min_support
    stop = {name_key(s) for s in (cfg.get("stop_other_names") or [])}
    prefer = {name_key(k): v for k, v in (cfg.get("intervention_display") or {}).items()}
    uf = _UF()
    primary_count: Counter = Counter()
    spelling: dict[str, Counter] = defaultdict(Counter)
    support: dict[tuple[str, str], set[str]] = defaultdict(set)
    mentions: Counter = Counter()
    for nct, typ, name, others in rows:
        k = name_key(name)
        uf.find(k)
        primary_count[k] += 1
        mentions[k] += 1
        for o in (others or "").split("|"):
            if o:
                mentions[name_key(o)] += 1
        spelling[k][name.strip()] += 1
        if typ not in LINKABLE_TYPES or not can_bridge(name, k, stop):
            continue
        for o in (others or "").split("|"):
            ok = name_key(o)
            if ok and ok != k and can_bridge(o, ok, stop):
                support[tuple(sorted((k, ok)))].add(nct)

    primary = set(primary_count)
    strong = {e: n for e, n in support.items() if len(n) >= min_support}
    weak = {e: n for e, n in support.items() if len(n) < min_support}
    for a, b in strong:
        uf.union(a, b)

    # Freeze the components left by the corroborated links, and classify every
    # single-trial link against that snapshot before applying any of them.
    # Applying unions while still classifying moves roots under the loop's
    # feet: decisions then depend on iteration order, and membership lookups
    # silently return empty sets. Classify first, mutate after.
    root0 = {k: uf.find(k) for k in list(uf.p)}
    members0: dict[str, set[str]] = defaultdict(set)
    for k, r in root0.items():
        members0[r].add(k)

    def r0(x: str) -> str:
        return root0.get(x, x)

    def m0(r: str) -> set[str]:
        return members0.get(r) or {r}

    established = {r for r, ms in members0.items() if ms & primary}

    # Pass 1: links between two pure aliases. These join nothing established,
    # so they are safe to apply - and they must be applied before ambiguity is
    # judged, or two aliases pointing at different drugs, linked to each
    # other, would chain those two drugs together.
    alias_uf = _UF()
    for (a, b), n in weak.items():
        ra, rb = r0(a), r0(b)
        if ra != rb and ra not in established and rb not in established:
            alias_uf.union(ra, rb)

    def alias_comp(r: str) -> str:
        return alias_uf.find(r)

    # Pass 2: classify links touching an established entity, per alias component.
    rejected: dict[tuple[str, str], tuple[set[str], str]] = {}
    attach: dict[str, set[str]] = defaultdict(set)
    attach_edges: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (a, b), n in weak.items():
        ra, rb = r0(a), r0(b)
        if ra == rb:
            continue
        ea, eb = ra in established, rb in established
        if ea and eb:
            rejected[(a, b)] = (n, "would merge two established entities")
        elif ea or eb:
            comp = alias_comp(rb if ea else ra)
            attach[comp].add(ra if ea else rb)
            attach_edges[comp].append((a, b))

    comp_members: dict[str, set[str]] = defaultdict(set)
    for r in {r0(x) for e in weak for x in e}:
        if r not in established:
            comp_members[alias_comp(r)] |= m0(r)

    accepted_weak = 0
    single_trial_alias: set[str] = set()
    for comp, targets in attach.items():
        if len(targets) > 1:
            for e in attach_edges[comp]:
                rejected[e] = (weak[e], "ambiguous alias: points at more than one entity")
            continue
        for a, b in attach_edges[comp]:
            uf.union(a, b)
            accepted_weak += 1
        single_trial_alias |= comp_members[comp]
    # Alias-only links last: they now only ever grow an entity that one
    # unambiguous attachment already chose, or stay standalone.
    for (a, b), n in weak.items():
        ra, rb = r0(a), r0(b)
        if ra != rb and ra not in established and rb not in established:
            comp = alias_comp(ra)
            if comp in attach and len(attach[comp]) > 1:
                continue
            uf.union(a, b)

    groups: dict[str, set[str]] = defaultdict(set)
    for k in list(uf.p):
        groups[uf.find(k)].add(k)

    canon: dict[str, tuple[str, str]] = {}
    warnings = []
    for root, members in groups.items():
        pref = next((prefer[m] for m in sorted(members) if m in prefer), None)
        if pref is None:
            # The member used most often as a primary name, shown in its most
            # common original spelling - "Dapagliflozin", not the key "dapagliflozin".
            best = sorted(members, key=lambda m: (-primary_count[m], -mentions[m], len(m), m))[0]
            pref = (sorted(spelling[best].items(),
                           key=lambda kv: (kv[0].casefold() != best, -kv[1], len(kv[0]), kv[0]))[0][0]
                    if spelling[best] else best)
        canon[root] = (name_key(pref), pref)
        if len(members) > LARGE_GROUP:
            warnings.append({"group": pref, "size": len(members), "members": sorted(members)})

    # Confidence travels with every alias, so a consumer can decide how far to
    # trust a lookup: "primary" names are the entity's own, "corroborated"
    # aliases are asserted by two or more trials, "single_trial" aliases by one.
    alias_rows = []
    for k in sorted(uf.p):
        ckey, cname = canon[uf.find(k)]
        conf = "primary" if k in primary else ("single_trial" if k in single_trial_alias else "corroborated")
        alias_rows.append((k, "placebo" if "placebo" in k else ckey,
                           "Placebo" if "placebo" in k else cname, k in primary, conf))

    # A raw name can be entered under different intervention types by different
    # trials; take its most common type, ties alphabetical, so the result does
    # not depend on the order rows arrive in.
    type_counts: dict[str, Counter] = defaultdict(Counter)
    for _, typ, name, _ in rows:
        type_counts[name][typ or "OTHER"] += 1

    out = []
    for name in sorted({r[2] for r in rows}):
        typ = sorted(type_counts[name].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        k = name_key(name)
        root = uf.find(k)
        ckey, cname = canon[root]
        placebo = "placebo" in k
        if placebo:
            ckey, cname = "placebo", "Placebo"
        out.append((name, k, ckey, cname, typ, len(groups[root]), placebo))
    report = {"links_asserted": len(support), "links_corroborated": len(strong),
              "links_alias_attached": accepted_weak, "links_rejected": len(rejected),
              "min_support": min_support, "large_groups": warnings,
              "rejected": sorted([a, b, sorted(n), why] for (a, b), (n, why) in rejected.items()),
              "aliases": alias_rows}
    return out, report


# ------------------------------------------------------------------- runner
def build(con, cfg: dict | None = None) -> dict:
    cfg = load_config() if cfg is None else cfg
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")

    spon = resolve_sponsors(con.execute(
        "SELECT sponsor_name_raw, sponsor_class FROM staging.stg_sponsors "
        "WHERE sponsor_name_raw IS NOT NULL").fetchall(), cfg)
    create_and_load(con, "silver.map_sponsor", [
        ("sponsor_name_raw", "VARCHAR"), ("sponsor_key", "VARCHAR"),
        ("sponsor_name", "VARCHAR"), ("sponsor_class", "VARCHAR"),
        ("parent_sponsor_key", "VARCHAR"), ("parent_sponsor_name", "VARCHAR")], spon)

    intr, ireport = resolve_interventions(con.execute(
        "SELECT nct_id, intervention_type, intervention_name, other_names FROM staging.stg_interventions "
        "WHERE intervention_name IS NOT NULL ORDER BY nct_id, ordinal").fetchall(), cfg)
    create_and_load(con, "silver.map_intervention", [
        ("intervention_name_raw", "VARCHAR"), ("name_key", "VARCHAR"), ("intervention_key", "VARCHAR"),
        ("intervention_name", "VARCHAR"), ("intervention_type", "VARCHAR"),
        ("group_size", "INTEGER"), ("is_placebo", "BOOLEAN")], intr)

    create_and_load(con, "silver.intervention_alias", [
        ("alias_key", "VARCHAR"), ("intervention_key", "VARCHAR"),
        ("intervention_name", "VARCHAR"), ("is_primary_name", "BOOLEAN"),
        ("confidence", "VARCHAR")], ireport["aliases"])
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    create_and_load(con, "meta.rejected_links", [
        ("name_a", "VARCHAR"), ("name_b", "VARCHAR"), ("asserted_by", "VARCHAR"), ("reason", "VARCHAR")],
        [(a, b, "|".join(n), why) for a, b, n, why in ireport["rejected"]])

    return {"map_sponsor": len(spon), "map_intervention": len(intr),
            "aliases": len(ireport["aliases"]),
            "sponsor_raw_names": len(spon),
            "sponsor_entities": len({r[1] for r in spon}),
            "sponsors_with_parent": len({r[1] for r in spon if r[4]}),
            "intervention_raw_names": len(intr),
            "intervention_entities": len({r[2] for r in intr}),
            "links_asserted": ireport["links_asserted"],
            "links_corroborated": ireport["links_corroborated"],
            "links_alias_attached": ireport["links_alias_attached"],
            "links_rejected": ireport["links_rejected"],
            "large_group_warnings": ireport["large_groups"], "rejected_links": ireport["rejected"]}


LINEAGE = [
    ("staging.stg_sponsors.sponsor_name_raw", "silver.map_sponsor.sponsor_name_raw"),
    ("staging.stg_sponsors.sponsor_name_raw", "silver.map_sponsor.sponsor_key"),
    ("staging.stg_sponsors.sponsor_name_raw", "silver.map_sponsor.sponsor_name"),
    ("staging.stg_sponsors.sponsor_class", "silver.map_sponsor.sponsor_class"),
    ("staging.stg_sponsors.sponsor_name_raw", "silver.map_sponsor.parent_sponsor_key"),
    ("staging.stg_sponsors.sponsor_name_raw", "silver.map_sponsor.parent_sponsor_name"),
    ("staging.stg_interventions.intervention_name", "silver.map_intervention.intervention_name_raw"),
    ("staging.stg_interventions.intervention_name", "silver.map_intervention.name_key"),
    ("staging.stg_interventions.intervention_name", "silver.map_intervention.intervention_key"),
    ("staging.stg_interventions.other_names", "silver.map_intervention.intervention_key"),
    ("staging.stg_interventions.intervention_name", "silver.map_intervention.intervention_name"),
    ("staging.stg_interventions.other_names", "silver.map_intervention.intervention_name"),
    ("staging.stg_interventions.intervention_type", "silver.map_intervention.intervention_type"),
    ("staging.stg_interventions.other_names", "silver.map_intervention.group_size"),
    ("staging.stg_interventions.intervention_name", "silver.map_intervention.is_placebo"),
    ("staging.stg_interventions.intervention_name", "silver.intervention_alias.alias_key"),
    ("staging.stg_interventions.other_names", "silver.intervention_alias.alias_key"),
    ("staging.stg_interventions.intervention_name", "silver.intervention_alias.intervention_key"),
    ("staging.stg_interventions.other_names", "silver.intervention_alias.intervention_key"),
    ("staging.stg_interventions.intervention_name", "silver.intervention_alias.intervention_name"),
    ("staging.stg_interventions.intervention_name", "silver.intervention_alias.is_primary_name"),
    ("staging.stg_interventions.other_names", "silver.intervention_alias.confidence"),
]
