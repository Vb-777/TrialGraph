-- One row per trial: the latest registry version, typed and conformed.
-- Partial dates ("2019-03") become the first of the month, and the precision
-- is kept beside them so nothing downstream mistakes "March" for "1 March".
-- Estimated and actual enrollment go into separate columns, because they
-- answer different questions and must never be summed together.
SELECT
    s.nct_id,
    s.brief_title,
    CASE WHEN s.overall_status IN (
            'ACTIVE_NOT_RECRUITING', 'COMPLETED', 'ENROLLING_BY_INVITATION',
            'NOT_YET_RECRUITING', 'RECRUITING', 'SUSPENDED', 'TERMINATED', 'WITHDRAWN',
            'AVAILABLE', 'NO_LONGER_AVAILABLE', 'TEMPORARILY_NOT_AVAILABLE',
            'APPROVED_FOR_MARKETING', 'WITHHELD', 'UNKNOWN')
         THEN s.overall_status ELSE 'UNRECOGNISED' END AS overall_status,
    s.overall_status AS overall_status_raw,
    s.why_stopped,
    CASE WHEN length(s.start_date_raw) = 7 THEN TRY_CAST(s.start_date_raw || '-01' AS DATE)
         WHEN length(s.start_date_raw) = 10 THEN TRY_CAST(s.start_date_raw AS DATE) END AS start_date,
    CASE WHEN length(s.start_date_raw) = 7 THEN 'month'
         WHEN length(s.start_date_raw) = 10 THEN 'day' END AS start_date_precision,
    s.start_date_raw,
    CASE WHEN length(s.primary_completion_date_raw) = 7 THEN TRY_CAST(s.primary_completion_date_raw || '-01' AS DATE)
         WHEN length(s.primary_completion_date_raw) = 10 THEN TRY_CAST(s.primary_completion_date_raw AS DATE) END
        AS primary_completion_date,
    s.primary_completion_date_type,
    TRY_CAST(s.last_update_posted_raw AS DATE) AS last_update_posted,
    s.enrollment_count,
    s.enrollment_type,
    CASE WHEN s.enrollment_type = 'ACTUAL' THEN s.enrollment_count END AS enrollment_actual,
    CASE WHEN s.enrollment_type = 'ESTIMATED' THEN s.enrollment_count END AS enrollment_planned,
    s.has_results,
    s.allocation,
    s.primary_purpose,
    s.study_type,
    s.batch_id,
    s.ingested_at
FROM staging.stg_studies s
