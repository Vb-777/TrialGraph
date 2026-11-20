"""Prepare unstructured trial text for retrieval.

Chunks are cut on word boundaries with overlap, so a sentence split across a
boundary still appears whole in one of the two chunks. Each chunk keeps its
trial ID and section, which is what lets an answer cite the exact record it
came from; the governed metadata (status, phase, sponsor) is joined on in SQL
by `gold.rag_chunks_enriched`, so it always matches the fact table rather than
a copy taken at chunking time.
"""
from __future__ import annotations

from .bulk import create_and_load

SECTIONS = ("brief_summary", "eligibility_criteria")
MAX_WORDS = 120
OVERLAP = 20


def chunk_text(text: str | None, max_words: int = MAX_WORDS, overlap: int = OVERLAP) -> list[str]:
    if not text or not text.strip():
        return []
    if not 0 <= overlap < max_words:
        raise ValueError("overlap must be in [0, max_words)")
    words = text.split()
    if len(words) <= max_words:
        return [" ".join(words)]
    out, step = [], max_words - overlap
    for start in range(0, len(words), step):
        out.append(" ".join(words[start:start + max_words]))
        if start + max_words >= len(words):
            break
    return out


def build(con) -> dict:
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    rows = con.execute(f"SELECT nct_id, {', '.join(SECTIONS)} FROM staging.stg_studies").fetchall()
    out = []
    for nct, *texts in rows:
        for section, text in zip(SECTIONS, texts):
            for i, c in enumerate(chunk_text(text)):
                out.append((f"{nct}#{section}#{i}", nct, section, i, c, len(c.split())))
    create_and_load(con, "gold.rag_chunks", [
        ("chunk_id", "VARCHAR"), ("nct_id", "VARCHAR"), ("section", "VARCHAR"),
        ("chunk_ord", "INTEGER"), ("chunk_text", "VARCHAR"), ("n_words", "INTEGER")], out)
    return {"rag_chunks": len(out)}


LINEAGE = [
    ("staging.stg_studies.nct_id", "gold.rag_chunks.nct_id"),
    ("staging.stg_studies.nct_id", "gold.rag_chunks.chunk_id"),
    ("staging.stg_studies.brief_summary", "gold.rag_chunks.chunk_text"),
    ("staging.stg_studies.eligibility_criteria", "gold.rag_chunks.chunk_text"),
    ("staging.stg_studies.brief_summary", "gold.rag_chunks.n_words"),
    ("staging.stg_studies.eligibility_criteria", "gold.rag_chunks.n_words"),
    ("staging.stg_studies.brief_summary", "gold.rag_chunks.section"),
    ("staging.stg_studies.eligibility_criteria", "gold.rag_chunks.section"),
    ("staging.stg_studies.brief_summary", "gold.rag_chunks.chunk_ord"),
    ("staging.stg_studies.eligibility_criteria", "gold.rag_chunks.chunk_ord"),
]
