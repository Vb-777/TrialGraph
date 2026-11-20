-- One row per (trial, resolved intervention). Two registry entries that
-- resolve to the same product collapse to one row, so a trial is never
-- counted twice for the same drug just because it named it twice.
SELECT DISTINCT
    i.nct_id,
    m.intervention_key,
    m.is_placebo
FROM staging.stg_interventions i
JOIN silver.map_intervention m ON m.intervention_name_raw = i.intervention_name
