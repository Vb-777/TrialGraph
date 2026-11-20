"""Filtered BM25 retrieval over trial text chunks.

Filters are applied *before* ranking, in SQL, against governed columns joined
from the fact table. The alternative - rank everything, then ask a language
model to respect "completed phase 3 only" - lets the filter be forgotten or
misread, and an answer about a recruiting phase-2 trial gets cited as if it
were a completed phase-3 one. Here a chunk that fails the filter is never a
candidate.

BM25 rather than embeddings, deliberately: it needs no model download, is
deterministic, and is a strong baseline on registry text, which is dense with
exact terms - drug codes, NCT IDs, eligibility thresholds - that dense
retrieval tends to blur. The index interface is small enough that an
embedding index can replace it without touching the tools.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
STOP = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "with", "by", "is", "are",
        "be", "this", "that", "will", "at", "as", "from", "who", "patients", "study", "trial"}

FILTERABLE = {"status": "overall_status", "status_group": "status_group", "phase": "phase_label",
              "start_year": "start_year", "lead_sponsor": "lead_sponsor_name", "section": "section"}


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.casefold()) if t not in STOP]


class BM25:
    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.ids = [d[0] for d in docs]
        self.tf = [Counter(tokens(d[1])) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df = Counter(t for c in self.tf for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 5, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        q = tokens(query)
        scores = []
        for i, tf in enumerate(self.tf):
            if allowed is not None and self.ids[i] not in allowed:
                continue
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scores.append((self.ids[i], s))
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:k]


class ChunkIndex:
    def __init__(self, con):
        self.con = con
        rows = con.execute("SELECT chunk_id, chunk_text FROM gold.rag_chunks_enriched").fetchall()
        self.bm25 = BM25(rows)

    def allowed(self, where: dict | None) -> set[str] | None:
        if not where:
            return None
        conds, params = [], []
        for k, v in where.items():
            if k not in FILTERABLE:
                raise ValueError(f"cannot filter on {k!r}; filterable: {sorted(FILTERABLE)}")
            vals = v if isinstance(v, list) else [v]
            conds.append(f"{FILTERABLE[k]} IN ({', '.join('?' * len(vals))})")
            params += vals
        return {r[0] for r in self.con.execute(
            f"SELECT chunk_id FROM gold.rag_chunks_enriched WHERE {' AND '.join(conds)}", params).fetchall()}

    def search(self, query: str, where: dict | None = None, k: int = 5) -> list[dict]:
        hits = self.bm25.search(query, k=k, allowed=self.allowed(where))
        if not hits:
            return []
        ids = [h[0] for h in hits]
        meta = {r[0]: r for r in self.con.execute(
            f"SELECT chunk_id, nct_id, section, chunk_text, overall_status, phase_label, lead_sponsor_name "
            f"FROM gold.rag_chunks_enriched WHERE chunk_id IN ({', '.join('?' * len(ids))})", ids).fetchall()}
        return [{"chunk_id": cid, "nct_id": meta[cid][1], "section": meta[cid][2], "score": round(s, 4),
                 "text": meta[cid][3], "status": meta[cid][4], "phase": meta[cid][5],
                 "lead_sponsor": meta[cid][6]} for cid, s in hits]
