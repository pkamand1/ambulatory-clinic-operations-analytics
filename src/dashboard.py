"""Build a self-contained interactive operations dashboard (HTML, Plotly).

The same star schema exported to data/powerbi/ feeds Power BI or Tableau;
this file gives a version anyone can open in a browser.

Usage: python src/dashboard.py [--db data/clinic.duckdb]
"""

import argparse
import os

import duckdb
import plotly.express as px
import plotly.io as pio
from plotly.offline import get_plotlyjs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPT = ["Primary Care", "Pediatrics", "Cardiology", "Endocrinology", "Hematology-Oncology", "Orthopedics"]
DEPT_COLORS = dict(zip(DEPT, ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]))
PAYER_ORDER = ["Commercial", "Medicare", "Medicaid", "Self-pay"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "clinic.duckdb"))
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    q = lambda s: con.sql(s).df()

    kpi = q("""select count(*) as appts, avg(is_no_show::int) as ns, avg(is_completed::int) as comp
               from fct_appointment""").iloc[0]
    rvu = q("select sum(work_rvu) as w, sum(expected_payment) as pay from fct_charge").iloc[0]
    fill = q("select avg(is_booked::int) as f from fct_slot").iloc[0].f

    figs = []
    ns = q("select * from v_no_show_monthly order by year_month")
    figs.append(px.line(ns, x="year_month", y="no_show_rate", color="department", markers=True, color_discrete_map=DEPT_COLORS, category_orders={"department": DEPT},
                        title="No-show rate by month (reminders began July)"))
    acc = q("select * from v_access_monthly order by year_month")
    figs.append(px.line(acc, x="year_month", y="fill_rate", color="department", markers=True, color_discrete_map=DEPT_COLORS, category_orders={"department": DEPT},
                        title="Template fill rate (booked slots / available slots)"))
    lag = q("select * from v_new_patient_lag order by year_month")
    figs.append(px.line(lag, x="year_month", y="median_days_to_new_patient_visit", color="department", markers=True, color_discrete_map=DEPT_COLORS, category_orders={"department": DEPT},
                        title="Median days from scheduling to new patient visit"))
    prod = q("select * from v_provider_productivity order by department_key, wrvu_per_fte")
    figs.append(px.bar(prod, x="provider_key", y="vs_department_median", color="department_key", color_discrete_map=DEPT_COLORS, category_orders={"department_key": DEPT},
                       hover_data=["clinical_fte", "visits", "work_rvus", "wrvu_per_fte"],
                       title="Work RVUs per clinical FTE relative to department median"))
    cl = q("select * from v_charge_lag order by mean_lag_days desc")
    figs.append(px.bar(cl, x="provider_key", y="share_posted_after_7_days", color="department_key", color_discrete_map=DEPT_COLORS, category_orders={"department_key": DEPT},
                       title="Share of charges posted more than 7 days after the visit"))
    pm = q("select * from v_payer_mix")
    figs.append(px.bar(pm, x="department", y="share", color="payer", category_orders={"payer": PAYER_ORDER, "department": DEPT}, title="Payer mix of completed visits"))
    names = {"year_month": "Month", "no_show_rate": "No-show rate", "fill_rate": "Fill rate",
             "median_days_to_new_patient_visit": "Median days", "provider_key": "Provider",
             "vs_department_median": "Ratio to median", "share_posted_after_7_days": "Share after 7 days",
             "department": "Department", "share": "Share", "department_key": "Department"}
    for f in figs:
        f.for_each_xaxis(lambda ax: ax.update(title_text=names.get(ax.title.text, ax.title.text)))
        f.for_each_yaxis(lambda ax: ax.update(title_text=names.get(ax.title.text, ax.title.text)))
        f.update_layout(height=380, margin=dict(l=40, r=20, t=60, b=40), legend_title_text="", template="plotly_white")

    cards = "".join(f'<div class="card"><div class="v">{v}</div><div class="l">{l}</div></div>' for v, l in [
        (f"{int(kpi.appts):,}", "appointments, 2025"), (f"{kpi.ns:.1%}", "no-show rate"),
        (f"{fill:.1%}", "template fill rate"), (f"{rvu.w:,.0f}", "work RVUs"),
        (f"${rvu.pay / 1e6:.1f}M", "expected professional payments")])
    # The Plotly library is inlined in the page head so the file works offline.
    parts = [pio.to_html(f, full_html=False, include_plotlyjs=False, default_width="100%", config={"responsive": True}) for f in figs]
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Medical group operations, 2025</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>{get_plotlyjs()}</script>
<style>body{{font-family:system-ui,sans-serif;margin:24px;background:#fafafa;color:#222}}
h1{{font-size:22px}} .cards{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0 20px}}
.card{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;min-width:150px}}
.v{{font-size:22px;font-weight:600}} .l{{font-size:12px;color:#666}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:12px}}
.grid>div{{background:#fff;border:1px solid #ddd;border-radius:8px}} p{{font-size:12px;color:#666}}</style></head>
<body><h1>Medical group operations, 2025 (simulated data)</h1><div class="cards">{cards}</div>
<div class="grid">{''.join(f'<div>{p}</div>' for p in parts)}</div>
<p>All data are simulated. Built from the star schema in this repository by src/dashboard.py.</p>
<script>window.addEventListener("load", () => document.querySelectorAll(".js-plotly-plot").forEach(d => Plotly.Plots.resize(d)));</script></body></html>"""
    os.makedirs(os.path.join(ROOT, "dashboard"), exist_ok=True)
    out = os.path.join(ROOT, "dashboard", "clinic_operations_dashboard.html")
    open(out, "w").write(html)
    print("wrote", out, f"{len(html) / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
