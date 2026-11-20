-- One row per resolved intervention entity.
SELECT
    intervention_key,
    max(intervention_name) AS intervention_name,
    min(intervention_type) AS intervention_type,
    max(group_size) AS synonym_count,
    max(CASE WHEN is_placebo THEN 1 ELSE 0 END) = 1 AS is_placebo
FROM silver.map_intervention
GROUP BY intervention_key
