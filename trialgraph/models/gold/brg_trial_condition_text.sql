-- Trial to condition as the sponsor wrote it. Kept beside the MeSH bridge
-- because neither is complete: on the heart-failure download, 509 trials
-- whose own conditions read "Chronic Heart Failure" or similar carry no MeSH
-- mapping at all, while 342 MeSH-mapped trials never use the words.
SELECT DISTINCT nct_id, condition_text, lower(condition_text) AS condition_text_lower
FROM staging.stg_conditions
WHERE condition_text IS NOT NULL
