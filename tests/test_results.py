"""The pipeline must recover what the simulation planted (data/truth.json)."""

import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAB = os.path.join(ROOT, "results", "tables")
truth = json.load(open(os.path.join(ROOT, "data", "truth.json")))


def test_all_data_quality_checks_pass():
    assert (pd.read_csv(os.path.join(TAB, "data_quality_checks.csv")).status == "pass").all()


def test_reminder_effect_detected():
    did = pd.read_csv(os.path.join(TAB, "reminder_program_did.csv")).set_index("estimate")
    row = did.loc["DiD log-odds ratio, fixed effects + patient covariates"]
    assert row.ci_high < 0                                  # a real reduction
    assert row.ci_low <= truth["reminder_effect_log_odds"] <= row.ci_high


def test_flags_only_the_planted_providers():
    lag = pd.read_csv(os.path.join(TAB, "charge_lag_by_provider.csv"))
    em = pd.read_csv(os.path.join(TAB, "em_level5_by_provider.csv"))
    assert set(lag[lag.flag].provider_key) == {truth["slow_charge_poster"]}
    assert set(em[em.flag].provider) == {truth["high_level_coder"]}


def test_no_show_drivers_recovered():
    ors = pd.read_csv(os.path.join(TAB, "no_show_odds_ratios.csv")).set_index("term")
    t = truth["no_show_log_odds"]
    assert abs(np.log(ors.loc["log_lead", "odds_ratio"]) - t["log1p_lead_days"]) < 0.06
    assert abs(np.log(ors.loc["age10", "odds_ratio"]) - 10 * t["age_per_year"]) < 0.03
