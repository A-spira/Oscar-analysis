"""Transformation tests: label normalisation edge cases and TMDb unknown amounts."""

import pandas as pd

from oscars.clean import normalize_categories, strip_footnotes, zero_to_na
from oscars.integrate import norm_key, norm_title


def test_category_labels_collapse_to_one_canonical_label() -> None:
    """Footnote markers, double spaces and historical variants all map to the same canonical category."""
    raw = pd.Series(
        [
            "Best Actor",
            "Best Actor in a Leading Role",
            "Best Writing (Adapted Screenplay)[22]",
            "Best  Writing (Screenplay -- Original)",
            "Best Director",
            "Best Foreign Language Film[14]",
        ]
    )
    assert normalize_categories(raw).tolist() == [
        "Best Actor in a Leading Role",
        "Best Actor in a Leading Role",
        "Best Writing (Adapted Screenplay)",
        "Best Writing (Original Screenplay)",
        "Best Directing",
        "Best International Feature Film",
    ]
    assert strip_footnotes("Best Picture") == "Best Picture"  # no marker: unchanged


def test_title_and_name_keys_ignore_typography() -> None:
    """Accents, case, punctuation, footnotes and posthumous markers do not prevent a match."""
    assert norm_key("J. K. Simmons") == norm_key("J.K. Simmons")
    assert norm_key("Benicio del Toro") == norm_key("Benicio Del Toro")
    assert norm_key("Ryūsuke Hamaguchi") == norm_key("Ryusuke Hamaguchi")
    assert norm_key("Elliot Page [ a ]") == norm_key("Elliot Page")
    assert norm_key("Heath Ledger †") == norm_key("Heath Ledger")
    assert norm_key("WALL-E") == norm_key("WALL·E")
    assert norm_key("Good Night, and Good Luck") == norm_key("Good Night, and Good Luck.")


def test_norm_title_strips_wikipedia_suffixes() -> None:
    """Country/language and producer suffixes added by Wikipedia are removed before title matching."""
    assert norm_title("The Barbarian Invasions (Canada) in French") == "The Barbarian Invasions"
    assert norm_title("Amélie[3]") == "Amélie"
    assert norm_title("Anora – Sean Baker") == "Anora"


def test_tmdb_zero_budget_becomes_na_not_zero() -> None:
    """A TMDb budget of 0 means unknown and must become NA, while real amounts and NA are kept."""
    out = zero_to_na(pd.Series([0, 15_000_000, None, 1], dtype="Int64"))

    assert str(out.dtype) == "Int64"
    assert out.isna().tolist() == [True, False, True, False]
    assert out.iloc[1] == 15_000_000
    assert out.iloc[3] == 1
