-- Staging: load the raw extract with explicit types, one table per source file.
CREATE OR REPLACE TABLE stg_departments AS
SELECT * FROM read_csv('{raw}/departments.csv', header = true);

CREATE OR REPLACE TABLE stg_providers AS
SELECT provider_id, department, CAST(clinical_fte AS DECIMAL(3, 2)) AS clinical_fte, CAST(hire_date AS DATE) AS hire_date
FROM read_csv('{raw}/providers.csv', header = true);

CREATE OR REPLACE TABLE stg_patients AS
SELECT patient_id, CAST(age AS INTEGER) AS age, payer, CAST(zip3 AS VARCHAR) AS zip3
FROM read_csv('{raw}/patients.csv', header = true, types = {'zip3': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_slots AS
SELECT slot_id, provider_id, department, CAST(slot_date AS DATE) AS slot_date,
       CAST(slot_time AS TIME) AS slot_time, slot_minutes
FROM read_csv('{raw}/schedule_slots.csv', header = true, types = {'slot_time': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_appointments AS
SELECT appt_id, slot_id, patient_id, provider_id, department,
       CAST(appt_date AS DATE) AS appt_date, CAST(appt_time AS TIME) AS appt_time,
       CAST(scheduled_date AS DATE) AS scheduled_date, visit_type, appt_status,
       CAST(reminder_sent AS BOOLEAN) AS reminder_sent
FROM read_csv('{raw}/appointments.csv', header = true, types = {'appt_time': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_charges AS
SELECT charge_id, appt_id, provider_id, patient_id, CAST(cpt_code AS VARCHAR) AS cpt_code,
       CAST(work_rvu AS DECIMAL(5, 2)) AS work_rvu, CAST(expected_payment AS DECIMAL(10, 2)) AS expected_payment,
       CAST(service_date AS DATE) AS service_date, CAST(post_date AS DATE) AS post_date
FROM read_csv('{raw}/charges.csv', header = true, types = {'cpt_code': 'VARCHAR'});
