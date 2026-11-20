-- Whether a trial publishes a central contact - derived from row existence
-- only. Names, emails and phone numbers stop at staging: they are personal
-- data, no analytical question needs them, and the lineage check in
-- quality.py fails the build if any column descended from them reaches gold.
SELECT DISTINCT nct_id, TRUE AS has_central_contact
FROM staging.stg_contacts
WHERE ordinal IS NOT NULL
