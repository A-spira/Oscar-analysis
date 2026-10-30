"""Reusable data-quality checks: table profiles, guarded joins, match rates, outliers.

Every check returns plain JSON-serialisable dicts and is recorded in a `Report`, which the pipeline
prints and writes to `data/output/integration_report.json`.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

Relation = Literal["1-1", "n-1"]
_VALIDATE: dict[Relation, Literal["one_to_one", "many_to_one"]] = {"1-1": "one_to_one", "n-1": "many_to_one"}


def _py(value: Any) -> Any:
    """Convert numpy / pandas scalars to plain Python for JSON."""
    if isinstance(value, dict):
        return {str(k): _py(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_py(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    if value is pd.NA:
        return None
    return value


@dataclass
class Report:
    """Ordered collection of check results, keyed by pipeline step."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, check: str, **info: Any) -> dict[str, Any]:
        entry = _py({"step": step, "check": check, **info})
        self.entries.append(entry)
        return entry

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checks": self.entries}, indent=2, ensure_ascii=False), encoding="utf-8")


def profile_table(
    report: Report, step: str, name: str, df: pd.DataFrame, grain: str, key: list[str], na_columns: list[str]
) -> dict[str, Any]:
    """Grain, row count, key uniqueness and % NA of the columns we actually use."""
    n_dup = int(df.duplicated(key).sum())
    return report.add(
        step,
        "table",
        table=name,
        grain=grain,
        key=key,
        rows=len(df),
        key_unique=n_dup == 0,
        duplicate_keys=n_dup,
        dtypes={c: str(df[c].dtype) for c in na_columns},
        na_pct={c: round(100 * float(df[c].isna().mean()), 2) for c in na_columns},
    )


def guarded_merge(
    report: Report,
    step: str,
    name: str,
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: str | list[str],
    relation: Relation,
    left_on_notna: bool = True,
) -> pd.DataFrame:
    """Left join that must never multiply rows; records the match rate.

    `relation` is the expected left->right cardinality; pandas `validate` raises if the right key is
    not unique, and we assert the row count is unchanged. Rows whose key is NA on the left are left
    out of the match-rate denominator when `left_on_notna` is true.
    """
    keys = [on] if isinstance(on, str) else on
    rows_before = len(left)
    out = left.merge(right, on=keys, how="left", validate=_VALIDATE[relation], indicator="_merge")
    assert len(out) == rows_before, f"{name}: join multiplied rows ({rows_before} -> {len(out)})"
    eligible = out[keys].notna().all(axis=1) if left_on_notna else pd.Series(True, index=out.index)
    matched = int(((out["_merge"] == "both") & eligible).sum())
    denom = int(eligible.sum())
    report.add(
        step,
        "join",
        join=name,
        key=keys,
        relation=relation,
        rows_before=rows_before,
        rows_after=len(out),
        right_rows=len(right),
        right_key_unique=not right.duplicated(keys).any(),
        matched=matched,
        unmatched=denom - matched,
        match_rate_pct=round(100 * matched / denom, 2) if denom else None,
    )
    return out.drop(columns="_merge")


def match_rate(report: Report, step: str, name: str, matched: pd.Series) -> dict[str, Any]:
    """Share of rows with a non-null match (e.g. a tconst)."""
    n = len(matched)
    k = int(matched.notna().sum())
    return report.add(
        step,
        "match_rate",
        scope=name,
        total=n,
        matched=k,
        unmatched=n - k,
        match_rate_pct=round(100 * k / n, 2) if n else None,
    )


def outliers(
    report: Report, step: str, name: str, s: pd.Series, low: float | None = None, high: float | None = None
) -> dict[str, Any]:
    """Count values outside [low, high] and give the distribution (constat only, nothing is dropped)."""
    x = pd.to_numeric(s, errors="coerce")
    below = int((x < low).sum()) if low is not None else 0
    above = int((x > high).sum()) if high is not None else 0
    q = x.quantile([0, 0.01, 0.5, 0.99, 1]).round(2).to_dict()
    return report.add(
        step,
        "outliers",
        column=name,
        rule=f"[{low}, {high}]",
        below=below,
        above=above,
        quantiles={str(k): v for k, v in q.items()},
    )
