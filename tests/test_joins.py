"""Join tests: the guarded merge must keep the grain of the left table."""

import pandas as pd
import pytest

from oscars.checks import Report, guarded_merge


def _nominations() -> pd.DataFrame:
    # Grain: 1 nomination. Two nominations share a film, one is unmatched (tconst NA).
    return pd.DataFrame(
        {
            "category": ["Best Picture", "Best Directing", "Best Picture"],
            "tconst": ["tt1", "tt1", None],
        }
    )


def test_many_to_one_join_keeps_row_count_and_reports_match_rate() -> None:
    """Enriching nominations with a film table (unique tconst) keeps 1 row per nomination."""
    films = pd.DataFrame({"tconst": ["tt1", "tt2"], "title": ["Babel", "Minari"]})
    report = Report()

    out = guarded_merge(report, "test", "nominations <- films", _nominations(), films, "tconst", "n-1")

    assert len(out) == 3
    assert out["title"].tolist()[:2] == ["Babel", "Babel"]
    entry = report.entries[-1]
    assert (entry["rows_before"], entry["rows_after"]) == (3, 3)
    # The NA key is excluded from the denominator: 2 matchable rows, 2 matched.
    assert (entry["matched"], entry["unmatched"], entry["match_rate_pct"]) == (2, 0, 100.0)


def test_duplicated_right_key_is_rejected_instead_of_multiplying_rows() -> None:
    """A right table with a duplicated key would multiply rows, so the guarded merge must raise."""
    films = pd.DataFrame({"tconst": ["tt1", "tt1"], "title": ["Babel", "Babel (re-release)"]})

    with pytest.raises(pd.errors.MergeError):
        guarded_merge(Report(), "test", "nominations <- films", _nominations(), films, "tconst", "n-1")


def test_one_to_one_join_rejects_duplicated_left_key() -> None:
    """A join declared 1-1 must fail when the left key is not unique either."""
    left = pd.DataFrame({"tconst": ["tt1", "tt1"]})
    right = pd.DataFrame({"tconst": ["tt1"], "numVotes": [10]})

    with pytest.raises(pd.errors.MergeError):
        guarded_merge(Report(), "test", "movies <- ratings", left, right, "tconst", "1-1")
