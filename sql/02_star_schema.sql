-- Star schema for reporting (Power BI, Tableau, or Excel): conformed
-- dimensions and fact tables at a declared grain.

CREATE OR REPLACE TABLE dim_date AS
SELECT d::DATE                                   AS date_key,
       year(d)                                   AS year,
       month(d)                                  AS month,
       strftime(d, '%Y-%m')                      AS year_month,
       quarter(d)                                AS quarter,
       dayofweek(d)                              AS day_of_week,
       strftime(d, '%A')                         AS day_name,
       dayofweek(d) IN (0, 6)                    AS is_weekend
FROM range(DATE '2024-06-01', DATE '2026-01-01', INTERVAL 1 DAY) t(d);

CREATE OR REPLACE TABLE dim_department AS
SELECT department AS department_key, slots_per_session, slot_minutes,
       department IN ('Primary Care', 'Pediatrics') AS is_primary_care
FROM stg_departments;

CREATE OR REPLACE TABLE dim_provider AS
SELECT provider_id AS provider_key, department AS department_key, clinical_fte, hire_date
FROM stg_providers;

-- Patients are de-identified: age band and payer only, no birth date or ZIP.
CREATE OR REPLACE TABLE dim_patient AS
SELECT patient_id AS patient_key, payer,
       CASE WHEN age < 18 THEN '0-17' WHEN age < 40 THEN '18-39' WHEN age < 65 THEN '40-64' ELSE '65+' END AS age_band,
       age
FROM stg_patients;

-- Grain: one row per scheduled appointment.
-- prior_no_shows counts the patient's no-shows strictly before this
-- appointment date, so it is safe to use as a predictor (no leakage).
CREATE OR REPLACE TABLE fct_appointment AS
WITH a AS (
    SELECT a.*,
           a.appt_status = 'No Show'       AS is_no_show,
           a.appt_status = 'Canceled Late' AS is_late_cancel,
           a.appt_status = 'Completed'     AS is_completed
    FROM stg_appointments a
)
SELECT a.appt_id, a.slot_id, a.patient_id AS patient_key, a.provider_id AS provider_key,
       a.department AS department_key, a.appt_date AS date_key, a.appt_time, hour(a.appt_time) AS appt_hour,
       a.scheduled_date, date_diff('day', a.scheduled_date, a.appt_date) AS lead_days,
       a.visit_type, a.appt_status, a.is_no_show, a.is_late_cancel, a.is_completed, a.reminder_sent,
       coalesce(sum(a.is_no_show::INT) OVER (
           PARTITION BY a.patient_id ORDER BY a.appt_date
           RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING), 0) AS prior_no_shows,
       coalesce(count(*) OVER (
           PARTITION BY a.patient_id ORDER BY a.appt_date
           RANGE BETWEEN UNBOUNDED PRECEDING AND INTERVAL 1 DAY PRECEDING), 0) AS prior_appointments
FROM a;

-- Grain: one row per template slot, flagged if booked.
CREATE OR REPLACE TABLE fct_slot AS
SELECT s.slot_id, s.provider_id AS provider_key, s.department AS department_key, s.slot_date AS date_key,
       s.slot_minutes, a.appt_id IS NOT NULL AS is_booked, coalesce(a.is_completed, FALSE) AS is_completed
FROM stg_slots s LEFT JOIN fct_appointment a USING (slot_id);

-- Grain: one row per professional charge.
CREATE OR REPLACE TABLE fct_charge AS
SELECT c.charge_id, c.appt_id, c.provider_id AS provider_key, c.patient_id AS patient_key,
       p.department AS department_key, c.service_date AS date_key, c.cpt_code,
       CASE WHEN c.cpt_code IN ('99202', '99203', '99204', '99205') THEN 'new' ELSE 'established' END AS visit_category,
       c.work_rvu, c.expected_payment, c.post_date,
       date_diff('day', c.service_date, c.post_date) AS charge_lag_days
FROM stg_charges c JOIN stg_providers p USING (provider_id);
