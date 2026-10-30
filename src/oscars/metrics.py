"""Business KPIs of the study: nomination rate and lift, always reported with n.

- nomination_rate = nominated / eligible
- lift            = segment nomination rate / overall nomination rate of the category

Undefined values (no eligible row, or a category with no nominee) are NaN, never a division by zero.
"""

import math

import pandas as pd


def nomination_rate(nominated: int, eligible: int) -> float:
    """Share of eligible rows that were nominated; NaN when there is no eligible row."""
    if eligible <= 0:
        return math.nan
    return nominated / eligible


def lift(segment_rate: float, overall_rate: float) -> float:
    """Segment rate relative to the category's overall rate; NaN when either rate is undefined or the base is 0."""
    if math.isnan(segment_rate) or math.isnan(overall_rate) or overall_rate == 0:
        return math.nan
    return segment_rate / overall_rate


def segment_kpis(df: pd.DataFrame, segment: str, category: str = "category", target: str = "nominated") -> pd.DataFrame:
    """One row per (category, segment value): n, nominated, nomination_rate, overall_rate, lift.

    Unused levels of a categorical segment are kept with n = 0 (rate and lift NaN), so an empty
    segment is visible instead of silently disappearing. Missing segment values form their own group.
    """
    rows = []
    for cat, g in df.groupby(category, sort=True):
        overall = nomination_rate(int(g[target].sum()), len(g))
        seg = g[segment]
        levels = list(seg.cat.categories) if isinstance(seg.dtype, pd.CategoricalDtype) else []
        levels += [v for v in seg.dropna().unique() if v not in levels]
        if seg.isna().any():
            levels.append(None)
        for level in levels:
            in_seg = seg.isna() if level is None else seg == level
            n = int(in_seg.sum())
            k = int(g.loc[in_seg, target].sum())
            rate = nomination_rate(k, n)
            rows.append(
                {
                    category: cat,
                    segment: level,
                    "n": n,
                    "nominated": k,
                    "nomination_rate": rate,
                    "overall_rate": overall,
                    "lift": lift(rate, overall),
                }
            )
    return pd.DataFrame(rows)
