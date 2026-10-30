"""KPI tests: nomination rate and lift on hand-computable toy data."""

import math

import pandas as pd
import pytest

from oscars.metrics import lift, nomination_rate, segment_kpis


def _toy() -> pd.DataFrame:
    # Category A: 10 eligible films, 3 nominated (overall 0.3).
    #   Q4 releases: 4 films, 2 nominated -> rate 0.5,  lift 0.5 / 0.3
    #   other:       6 films, 1 nominated -> rate 1/6,  lift (1/6) / 0.3
    # Category B: 2 eligible films, none nominated (overall 0).
    return pd.DataFrame(
        {
            "category": ["A"] * 10 + ["B"] * 2,
            "release": pd.Categorical(
                ["q4"] * 4 + ["other"] * 6 + ["q4", "other"], categories=["q4", "other", "summer"]
            ),
            "nominated": [True, True, False, False, True, False, False, False, False, False, False, False],
        }
    )


def _kpi(kpis: pd.DataFrame, segment: str, category: str, level: str) -> dict[str, float]:
    """The numeric KPIs of one (category, segment level) row, as floats."""
    row = kpis[(kpis["category"] == category) & (kpis[segment] == level)]
    assert len(row) == 1
    return {str(k): float(v) for k, v in row.iloc[0].drop(["category", segment]).items()}


def test_segment_kpis_rate_and_lift_match_hand_computation() -> None:
    """The nomination rate is nominated / eligible and the lift is segment rate / category rate."""
    kpis = segment_kpis(_toy(), "release")

    q4 = _kpi(kpis, "release", "A", "q4")
    assert (q4["n"], q4["nominated"]) == (4, 2)
    assert q4["nomination_rate"] == pytest.approx(0.5)
    assert q4["overall_rate"] == pytest.approx(0.3)
    assert q4["lift"] == pytest.approx(0.5 / 0.3)

    other = _kpi(kpis, "release", "A", "other")
    assert other["nomination_rate"] == pytest.approx(1 / 6)
    assert other["lift"] == pytest.approx((1 / 6) / 0.3)


def test_empty_segment_and_zero_base_give_nan_not_division_error() -> None:
    """An empty segment (n = 0) or a category without nominee yields NaN instead of a division by zero."""
    kpis = segment_kpis(_toy(), "release")

    summer = _kpi(kpis, "release", "A", "summer")  # unused categorical level kept with n = 0
    assert summer["n"] == 0
    assert math.isnan(summer["nomination_rate"])
    assert math.isnan(summer["lift"])

    b_q4 = _kpi(kpis, "release", "B", "q4")  # rate 0 / base 0 -> lift undefined
    assert b_q4["nomination_rate"] == 0
    assert math.isnan(b_q4["lift"])

    assert math.isnan(nomination_rate(0, 0))
    assert math.isnan(lift(0.2, 0.0))
    assert math.isnan(lift(math.nan, 0.3))


def test_missing_segment_values_form_their_own_group() -> None:
    """Rows with an unknown segment (e.g. budget unknown) are counted in an explicit NA group, not dropped."""
    df = pd.DataFrame(
        {"category": ["A"] * 4, "budget_bucket": ["<5M", None, None, "<5M"], "nominated": [True, False, True, False]}
    )
    kpis = segment_kpis(df, "budget_bucket")

    assert int(kpis["n"].sum()) == len(df)
    na_rows = kpis[kpis["budget_bucket"].isna()]
    assert len(na_rows) == 1
    assert (int(na_rows["n"].iloc[0]), int(na_rows["nominated"].iloc[0])) == (2, 1)
