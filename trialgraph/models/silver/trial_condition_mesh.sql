-- One row per (trial, MeSH descriptor). The registry lists a descriptor as a
-- direct mapping of the trial's conditions or as an ancestor of one; when it
-- appears as both, "direct" wins, because it is the stronger statement.
SELECT
    nct_id,
    mesh_id,
    max(mesh_term) AS mesh_term,
    CASE WHEN sum(CASE WHEN relation = 'direct' THEN 1 ELSE 0 END) > 0
         THEN 'direct' ELSE 'ancestor' END AS relation
FROM staging.stg_condition_mesh
WHERE mesh_id IS NOT NULL
GROUP BY nct_id, mesh_id
