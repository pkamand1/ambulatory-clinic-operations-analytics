"""Simulate a year of scheduling and billing extracts for a fictional medical group.

The tables mirror the structure of common ambulatory EHR reporting extracts
(scheduling slots, appointments with status, encounters, professional
charges with CPT codes) for six departments and 40 providers in 2025. No real
patients, providers, or organizations are represented.

Known effects are planted so the analysis can be validated:
  * No-show risk rises with scheduling lead time, Medicaid and self-pay
    coverage, early and late appointment times, and a patient-level tendency
    that shows up as prior no-shows; it falls with age.
  * A text-reminder program starts July 1 in three departments (Primary Care,
    Cardiology, Endocrinology) and lowers the log-odds of a no-show by 0.45
    for reminded appointments. The other departments are untreated controls,
    and a summer rise in no-shows affects everyone, so a simple before and
    after comparison is biased.
  * One provider (PRV117) posts charges about 12 days after the visit while
    everyone else averages about 2 days.
  * One primary care provider (PRV109) bills the highest-level established
    visit (99215) about five times as often as peers, a pattern a coding
    compliance review would look at.
  * Providers differ in how fully their templates are booked.

Writes data/raw/*.csv and data/truth.json.
"""

import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

SEED = 2025
YEAR_START, YEAR_END = date(2025, 1, 1), date(2025, 12, 31)
REMINDER_START = date(2025, 7, 1)
REMINDER_DEPTS = {"Primary Care", "Cardiology", "Endocrinology"}
REMINDER_EFFECT = -0.45
SLOW_POSTER = "PRV117"
HIGH_CODER = "PRV109"           # codes level-5 established visits far more often than peers
HIGH_CODER_EST_MIX = [0.02, 0.20, 0.43, 0.35]

DEPTS = {  # name: (providers, slots per half-day session, slot minutes, base fill rate, new-patient share)
    "Primary Care":        (12, 11, 20, 0.92, 0.12),
    "Cardiology":          (6, 9, 30, 0.88, 0.25),
    "Hematology-Oncology": (6, 8, 30, 0.90, 0.20),
    "Endocrinology":       (5, 9, 30, 0.86, 0.22),
    "Orthopedics":         (6, 12, 20, 0.90, 0.35),
    "Pediatrics":          (5, 12, 20, 0.88, 0.10),
}
CPT = {  # code: (work RVU, visit type)
    "99202": (0.93, "new"), "99203": (1.60, "new"), "99204": (2.60, "new"), "99205": (3.50, "new"),
    "99212": (0.70, "est"), "99213": (1.30, "est"), "99214": (1.92, "est"), "99215": (2.80, "est"),
}
CPT_MIX = {  # probabilities over the four codes for (primary care-like, specialty-like)
    "new": {"pc": [0.10, 0.45, 0.35, 0.10], "spec": [0.03, 0.27, 0.45, 0.25]},
    "est": {"pc": [0.08, 0.45, 0.40, 0.07], "spec": [0.04, 0.33, 0.45, 0.18]},
}
PAYERS = ["Commercial", "Medicare", "Medicaid", "Self-pay"]
PAYER_MULT = {"Commercial": 1.30, "Medicare": 1.00, "Medicaid": 0.70, "Self-pay": 0.40}
CONVERSION = 33.4   # illustrative dollars per total RVU


def main(out="data"):
    rng = np.random.default_rng(SEED)
    raw = os.path.join(out, "raw")
    os.makedirs(raw, exist_ok=True)

    # Departments and providers ------------------------------------------
    providers, pid = [], 100
    for d, (n, *_rest) in DEPTS.items():
        for _ in range(n):
            pid += 1
            providers.append({"provider_id": f"PRV{pid}", "department": d,
                              "clinical_fte": float(rng.choice([0.5, 0.6, 0.8, 0.9, 1.0], p=[.1, .15, .25, .2, .3])),
                              "hire_date": str(date(2014, 1, 1) + timedelta(days=int(rng.integers(0, 3800))))})
    prov = pd.DataFrame(providers)

    # Patients -------------------------------------------------------------
    n_pat = 26000
    age = np.clip(rng.normal(48, 21, n_pat), 0, 95).round().astype(int)
    payer = np.where(age >= 65, rng.choice(PAYERS, n_pat, p=[.15, .80, .03, .02]),
                     rng.choice(PAYERS, n_pat, p=[.62, .02, .28, .08]))
    pat = pd.DataFrame({"patient_id": [f"PT{i:06d}" for i in range(n_pat)], "age": age, "payer": payer,
                        "zip3": rng.choice(["465", "466", "490", "491", "460", "461"], n_pat)})
    u_patient = rng.normal(0, 0.9, n_pat)           # latent no-show tendency
    peds = np.flatnonzero(age < 18)
    adults = np.flatnonzero(age >= 18)

    # Slots, appointments, encounters, charges ---------------------------
    workdays = [YEAR_START + timedelta(days=i) for i in range((YEAR_END - YEAR_START).days + 1)
                if (YEAR_START + timedelta(days=i)).weekday() < 5]
    slots, appts, charges = [], [], []
    sid = aid = 0
    for p in prov.itertuples():
        n, per_session, minutes, fill, new_share = DEPTS[p.department]
        sessions_per_week = int(round(p.clinical_fte * 9))
        week_pattern = rng.choice(10, size=sessions_per_week, replace=False)   # which half-days in a week
        pool = peds if p.department == "Pediatrics" else adults
        panel = rng.choice(pool, size=1400, replace=False)
        spec = "pc" if p.department in ("Primary Care", "Pediatrics") else "spec"
        fill_p = float(np.clip(fill + rng.normal(0, 0.04), 0.70, 0.98))
        for day in workdays:
            if rng.random() < 0.06:          # provider out (vacation, CME)
                continue
            for half in (0, 1):
                if (day.weekday() * 2 + half) not in week_pattern:
                    continue
                start_min = 8 * 60 if half == 0 else 13 * 60
                for k in range(per_session):
                    sid += 1
                    t = start_min + k * minutes
                    slot_id = f"SL{sid:07d}"
                    slots.append((slot_id, p.provider_id, p.department, day.isoformat(), f"{t // 60:02d}:{t % 60:02d}", minutes))
                    summer = 1 if day.month in (6, 7, 8) else 0
                    if rng.random() > fill_p - 0.03 * summer:
                        continue
                    aid += 1
                    i = int(rng.choice(panel))
                    visit = "new" if rng.random() < new_share else "est"
                    med_lead = (25 if spec == "spec" else 9) if visit == "new" else 14
                    lead = int(min(180, rng.lognormal(np.log(med_lead), 0.8)))
                    sched = day - timedelta(days=lead)
                    hour = t // 60
                    reminded = int(p.department in REMINDER_DEPTS and day >= REMINDER_START)
                    logit = (-3.30 + 0.32 * np.log1p(lead) + 0.85 * (payer[i] == "Medicaid")
                             + 0.55 * (payer[i] == "Self-pay") - 0.010 * (age[i] - 45)
                             + 0.30 * (hour < 9 or hour >= 16) + 0.25 * summer
                             + u_patient[i] + REMINDER_EFFECT * reminded)
                    r = rng.random()
                    p_ns = 1 / (1 + np.exp(-logit))
                    if r < p_ns:
                        status = "No Show"
                    elif r < p_ns + 0.06:
                        status = "Canceled Late"
                    else:
                        status = "Completed"
                    appts.append((f"AP{aid:07d}", slot_id, pat.patient_id[i], p.provider_id, p.department,
                                  day.isoformat(), f"{t // 60:02d}:{t % 60:02d}", sched.isoformat(),
                                  visit, status, reminded))
                    if status == "Completed":
                        codes = [c for c, v in CPT.items() if v[1] == visit]
                        mix = HIGH_CODER_EST_MIX if (p.provider_id == HIGH_CODER and visit == "est") else CPT_MIX[visit][spec]
                        code = codes[int(rng.choice(4, p=mix))]
                        lag = rng.gamma(2.0, 6.0) if p.provider_id == SLOW_POSTER else rng.gamma(1.5, 1.3)
                        wrvu = CPT[code][0]
                        charges.append((f"CH{aid:07d}", f"AP{aid:07d}", p.provider_id, pat.patient_id[i], code,
                                        wrvu, round(2.05 * wrvu * CONVERSION * PAYER_MULT[payer[i]], 2),
                                        day.isoformat(), (day + timedelta(days=int(round(lag)))).isoformat()))

    pd.DataFrame(slots, columns=["slot_id", "provider_id", "department", "slot_date", "slot_time", "slot_minutes"]
                 ).to_csv(os.path.join(raw, "schedule_slots.csv"), index=False)
    pd.DataFrame(appts, columns=["appt_id", "slot_id", "patient_id", "provider_id", "department", "appt_date",
                                 "appt_time", "scheduled_date", "visit_type", "appt_status", "reminder_sent"]
                 ).to_csv(os.path.join(raw, "appointments.csv"), index=False)
    pd.DataFrame(charges, columns=["charge_id", "appt_id", "provider_id", "patient_id", "cpt_code", "work_rvu",
                                   "expected_payment", "service_date", "post_date"]
                 ).to_csv(os.path.join(raw, "charges.csv"), index=False)
    pat.to_csv(os.path.join(raw, "patients.csv"), index=False)
    prov.to_csv(os.path.join(raw, "providers.csv"), index=False)
    pd.DataFrame([{"department": d, "slots_per_session": v[1], "slot_minutes": v[2]} for d, v in DEPTS.items()]
                 ).to_csv(os.path.join(raw, "departments.csv"), index=False)
    truth = {"seed": SEED, "reminder_start": REMINDER_START.isoformat(), "reminder_departments": sorted(REMINDER_DEPTS),
             "reminder_effect_log_odds": REMINDER_EFFECT, "slow_charge_poster": SLOW_POSTER, "high_level_coder": HIGH_CODER,
             "no_show_log_odds": {"log1p_lead_days": 0.32, "medicaid": 0.85, "self_pay": 0.55,
                                  "age_per_year": -0.010, "early_or_late_slot": 0.30, "summer": 0.25}}
    json.dump(truth, open(os.path.join(out, "truth.json"), "w"), indent=2)
    print(f"slots {len(slots):,}  appointments {len(appts):,}  charges {len(charges):,}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
