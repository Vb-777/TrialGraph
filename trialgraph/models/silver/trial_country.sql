-- One row per (trial, country). Sites are many; countries are what most
-- questions are about.
SELECT DISTINCT nct_id, country
FROM staging.stg_locations
WHERE country IS NOT NULL
