-- One row per MeSH descriptor that any trial maps to, directly or as an ancestor.
SELECT
    mesh_id,
    max(mesh_term) AS mesh_term,
    count(DISTINCT CASE WHEN relation = 'direct' THEN nct_id END) AS trials_direct,
    count(DISTINCT nct_id) AS trials_direct_or_descendant
FROM silver.trial_condition_mesh
GROUP BY mesh_id
