"""Build the DuckDB warehouse from the raw extract, run data quality checks,
and export the star schema as Parquet for Power BI or Tableau.

Usage: python src/build_warehouse.py [--data data]
"""

import argparse
import os

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHECKS = {
    "appointment ids unique": "select count(*) - count(distinct appt_id) from fct_appointment",
    "each slot booked at most once": "select count(*) from (select slot_id from fct_appointment group by 1 having count(*) > 1)",
    "appointments without a slot": "select count(*) from fct_appointment a left join stg_slots s using (slot_id) where s.slot_id is null",
    "scheduled after the appointment date": "select count(*) from fct_appointment where lead_days < 0",
    "charges for visits that were not completed": "select count(*) from fct_charge c join fct_appointment a using (appt_id) where not a.is_completed",
    "completed visits without a charge": "select count(*) from fct_appointment a left join fct_charge c using (appt_id) where a.is_completed and c.charge_id is null",
    "charges posted before the service date": "select count(*) from fct_charge where charge_lag_days < 0",
    "patients in facts missing from dimension": "select count(*) from fct_appointment a left join dim_patient p using (patient_key) where p.patient_key is null",
    "pediatric visits by adults (age 18 or older)": "select count(*) from fct_appointment a join dim_patient p using (patient_key) where a.department_key = 'Pediatrics' and p.age >= 18",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    raw = os.path.abspath(os.path.join(a.data, "raw"))
    db = os.path.join(a.data, "clinic.duckdb")
    if os.path.exists(db):
        os.remove(db)
    con = duckdb.connect(db)
    for f in ("01_staging.sql", "02_star_schema.sql", "03_metrics.sql"):
        con.execute(open(os.path.join(ROOT, "sql", f)).read().replace("{raw}", raw))
    rows = [{"check": k, "failing_rows": con.execute(q).fetchone()[0]} for k, q in CHECKS.items()]
    dq = pd.DataFrame(rows)
    dq["status"] = dq.failing_rows.map(lambda n: "pass" if n == 0 else "fail")
    os.makedirs(os.path.join(ROOT, "results", "tables"), exist_ok=True)
    dq.to_csv(os.path.join(ROOT, "results", "tables", "data_quality_checks.csv"), index=False)
    exp = os.path.join(a.data, "powerbi")
    os.makedirs(exp, exist_ok=True)
    for t in ("dim_date", "dim_department", "dim_provider", "dim_patient", "fct_appointment", "fct_slot", "fct_charge"):
        cols = "* exclude (age)" if t == "dim_patient" else "*"
        con.execute(f"copy (select {cols} from {t}) to '{exp}/{t}.parquet' (format parquet, compression zstd)")
    print(dq.to_string(index=False))
    if (dq.status == "fail").any():
        raise SystemExit("data quality check failed")


if __name__ == "__main__":
    main()
