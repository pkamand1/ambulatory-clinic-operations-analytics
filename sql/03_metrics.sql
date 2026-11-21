-- Operational metrics as views over the star schema.

-- Access and utilization by department and month.
CREATE OR REPLACE VIEW v_access_monthly AS
SELECT s.department_key AS department, d.year_month,
       count(*)                                         AS template_slots,
       round(avg(s.is_booked::INT), 4)                  AS fill_rate,
       round(avg(s.is_completed::INT), 4)               AS completed_slot_rate
FROM fct_slot s JOIN dim_date d USING (date_key)
GROUP BY ALL;

CREATE OR REPLACE VIEW v_new_patient_lag AS
SELECT department_key AS department, strftime(date_key, '%Y-%m') AS year_month,
       count(*)                                          AS new_patient_appointments,
       median(lead_days)                                 AS median_days_to_new_patient_visit,
       quantile_cont(lead_days, 0.9)                     AS p90_days_to_new_patient_visit
FROM fct_appointment WHERE visit_type = 'new'
GROUP BY ALL;

-- No-shows and late cancellations by department and month.
CREATE OR REPLACE VIEW v_no_show_monthly AS
SELECT department_key AS department, strftime(date_key, '%Y-%m') AS year_month,
       count(*)                                 AS appointments,
       round(avg(is_no_show::INT), 4)           AS no_show_rate,
       round(avg(is_late_cancel::INT), 4)       AS late_cancel_rate,
       round(avg(reminder_sent::INT), 4)        AS reminder_rate
FROM fct_appointment GROUP BY ALL;

-- Provider productivity: work RVUs annualized per 1.0 clinical FTE, compared
-- with the department median (an internal benchmark).
CREATE OR REPLACE VIEW v_provider_productivity AS
WITH p AS (
    SELECT c.provider_key, pr.department_key, pr.clinical_fte,
           count(*)                       AS visits,
           sum(c.work_rvu)                AS work_rvus,
           sum(c.expected_payment)        AS expected_payments
    FROM fct_charge c JOIN dim_provider pr USING (provider_key)
    GROUP BY ALL
)
SELECT *, round(work_rvus / clinical_fte, 0)                                       AS wrvu_per_fte,
       round(work_rvus / clinical_fte / median(work_rvus / clinical_fte) OVER (PARTITION BY department_key), 3) AS vs_department_median,
       round(work_rvus / visits, 3)                                                 AS wrvu_per_visit
FROM p;

-- Revenue cycle: charge lag by provider.
CREATE OR REPLACE VIEW v_charge_lag AS
SELECT provider_key, department_key,
       count(*)                                          AS charges,
       round(avg(charge_lag_days), 1)                    AS mean_lag_days,
       median(charge_lag_days)                           AS median_lag_days,
       round(avg((charge_lag_days <= 3)::INT), 4)        AS share_posted_within_3_days,
       round(avg((charge_lag_days > 7)::INT), 4)         AS share_posted_after_7_days
FROM fct_charge GROUP BY ALL;

-- Coding distribution: share of each E/M level by department and visit category.
CREATE OR REPLACE VIEW v_em_distribution AS
SELECT department_key AS department, visit_category, cpt_code, count(*) AS charges,
       round(count(*) / sum(count(*)) OVER (PARTITION BY department_key, visit_category), 4) AS share
FROM fct_charge GROUP BY department_key, visit_category, cpt_code;

-- Payer mix by department.
CREATE OR REPLACE VIEW v_payer_mix AS
SELECT a.department_key AS department, p.payer, count(*) AS completed_visits,
       round(count(*) / sum(count(*)) OVER (PARTITION BY a.department_key), 4) AS share
FROM fct_appointment a JOIN dim_patient p USING (patient_key)
WHERE a.is_completed GROUP BY a.department_key, p.payer;
