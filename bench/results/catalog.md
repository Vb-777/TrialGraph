# TrialGraph data catalog (gold layer)

33 tables, 194 columns, 193 lineage edges. Gold: 66/66 columns documented, 65/66 traced to a source JSON path.

## `gold.brg_trial_condition`

Trial to MeSH descriptor. Many-to-many - aggregate through it with COUNT DISTINCT.  
Grain: `nct_id, mesh_id` · Rows: 35,478 · Owner: dsai-data-engineering  
Built from: `silver.trial_condition_mesh`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `mesh_id` | VARCHAR | MeSH descriptor | `derivedSection.conditionBrowseModule.ancestors[*].id`<br>`derivedSection.conditionBrowseModule.meshes[*].id` |
| `relation` | VARCHAR | direct | ancestor - how the descriptor relates to the trial's conditions | `derivedSection.conditionBrowseModule.ancestors[*]`<br>`derivedSection.conditionBrowseModule.meshes[*]` |

## `gold.brg_trial_condition_text`

Trial to condition as registered in free text. Complements the MeSH bridge, which misses trials the registry never mapped.  
Grain: `nct_id, condition_text` · Rows: 10,907 · Owner: dsai-data-engineering  
Built from: `staging.stg_conditions`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `condition_text` | VARCHAR | Condition exactly as the sponsor registered it | `protocolSection.conditionsModule.conditions[*]` |
| `condition_text_lower` | VARCHAR | Lower-cased condition text for case-insensitive matching | `protocolSection.conditionsModule.conditions[*]` |

## `gold.brg_trial_country`

Trial to country with at least one site.  
Grain: `nct_id, country` · Rows: 9,216 · Owner: dsai-data-engineering  
Built from: `silver.trial_country`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `country` | VARCHAR | Country name as registered | `protocolSection.contactsLocationsModule.locations[*].country` |

## `gold.brg_trial_intervention`

Trial to resolved intervention.  
Grain: `nct_id, intervention_key` · Rows: 8,792 · Owner: dsai-data-engineering  
Built from: `silver.trial_intervention`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `intervention_key` | VARCHAR | Resolved intervention | `protocolSection.armsInterventionsModule.interventions[*].name`<br>`protocolSection.armsInterventionsModule.interventions[*].otherNames` |
| `is_placebo` | BOOLEAN | Whether this arm's intervention is a placebo | `protocolSection.armsInterventionsModule.interventions[*].name` |

## `gold.brg_trial_sponsor`

Trial to resolved sponsor, lead and collaborators.  
Grain: `nct_id, sponsor_key, role` · Rows: 9,423 · Owner: dsai-data-engineering  
Built from: `silver.trial_sponsor`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `sponsor_key` | VARCHAR | Resolved sponsor | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `role` | VARCHAR | lead | collaborator | `protocolSection.sponsorCollaboratorsModule.collaborators[*]`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor` |

## `gold.dim_condition`

One row per MeSH descriptor any trial maps to.  
Grain: `mesh_id` · Rows: 870 · Owner: dsai-data-engineering  
Built from: `silver.trial_condition_mesh`

| column | type | description | source path(s) |
|---|---|---|---|
| `mesh_id` | VARCHAR | MeSH descriptor ID as supplied by the registry | `derivedSection.conditionBrowseModule.ancestors[*].id`<br>`derivedSection.conditionBrowseModule.meshes[*].id` |
| `mesh_term` | VARCHAR | MeSH preferred term | `derivedSection.conditionBrowseModule.ancestors[*].term`<br>`derivedSection.conditionBrowseModule.meshes[*].term` |
| `trials_direct` | BIGINT | Trials whose conditions map to this descriptor directly | `derivedSection.conditionBrowseModule.ancestors[*]`<br>`derivedSection.conditionBrowseModule.meshes[*]`<br>`protocolSection.identificationModule.nctId` |
| `trials_direct_or_descendant` | BIGINT | Trials mapping to this descriptor or any descriptor beneath it | `protocolSection.identificationModule.nctId` |

## `gold.dim_intervention`

One row per resolved product or intervention.  
Grain: `intervention_key` · Rows: 5,476 · Owner: dsai-data-engineering  
Built from: `silver.map_intervention`

| column | type | description | source path(s) |
|---|---|---|---|
| `intervention_key` | VARCHAR | Resolved product key | `protocolSection.armsInterventionsModule.interventions[*].name`<br>`protocolSection.armsInterventionsModule.interventions[*].otherNames` |
| `intervention_name` | VARCHAR | Display name | `protocolSection.armsInterventionsModule.interventions[*].name`<br>`protocolSection.armsInterventionsModule.interventions[*].otherNames` |
| `intervention_type` | VARCHAR | DRUG | BIOLOGICAL | DEVICE | PROCEDURE | BEHAVIORAL | ... | `protocolSection.armsInterventionsModule.interventions[*].type` |
| `synonym_count` | INTEGER | Number of names resolved to this product | `protocolSection.armsInterventionsModule.interventions[*].otherNames` |
| `is_placebo` | BOOLEAN | Whether this entity is a placebo | `protocolSection.armsInterventionsModule.interventions[*].name` |

## `gold.dim_sponsor`

One row per resolved sponsor entity.  
Grain: `sponsor_key` · Rows: 3,524 · Owner: dsai-data-engineering  
Built from: `silver.map_sponsor`

| column | type | description | source path(s) |
|---|---|---|---|
| `sponsor_key` | VARCHAR | Normalised sponsor key | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `sponsor_name` | VARCHAR | Display name - the most frequent registered spelling | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `sponsor_class` | VARCHAR | INDUSTRY | NIH | FED | OTHER_GOV | NETWORK | INDIV | OTHER | `protocolSection.sponsorCollaboratorsModule.collaborators[*].class`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.class` |
| `raw_name_variants` | BIGINT | Number of distinct registered spellings folded into this sponsor | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `parent_sponsor_key` | VARCHAR | Parent company where the registry itself states one; else null | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `parent_sponsor_name` | VARCHAR | Parent company display name | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `group_sponsor_key` | VARCHAR | Parent if stated | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `group_sponsor_name` | VARCHAR | Display name at the roll-up level | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |

## `gold.fct_trial`

The trial fact. One row per trial; every governed metric is defined at this grain.  
Grain: `nct_id` · Rows: 5,317 · Owner: dsai-data-engineering  
Built from: `silver.trial`, `silver.trial_contact_flag`, `silver.trial_phase`, `silver.trial_sponsor`

| column | type | description | source path(s) |
|---|---|---|---|
| `nct_id` | VARCHAR | ClinicalTrials.gov identifier | `protocolSection.identificationModule.nctId` |
| `brief_title` | VARCHAR | Short public title of the trial | `protocolSection.identificationModule.briefTitle` |
| `overall_status` | VARCHAR | Registry recruitment status; UNRECOGNISED if outside the registry's code list | `protocolSection.statusModule.overallStatus` |
| `status_group` | VARCHAR | ongoing | completed | stopped | unknown - status collapsed for analysis | `protocolSection.statusModule.overallStatus` |
| `why_stopped` | VARCHAR | Free-text reason given for terminating | `protocolSection.statusModule.whyStopped` |
| `phase_label` | VARCHAR | Phase, keeping combinations such as PHASE2/PHASE3; NA for non-drug trials | `protocolSection.designModule.phases[*]` |
| `phase_rank` | INTEGER | Highest phase reached, 0 = early phase 1 to 4 = phase 4; null if not applicable | `protocolSection.designModule.phases[*]` |
| `start_date` | DATE | Start date; month-only registry dates are set to the 1st - see start_date_precision | `protocolSection.statusModule.startDateStruct.date` |
| `start_date_precision` | VARCHAR | day | month - how precise start_date really is | `protocolSection.statusModule.startDateStruct.date` |
| `start_year` | BIGINT | Calendar year of start_date | `protocolSection.statusModule.startDateStruct.date` |
| `primary_completion_date` | DATE | Primary completion date | `protocolSection.statusModule.primaryCompletionDateStruct.date` |
| `enrollment_actual` | INTEGER | Participants actually enrolled; null unless the registry marks the count ACTUAL | `protocolSection.designModule.enrollmentInfo.count`<br>`protocolSection.designModule.enrollmentInfo.type` |
| `enrollment_planned` | INTEGER | Anticipated enrollment; null unless the registry marks the count ESTIMATED | `protocolSection.designModule.enrollmentInfo.count`<br>`protocolSection.designModule.enrollmentInfo.type` |
| `enrollment_type` | VARCHAR | ACTUAL | ESTIMATED - which of the two columns above is populated | `protocolSection.designModule.enrollmentInfo.type` |
| `has_results` | BOOLEAN | Whether results are posted on the registry | `hasResults` |
| `allocation` | VARCHAR | RANDOMIZED | NON_RANDOMIZED | NA | `protocolSection.designModule.designInfo.allocation` |
| `lead_sponsor_key` | VARCHAR | Resolved lead sponsor; joins gold.dim_sponsor | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
| `has_central_contact` | BOOLEAN | Whether a central contact is published. Derived from whether a contact row exists, never from contact values - deliberately has no source path, which is what keeps it clear of the PII lineage check. | _derived_ |
| `last_update_posted` | DATE | Date the registry record was last updated | `protocolSection.statusModule.lastUpdatePostDateStruct.date` |

## `gold.rag_chunks`

Retrieval chunks of each trial's summary and eligibility text.  
Grain: `chunk_id` · Rows: 21,009 · Owner: dsai-data-engineering  
Built from: `staging.stg_studies`

| column | type | description | source path(s) |
|---|---|---|---|
| `chunk_id` | VARCHAR | nct_id#section#ordinal - stable citation handle | `protocolSection.identificationModule.nctId` |
| `nct_id` | VARCHAR | Trial the chunk came from | `protocolSection.identificationModule.nctId` |
| `section` | VARCHAR | brief_summary | eligibility_criteria | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `chunk_ord` | INTEGER | Position of the chunk within its section | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `chunk_text` | VARCHAR | Chunk text | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `n_words` | INTEGER | Words in the chunk | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |

## `gold.rag_chunks_enriched`

Chunks with governed trial attributes attached for filtered retrieval.  
Grain: `chunk_id` · Rows: 21,009 · Owner: dsai-data-engineering  
Built from: `gold.dim_sponsor`, `gold.fct_trial`, `gold.rag_chunks`

| column | type | description | source path(s) |
|---|---|---|---|
| `chunk_id` | VARCHAR | Citation handle | `protocolSection.identificationModule.nctId` |
| `nct_id` | VARCHAR | Trial | `protocolSection.identificationModule.nctId` |
| `section` | VARCHAR | Section of the record | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `chunk_ord` | INTEGER | Position within the section | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `chunk_text` | VARCHAR | Chunk text | `protocolSection.descriptionModule.briefSummary`<br>`protocolSection.eligibilityModule.eligibilityCriteria` |
| `overall_status` | VARCHAR | Trial status | `protocolSection.statusModule.overallStatus` |
| `status_group` | VARCHAR | Collapsed status | `protocolSection.statusModule.overallStatus` |
| `phase_label` | VARCHAR | Phase | `protocolSection.designModule.phases[*]` |
| `start_year` | BIGINT | Start year | `protocolSection.statusModule.startDateStruct.date` |
| `lead_sponsor_name` | VARCHAR | Lead sponsor display name | `protocolSection.sponsorCollaboratorsModule.collaborators[*].name`<br>`protocolSection.sponsorCollaboratorsModule.leadSponsor.name` |
