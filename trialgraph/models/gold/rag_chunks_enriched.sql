-- Retrieval chunks carrying the trial's governed attributes as metadata, so a
-- retriever can filter ("completed phase 3 trials only") before it ranks,
-- instead of ranking everything and hoping the filter survives the prompt.
SELECT
    c.chunk_id,
    c.nct_id,
    c.section,
    c.chunk_ord,
    c.chunk_text,
    f.overall_status,
    f.status_group,
    f.phase_label,
    f.start_year,
    s.sponsor_name AS lead_sponsor_name
FROM gold.rag_chunks c
JOIN gold.fct_trial f ON f.nct_id = c.nct_id
LEFT JOIN gold.dim_sponsor s ON s.sponsor_key = f.lead_sponsor_key
