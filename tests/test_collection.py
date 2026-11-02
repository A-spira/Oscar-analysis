"""Collection check tests: ceremony sequence gaps/duplicates and figures cited in the docs."""

import pandas as pd

from oscars.collect.verify_collection import billing_na_counts, compare_claim, sequence_issues


def test_complete_sequence_has_no_issue() -> None:
    """Ceremonies 1..5, each once, in any order, pass."""
    issues = sequence_issues([3, 1, 2, 5, 4], first=1, last=5)

    assert issues.ok
    assert (issues.missing, issues.duplicated, issues.out_of_range) == ([], [], [])


def test_gaps_duplicates_and_out_of_range_values_are_all_reported() -> None:
    """A missing ceremony, a ceremony scraped twice and a number outside the range are each detected."""
    issues = sequence_issues([1, 2, 2, 4, 7], first=1, last=5)

    assert not issues.ok
    assert issues.missing == [3, 5]
    assert issues.duplicated == [2]
    assert issues.out_of_range == [7]


def test_billing_na_claim_counts_na_rows_and_checks_they_are_all_nominees() -> None:
    """A cited 'billing_order NA' figure must equal both the NA rows and the NA rows that are nominees."""
    talent = pd.DataFrame(
        {
            "nominated": [True, True, False, True],
            "billing_order": [None, None, 3, 1],  # 2 NA rows, both nominees; a ranked nominee (Elliot Page case)
        }
    )
    counts = billing_na_counts(talent)
    assert counts == (2.0, 2.0)
    assert compare_claim("3 re-injected rows, 2 with `billing_order` NA", [3, counts]) == []

    talent.loc[2, "billing_order"] = None  # a non-nominee with NA: no longer "all nominees"
    assert compare_claim("3 re-injected rows, 2 with `billing_order` NA", [3, billing_na_counts(talent)]) == [
        ("2", "3 / 2")
    ]
