"""Business KPIs of the study: nomination rate and lift, always reported with n.

- nomination_rate = nominated / eligible
- lift            = segment nomination rate / overall nomination rate of the category
- Wilson 95 % confidence interval for each rate (well-behaved for small n and rates near 0)

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


def wilson_interval(nominated: int, eligible: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval (95 % by default) for nominated / eligible; (NaN, NaN) when eligible is 0."""
    if eligible <= 0:
        return math.nan, math.nan
    p = nominated / eligible
    denom = 1 + z**2 / eligible
    centre = (p + z**2 / (2 * eligible)) / denom
    half = z * math.sqrt(p * (1 - p) / eligible + z**2 / (4 * eligible**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def segment_kpis(df: pd.DataFrame, segment: str, category: str = "category", target: str = "nominated") -> pd.DataFrame:
    """One row per (category, segment value): n, nominated, nomination_rate, ci_low, ci_high, overall_rate, lift.

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
            ci_low, ci_high = wilson_interval(k, n)
            rows.append(
                {
                    category: cat,
                    segment: level,
                    "n": n,
                    "nominated": k,
                    "nomination_rate": rate,
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "overall_rate": overall,
                    "lift": lift(rate, overall),
                }
            )
    return pd.DataFrame(rows)
