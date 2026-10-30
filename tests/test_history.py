"""Anti-leakage tests: the prior Oscar record only uses earlier ceremonies."""

import pandas as pd

from oscars.build_analytical import OscarHistory

ACTING = ["Best Actress in a Leading Role", "Best Actress in a Supporting Role"]


def _nominations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "year": [2000, 2005, 2005, 2010, 2010, 2008],
            "category": [
                "Best Actress in a Leading Role",
                "Best Actress in a Supporting Role",
                "Best Actress in a Leading Role",
                "Best Actress in a Leading Role",
                "Best Picture",  # not an acting category: never counted
                "Best Directing",
            ],
            "nominee": [
                "Meryl Streep",
                "Meryl Streep",
                "Meryl Streep (posthumous nomination)",  # note in parentheses is ignored
                "MERYL STREEP",  # case-insensitive key
                "Meryl Streep",
                "Meryl Streep",
            ],
            "winner": [True, False, True, False, True, True],
        }
    )


def test_prior_record_counts_only_strictly_earlier_ceremonies() -> None:
    """At ceremony Y, n_prior_noms / n_prior_wins use ceremonies < Y only (no same-year or future data)."""
    history = OscarHistory(_nominations(), ACTING)

    assert history.prior("Meryl Streep", 2000) == (0, 0)  # her 2000 nomination is not "prior"
    assert history.prior("Meryl Streep", 2001) == (1, 1)
    assert history.prior("Meryl Streep", 2005) == (1, 1)  # both 2005 nominations excluded
    assert history.prior("Meryl Streep", 2006) == (3, 2)
    assert history.prior("Meryl Streep", 2010) == (3, 2)  # 2010 still excluded
    assert history.prior("Meryl Streep", 2011) == (4, 2)  # Best Picture / Directing never counted


def test_prior_record_of_unknown_or_missing_person_is_zero() -> None:
    """A person never nominated, or a missing name, has an empty record rather than an error."""
    history = OscarHistory(_nominations(), ACTING)

    assert history.prior("Unknown Actress", 2020) == (0, 0)
    assert history.prior(None, 2020) == (0, 0)


def test_co_nominees_each_get_their_own_record() -> None:
    """A joint nomination 'X and Y' counts once for each person."""
    noms = pd.DataFrame(
        {
            "year": [2008],
            "category": ["Best Directing"],
            "nominee": ["Joel Coen and Ethan Coen"],
            "winner": [True],
        }
    )
    history = OscarHistory(noms, ["Best Directing"])

    assert history.prior("Joel Coen", 2009) == (1, 1)
    assert history.prior("Ethan Coen", 2009) == (1, 1)
