"""Collection check tests: the ceremony sequence must have no gap and no duplicate."""

from oscars.collect.verify_collection import sequence_issues


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
