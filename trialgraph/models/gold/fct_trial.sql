-- The trial fact: one row per trial, at the grain every metric is defined on.
SELECT
    t.nct_id,
    t.brief_title,
    t.overall_status,
    CASE
        WHEN t.overall_status IN ('RECRUITING', 'NOT_YET_RECRUITING',
                                  'ENROLLING_BY_INVITATION', 'ACTIVE_NOT_RECRUITING') THEN 'ongoing'
        WHEN t.overall_status = 'COMPLETED' THEN 'completed'
        WHEN t.overall_status IN ('TERMINATED', 'WITHDRAWN', 'SUSPENDED') THEN 'stopped'
        ELSE 'unknown'
    END AS status_group,
    t.why_stopped,
    p.phase_label,
    p.phase_rank,
    t.start_date,
    t.start_date_precision,
    EXTRACT(YEAR FROM t.start_date) AS start_year,
    t.primary_completion_date,
    t.enrollment_actual,
    t.enrollment_planned,
    t.enrollment_type,
    t.has_results,
    t.allocation,
    ls.sponsor_key AS lead_sponsor_key,
    COALESCE(c.has_central_contact, FALSE) AS has_central_contact,
    t.last_update_posted
FROM silver.trial t
JOIN silver.trial_phase p ON p.nct_id = t.nct_id
LEFT JOIN silver.trial_sponsor ls ON ls.nct_id = t.nct_id AND ls.role = 'lead'
LEFT JOIN silver.trial_contact_flag c ON c.nct_id = t.nct_id
