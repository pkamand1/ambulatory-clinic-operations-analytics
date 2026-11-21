# Data dictionary

The reporting model is a star schema built by `sql/02_star_schema.sql` and exported to `data/powerbi/*.parquet` for Power BI, Tableau, or Excel. Relationships run from each fact table to the dimensions on the `*_key` columns.

## Dimensions

| Table | Grain | Columns |
| --- | --- | --- |
| `dim_date` | one row per calendar day | `date_key`, `year`, `month`, `year_month`, `quarter`, `day_of_week`, `day_name`, `is_weekend` |
| `dim_department` | one row per department | `department_key`, `slots_per_session`, `slot_minutes`, `is_primary_care` |
| `dim_provider` | one row per provider | `provider_key`, `department_key`, `clinical_fte`, `hire_date` |
| `dim_patient` | one row per patient (de-identified) | `patient_key`, `payer`, `age_band`; exact age is kept in the warehouse for modeling but left out of the export |

## Facts

### `fct_appointment`: one row per scheduled appointment

| Column | Source | Derivation |
| --- | --- | --- |
| `appt_id`, `slot_id`, `patient_key`, `provider_key`, `department_key` | appointments extract | keys as delivered |
| `date_key`, `appt_time`, `appt_hour` | appointment date and time | `appt_hour = hour(appt_time)` |
| `scheduled_date` | date the appointment was booked | as delivered |
| `lead_days` | | `appt_date − scheduled_date`, in days |
| `visit_type` | | `new` or `est` (established) |
| `appt_status` | | `Completed`, `No Show`, or `Canceled Late` (canceled within 24 hours) |
| `is_no_show`, `is_late_cancel`, `is_completed` | | indicator flags from `appt_status` |
| `reminder_sent` | | text reminder sent for this appointment |
| `prior_no_shows` | | the patient's no-shows on earlier dates only (window function), so it is safe as a predictor |
| `prior_appointments` | | the patient's appointments on earlier dates |

### `fct_slot`: one row per template slot

| Column | Derivation |
| --- | --- |
| `slot_id`, `provider_key`, `department_key`, `date_key`, `slot_minutes` | from the schedule template extract |
| `is_booked` | an appointment was scheduled into the slot |
| `is_completed` | the booked appointment was completed |

Template fill rate = booked slots / template slots.

### `fct_charge`: one row per professional charge

| Column | Derivation |
| --- | --- |
| `charge_id`, `appt_id`, `provider_key`, `patient_key`, `department_key` | keys; department comes from the rendering provider |
| `date_key` | date of service |
| `cpt_code`, `visit_category` | evaluation and management code; `new` for 99202 to 99205, `established` for 99212 to 99215 |
| `work_rvu` | work relative value units for the code |
| `expected_payment` | illustrative: 2.05 × work RVU × a conversion factor × a payer multiplier (simulated, not a fee schedule) |
| `post_date`, `charge_lag_days` | date the charge posted; `post_date − date_key` |

## Metric definitions (`sql/03_metrics.sql`)

| Metric | Definition |
| --- | --- |
| No-show rate | no-shows / scheduled appointments |
| Late cancellation rate | cancellations within 24 hours / scheduled appointments |
| Template fill rate | booked slots / template slots |
| Days to new patient visit | median and 90th percentile of `lead_days` for new patient appointments |
| Work RVUs per clinical FTE | annual work RVUs / clinical FTE, compared with the department median (an internal benchmark) |
| Charge lag | days from service to charge posting; share posted within 3 days and after 7 days |
| E/M distribution | share of each CPT level within department and visit category |
| Payer mix | completed visits by payer within department |
