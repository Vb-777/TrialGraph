**31 pass · 2 warn · 0 fail**

| check | severity | failing | status |
|---|---|---:|---|
| `silver.trial.nct_id.not_null` | error | 0 | pass |
| `silver.trial.nct_id.unique` | error | 0 | pass |
| `silver.trial.nct_id.regex` | error | 0 | pass |
| `silver.trial.overall_status.accepted_values` | warn | 0 | pass |
| `silver.trial.start_date_parses` | warn | 0 | pass |
| `silver.trial.enrollment_non_negative` | error | 0 | pass |
| `silver.trial.completion_not_before_start` | warn | 0 | pass |
| `silver.trial.enrollment_type_consistent` | error | 0 | pass |
| `gold.fct_trial.nct_id.unique` | error | 0 | pass |
| `gold.fct_trial.lead_sponsor_key.not_null` | warn | 0 | pass |
| `gold.fct_trial.lead_sponsor_key.relationship` | error | 0 | pass |
| `gold.fct_trial.status_group.accepted_values` | error | 0 | pass |
| `gold.fct_trial.enrollment_population_scale` | warn | 2 | warn |
| `gold.fct_trial.has_mesh_condition` | warn | 579 | warn |
| `gold.brg_trial_condition.nct_id+mesh_id.unique` | error | 0 | pass |
| `gold.brg_trial_condition.nct_id.relationship` | error | 0 | pass |
| `gold.brg_trial_condition.mesh_id.relationship` | error | 0 | pass |
| `gold.brg_trial_intervention.nct_id.relationship` | error | 0 | pass |
| `gold.brg_trial_intervention.intervention_key.relationship` | error | 0 | pass |
| `gold.brg_trial_sponsor.nct_id.relationship` | error | 0 | pass |
| `gold.brg_trial_sponsor.sponsor_key.relationship` | error | 0 | pass |
| `gold.brg_trial_sponsor.role.accepted_values` | error | 0 | pass |
| `gold.brg_trial_country.nct_id+country.unique` | error | 0 | pass |
| `gold.brg_trial_country.nct_id.relationship` | error | 0 | pass |
| `gold.brg_trial_condition_text.nct_id.relationship` | error | 0 | pass |
| `gold.dim_intervention.intervention_key.unique` | error | 0 | pass |
| `gold.dim_intervention.no_runaway_synonym_group` | warn | 0 | pass |
| `gold.rag_chunks.chunk_id.unique` | error | 0 | pass |
| `gold.rag_chunks.nct_id.relationship` | error | 0 | pass |
| `reconcile.bronze_to_silver_trials` | error | 0 | pass |
| `reconcile.silver_to_gold_trials` | error | 0 | pass |
| `governance.pii_lineage` | error | 0 | pass |
| `governance.gold_documented` | error | 0 | pass |