import math

import numpy as np
import pandas as pd
import pytest

from sectorlab.config import SECTORS
from sectorlab.factors import (
    classify, combine_signals, robust_value_score, valuation_scores,
)


def test_cheap_pe_scores_higher_than_expensive_pe():
    history = pd.Series(np.linspace(12, 28, 60))
    assert robust_value_score(13, history, higher_is_cheap=False) > 50
    assert robust_value_score(27, history, higher_is_cheap=False) < 50


def test_high_fcf_yield_scores_higher():
    history = pd.Series(np.linspace(.02, .08, 60))
    assert robust_value_score(.075, history, higher_is_cheap=True) > 50


def test_no_history_returns_nan():
    assert math.isnan(robust_value_score(10, [8, 9, 11], higher_is_cheap=False))


def test_zero_mad_returns_nan():
    assert math.isnan(robust_value_score(10, [10] * 60, higher_is_cheap=False))


def test_no_lookahead_valuation_score():
    # Vary data AFTER the as-of date: the resulting score must not change.
    months = pd.date_range("2018-01-31", periods=75, freq="BME")
    data = pd.DataFrame({"date": months, "ticker": "XLK", "metric": "forward_pe",
                         "value": np.linspace(20, 35, len(months)), "available_at": months})
    future = pd.DataFrame([{"date": pd.Timestamp("2030-01-31"), "ticker": "XLK",
                            "metric": "forward_pe", "value": 10**7,
                            "available_at": pd.Timestamp("2030-01-31")}])
    cutoff = months[-1]
    a = valuation_scores(data, cutoff)
    b = valuation_scores(pd.concat([data, future], ignore_index=True), cutoff)
    pd.testing.assert_frame_equal(a, b)


def test_incomplete_pillar_blocks_composite():
    tickers = list(SECTORS)
    v = pd.DataFrame({"ticker": tickers, "valuation": [70.] * len(tickers)})
    m = pd.DataFrame({"ticker": tickers, "momentum": [60.] * len(tickers)})
    e = pd.DataFrame({"ticker": tickers, "revision": [80.] * len(tickers)})
    q = pd.DataFrame({"ticker": tickers, "quality": [75.] * len(tickers)})
    e.loc[0, "revision"] = np.nan
    result = combine_signals(v, m, e, q, signal_date="2026-10-30", provenance="TEST")
    assert len(result) == 11
    missing = result.loc[result.ticker == tickers[0]].iloc[0]
    assert pd.isna(missing.opportunity)
    assert missing.classification == "INSUFFICIENT_DATA"
    assert missing.data_status == "INCOMPLETE"


def test_composite_weights():
    v = pd.DataFrame({"ticker": ["XLK"], "valuation": [80.]})
    m = pd.DataFrame({"ticker": ["XLK"], "momentum": [60.]})
    e = pd.DataFrame({"ticker": ["XLK"], "revision": [70.]})
    q = pd.DataFrame({"ticker": ["XLK"], "quality": [90.]})
    row = combine_signals(v, m, e, q, signal_date="2026-10-30", provenance="TEST")
    xlk = row.loc[row.ticker == "XLK"].iloc[0]
    assert xlk.opportunity == pytest.approx(.40*80 + .25*60 + .20*70 + .15*90)
    assert xlk.rotation == pytest.approx(.55*60 + .45*70)


def test_interpretation_rules():
    assert classify(70, 70) == "VALUATION_SUPPORTED_ROTATION"
    assert classify(70, 30) == "CHEAP_BUT_WEAK"
    assert classify(25, 70) == "EXPENSIVE_LEADERSHIP"
    assert classify(25, 30) == "EXPENSIVE_AND_WEAK"
