-- One row per (trial, resolved sponsor, role).
SELECT DISTINCT
    s.nct_id,
    m.sponsor_key,
    s.role
FROM staging.stg_sponsors s
JOIN silver.map_sponsor m ON m.sponsor_name_raw = s.sponsor_name_raw
