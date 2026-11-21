"""Analyses for medical group operations leaders.

  1. Did the text-reminder program reduce no-shows? (difference-in-differences)
  2. Which appointments are at high risk of a no-show? (explainable model,
     validated on later months)
  3. Which providers need follow-up on charge lag or coding patterns?
  4. A provider scorecard for Excel.

Usage: python src/analysis.py [--db data/clinic.duckdb]
"""

import argparse
import json
import os

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, brier_score_loss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAB, FIG = os.path.join(ROOT, "results", "tables"), os.path.join(ROOT, "results", "figures")
TREATED = ["Primary Care", "Cardiology", "Endocrinology"]


def appointments(con):
    return con.sql("""
        select a.*, p.payer, p.age, p.age_band, month(a.date_key) as month
        from fct_appointment a join dim_patient p using (patient_key)
    """).df()


def reminder_did(a):
    a = a.copy()
    a["treated"] = a.department_key.isin(TREATED).astype(int)
    a["post"] = (a.date_key >= pd.Timestamp("2025-07-01")).astype(int)
    a["y"] = a.is_no_show.astype(int)
    rates = a.groupby(["treated", "post"]).y.mean().unstack()
    naive = a[a.treated == 1].groupby("post").y.mean()
    # Logistic difference-in-differences with department and calendar-month fixed effects,
    # standard errors clustered by patient.
    m = smf.logit("y ~ treated:post + C(department_key) + C(month)", data=a).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(a.patient_key)[0]})
    b, ci = m.params["treated:post"], m.conf_int().loc["treated:post"]
    # The same model with patient-level covariates.
    m2 = smf.logit("y ~ treated:post + C(department_key) + C(month) + np.log1p(lead_days) + C(payer) + age",
                   data=a).fit(disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(a.patient_key)[0]})
    b2, ci2 = m2.params["treated:post"], m2.conf_int().loc["treated:post"]
    did_rd = (rates.loc[1, 1] - rates.loc[1, 0]) - (rates.loc[0, 1] - rates.loc[0, 0])
    out = pd.DataFrame([
        {"estimate": "no-show rate, reminder departments, Jan to Jun", "value": round(rates.loc[1, 0], 4)},
        {"estimate": "no-show rate, reminder departments, Jul to Dec", "value": round(rates.loc[1, 1], 4)},
        {"estimate": "no-show rate, other departments, Jan to Jun", "value": round(rates.loc[0, 0], 4)},
        {"estimate": "no-show rate, other departments, Jul to Dec", "value": round(rates.loc[0, 1], 4)},
        {"estimate": "naive before-after change, reminder departments (points)", "value": round(naive[1] - naive[0], 4)},
        {"estimate": "difference-in-differences (percentage points)", "value": round(did_rd, 4)},
        {"estimate": "DiD log-odds ratio, fixed effects", "value": round(b, 3), "ci_low": round(ci[0], 3), "ci_high": round(ci[1], 3)},
        {"estimate": "DiD log-odds ratio, fixed effects + patient covariates", "value": round(b2, 3), "ci_low": round(ci2[0], 3), "ci_high": round(ci2[1], 3)},
        {"estimate": "true effect in the simulation (log-odds, conditional on patient)", "value": -0.45},
    ])
    out.to_csv(os.path.join(TAB, "reminder_program_did.csv"), index=False)

    monthly = a.groupby(["treated", "month"]).y.mean().unstack(0)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(monthly.index, monthly[1], "o-", label="reminder departments")
    ax.plot(monthly.index, monthly[0], "s-", label="other departments")
    ax.axvline(6.5, ls=":", color="k")
    ax.text(6.6, ax.get_ylim()[1] * 0.98, "reminders start", va="top", fontsize=8)
    ax.set_xticks(range(1, 13), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_ylabel("No-show rate")
    ax.set_title(f"Text reminders: difference-in-differences {did_rd * 100:+.1f} points")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "reminder_did.png"), dpi=150)
    plt.close(fig)
    return out


def no_show_model(a):
    a = a[a.appt_status != "Canceled Late"].copy()    # late cancellations are neither shows nor no-shows
    a["y"] = a.is_no_show.astype(int)
    a["log_lead"] = np.log1p(a.lead_days)
    a["early_or_late"] = ((a.appt_hour < 9) | (a.appt_hour >= 16)).astype(int)
    a["prior_ns"] = a.prior_no_shows.clip(upper=3)
    a["first_visit_in_data"] = (a.prior_appointments == 0).astype(int)
    a["reminder"] = a.reminder_sent.astype(int)
    a["age10"] = a.age / 10
    train, test = a[a.month <= 9], a[a.month >= 10]
    f = ("y ~ log_lead + C(payer, Treatment('Commercial')) + age10 + early_or_late + C(prior_ns) "
         "+ first_visit_in_data + C(visit_type) + reminder + C(department_key)")
    m = smf.logit(f, data=train).fit(disp=0)
    p_test = m.predict(test)
    ors = pd.DataFrame({"odds_ratio": np.exp(m.params), "ci_low": np.exp(m.conf_int()[0]),
                        "ci_high": np.exp(m.conf_int()[1]), "p_value": m.pvalues}).round(4)
    ors = ors.drop(index="Intercept")
    ors.index.name = "term"
    ors.to_csv(os.path.join(TAB, "no_show_odds_ratios.csv"))

    feats = ["log_lead", "age10", "early_or_late", "prior_ns", "first_visit_in_data", "reminder"]
    X = pd.get_dummies(a[feats + ["payer", "visit_type", "department_key"]], drop_first=True).astype(float)
    gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, random_state=0)
    gb.fit(X.loc[train.index], train.y)
    p_gb = gb.predict_proba(X.loc[test.index])[:, 1]

    # Operational use: call the highest-risk 10% of appointments.
    k = int(0.1 * len(test))
    top = np.argsort(-p_test.values)[:k]
    captured = test.y.values[top].sum() / test.y.sum()
    perf = pd.DataFrame([
        {"model": "logistic regression (explainable)", "AUC": round(roc_auc_score(test.y, p_test), 4),
         "Brier": round(brier_score_loss(test.y, p_test), 4),
         "no_shows_in_top_10pct_risk": round(captured, 4), "lift_top_decile": round(captured / 0.10, 2)},
        {"model": "gradient boosting (black box)", "AUC": round(roc_auc_score(test.y, p_gb), 4),
         "Brier": round(brier_score_loss(test.y, p_gb), 4),
         "no_shows_in_top_10pct_risk": round(test.y.values[np.argsort(-p_gb)[:k]].sum() / test.y.sum(), 4),
         "lift_top_decile": round(test.y.values[np.argsort(-p_gb)[:k]].sum() / test.y.sum() / 0.10, 2)},
    ])
    perf.to_csv(os.path.join(TAB, "no_show_model_performance.csv"), index=False)

    dec = pd.qcut(p_test, 10, labels=False)
    cal = pd.DataFrame({"predicted": p_test, "observed": test.y, "decile": dec}).groupby("decile").mean()
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot(cal.predicted, cal.observed, "o-")
    lim = max(cal.max()) * 1.05
    ax.plot([0, lim], [0, lim], ":", color="k")
    ax.set_xlabel("Predicted no-show probability (decile mean)")
    ax.set_ylabel("Observed no-show rate")
    ax.set_title(f"Calibration, Oct to Dec (AUC {perf.AUC[0]:.3f})")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "no_show_calibration.png"), dpi=150)
    plt.close(fig)
    return ors, perf


def provider_flags(con):
    lag = con.sql("select * from v_charge_lag order by mean_lag_days desc").df()
    em = con.sql("""
        select provider_key, department_key,
               count(*) filter (where cpt_code = '99215') as level5, count(*) as est_visits
        from fct_charge where visit_category = 'established' group by all
    """).df()
    rows = []
    for _, r in em.iterrows():
        peers = em[(em.department_key == r.department_key) & (em.provider_key != r.provider_key)]
        p0 = peers.level5.sum() / peers.est_visits.sum()
        pval = stats.binomtest(int(r.level5), int(r.est_visits), p0, alternative="greater").pvalue
        rows.append({"provider": r.provider_key, "department": r.department_key,
                     "level5_share": round(r.level5 / r.est_visits, 4), "peer_level5_share": round(p0, 4),
                     "ratio_to_peers": round(r.level5 / r.est_visits / p0, 2), "p_value": pval})
    em = pd.DataFrame(rows)
    em["flag"] = (em.p_value < 0.05 / len(em)) & (em.ratio_to_peers > 1.5)   # Bonferroni across providers
    em.sort_values("ratio_to_peers", ascending=False).to_csv(os.path.join(TAB, "em_level5_by_provider.csv"), index=False)
    lag["flag"] = lag.share_posted_after_7_days > 0.10
    lag.to_csv(os.path.join(TAB, "charge_lag_by_provider.csv"), index=False)

    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    l2 = lag.sort_values("mean_lag_days")
    ax[0].barh(l2.provider_key, l2.mean_lag_days, color=np.where(l2.flag, "#d62728", "#7f7f7f"))
    ax[0].set_xlabel("Mean days from visit to charge posting")
    ax[0].set_title("Charge lag by provider")
    ax[0].tick_params(axis="y", labelsize=5)
    e2 = em.sort_values("ratio_to_peers")
    ax[1].barh(e2.provider, e2.ratio_to_peers, color=np.where(e2.flag, "#d62728", "#7f7f7f"))
    ax[1].axvline(1, color="k", lw=0.8)
    ax[1].set_xlabel("99215 share relative to department peers")
    ax[1].set_title("Level-5 established visits")
    ax[1].tick_params(axis="y", labelsize=5)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "provider_flags.png"), dpi=150)
    plt.close(fig)
    return lag, em


def scorecard(con, lag, em):
    prod = con.sql("select * from v_provider_productivity").df()
    fill = con.sql("""select provider_key, round(avg(is_booked::int), 4) as template_fill_rate
                      from fct_slot group by 1""").df()
    ns = con.sql("""select provider_key, round(avg(is_no_show::int), 4) as no_show_rate
                    from fct_appointment group by 1""").df()
    sc = (prod.merge(fill, on="provider_key").merge(ns, on="provider_key")
          .merge(lag[["provider_key", "mean_lag_days", "share_posted_after_7_days"]], on="provider_key")
          .merge(em[["provider", "level5_share", "ratio_to_peers"]].rename(columns={"provider": "provider_key"}),
                 on="provider_key"))
    sc = sc.sort_values(["department_key", "provider_key"])
    path = os.path.join(ROOT, "results", "provider_scorecard.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        sc.to_excel(xw, sheet_name="Provider scorecard", index=False)
        con.sql("select * from v_no_show_monthly order by department, year_month").df().to_excel(
            xw, sheet_name="No-shows by month", index=False)
        con.sql("select * from v_access_monthly order by department, year_month").df().to_excel(
            xw, sheet_name="Template fill by month", index=False)
        ws = xw.sheets["Provider scorecard"]
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(12, len(str(col[0].value)) + 2)
    sc.to_csv(os.path.join(TAB, "provider_scorecard.csv"), index=False)
    return sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "clinic.duckdb"))
    a = ap.parse_args()
    os.makedirs(TAB, exist_ok=True)
    os.makedirs(FIG, exist_ok=True)
    con = duckdb.connect(a.db, read_only=True)
    appts = appointments(con)
    did = reminder_did(appts)
    ors, perf = no_show_model(appts)
    lag, em = provider_flags(con)
    scorecard(con, lag, em)
    print(did.to_string(index=False))
    print(perf.to_string(index=False))
    print(ors.to_string())
    print(lag[lag.flag].to_string(index=False))
    print(em[em.flag].to_string(index=False))


if __name__ == "__main__":
    main()
