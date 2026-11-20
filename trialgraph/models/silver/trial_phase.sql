-- One row per trial. A trial can list two phases (PHASE2 + PHASE3); the label
-- keeps the combination and phase_rank keeps the highest, so "how many trials
-- reached phase 3" and "how many were seamless 2/3 designs" are both answerable.
SELECT
    t.nct_id,
    CASE
        WHEN max(CASE WHEN p.phase = 'PHASE1' THEN 1 ELSE 0 END) = 1
         AND max(CASE WHEN p.phase = 'PHASE2' THEN 1 ELSE 0 END) = 1 THEN 'PHASE1/PHASE2'
        WHEN max(CASE WHEN p.phase = 'PHASE2' THEN 1 ELSE 0 END) = 1
         AND max(CASE WHEN p.phase = 'PHASE3' THEN 1 ELSE 0 END) = 1 THEN 'PHASE2/PHASE3'
        WHEN max(CASE WHEN p.phase = 'EARLY_PHASE1' THEN 1 ELSE 0 END) = 1 THEN 'EARLY_PHASE1'
        WHEN max(CASE WHEN p.phase = 'PHASE1' THEN 1 ELSE 0 END) = 1 THEN 'PHASE1'
        WHEN max(CASE WHEN p.phase = 'PHASE2' THEN 1 ELSE 0 END) = 1 THEN 'PHASE2'
        WHEN max(CASE WHEN p.phase = 'PHASE3' THEN 1 ELSE 0 END) = 1 THEN 'PHASE3'
        WHEN max(CASE WHEN p.phase = 'PHASE4' THEN 1 ELSE 0 END) = 1 THEN 'PHASE4'
        WHEN max(CASE WHEN p.phase = 'NA' THEN 1 ELSE 0 END) = 1 THEN 'NA'
        ELSE 'NOT_REPORTED'
    END AS phase_label,
    max(CASE p.phase WHEN 'EARLY_PHASE1' THEN 0 WHEN 'PHASE1' THEN 1 WHEN 'PHASE2' THEN 2
                     WHEN 'PHASE3' THEN 3 WHEN 'PHASE4' THEN 4 END) AS phase_rank
FROM staging.stg_studies t
LEFT JOIN staging.stg_phases p ON p.nct_id = t.nct_id
GROUP BY t.nct_id
