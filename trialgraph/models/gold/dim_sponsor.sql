-- One row per resolved sponsor, with how many spellings were folded into it.
-- parent_sponsor_* is set only where the registry itself states the corporate
-- relationship; group_sponsor_* rolls each sponsor up to that parent (or to
-- itself), which is the level portfolio questions are usually asked at.
SELECT
    m.sponsor_key,
    max(m.sponsor_name) AS sponsor_name,
    max(m.sponsor_class) AS sponsor_class,
    count(DISTINCT m.sponsor_name_raw) AS raw_name_variants,
    max(m.parent_sponsor_key) AS parent_sponsor_key,
    max(m.parent_sponsor_name) AS parent_sponsor_name,
    COALESCE(max(m.parent_sponsor_key), m.sponsor_key) AS group_sponsor_key,
    COALESCE(max(m.parent_sponsor_name), max(m.sponsor_name)) AS group_sponsor_name
FROM silver.map_sponsor m
GROUP BY m.sponsor_key
