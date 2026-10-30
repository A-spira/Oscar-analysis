"""Oscar <-> IMDb matching (films and persons) and TMDb merge, with integration checks.

Matching rules are those of the 2025 notebook 01_Merge_datasets.ipynb:
- film categories: exact title, then `norm_title`, release year in [Y-3, Y], closest to Y-1;
- person categories: exact name -> nconst(s), then the person's film in [Y-3, Y] closest to Y-1,
  with a 0.5 bonus when the IMDb role matches the category (OSCAR_TO_IMDB_ROLE).
"""

import re
import unicodedata
from typing import Any

import pandas as pd

from oscars.checks import Report, guarded_merge, match_rate, outliers, profile_table
from oscars.clean import STUDIO_CATEGORIES, TALENT_CATEGORIES
from oscars.paths import REFERENCE_DIR

STEP = "integrate"
MIN_CEREMONY = 2000
SHORT_CATEGORIES = {"Best Short Film (Animated)", "Best Short Film (Live Action)", "Best Documentary Short Film"}

OSCAR_TO_IMDB_ROLE = {
    "Best Actor in a Leading Role": {"actor"},
    "Best Actress in a Leading Role": {"actress"},
    "Best Actor in a Supporting Role": {"actor"},
    "Best Actress in a Supporting Role": {"actress"},
    "Best Directing": {"director"},
}

TitleLookup = dict[str, list[tuple[str, int]]]
NameLookup = dict[str, list[str]]
FilmIndex = dict[str, list[tuple[str, int, str]]]
MatchResult = tuple[str | None, str | None, str | None, str]  # tconst, nconst, imdb_role, status


def norm_title(s: str) -> str:
    """Strip Wikipedia footnotes, '(Country) in Language' suffixes and '– producer' suffixes (2025 rules)."""
    s = re.sub(r"\[\d+\]", "", s)
    s = re.sub(r"\s*\([^)]*\)\s+in\s+[A-Za-z\s]+$", "", s, flags=re.I)
    s = re.sub(r"\s+[-–—]\s+[A-Z][^-–—]*$", "", s)
    s = s.split(" (")[0].strip() if " (" in s and s.endswith(")") else s
    return s.strip()


def norm_key(s: str) -> str:
    """Typography-insensitive key for the fallback match: no accents, case, punctuation, spaces,
    Wikipedia footnotes ('[ a ]') or posthumous markers ('†'). 'J. K. Simmons' == 'J.K. Simmons'."""
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _group(df: pd.DataFrame, key: str, values: list[str]) -> dict[Any, list[Any]]:
    """{key: [values...]} keeping row order (same as the notebook's groupby().apply(list))."""
    out: dict[Any, list[Any]] = {}
    for row in df[[key, *values]].itertuples(index=False, name=None):
        out.setdefault(row[0], []).append(row[1:] if len(values) > 1 else row[1])
    return out


def match_film(title: str, year: int, lookup: TitleLookup) -> tuple[str | None, str]:
    """(tconst, status). Status explains a failed match."""
    clean = norm_title(title)
    candidates = lookup.get(title, []) or (lookup.get(clean, []) if clean != title else [])
    if not candidates:
        return None, "title_not_found_in_imdb_movies"
    scored = [(tc, abs(yr - (year - 1))) for tc, yr in candidates if year - 3 <= yr <= year]
    if not scored:
        return None, "title_found_outside_year_window"
    scored.sort(key=lambda x: x[1])
    return scored[0][0], "matched"


def match_person(
    name: str, year: int, category: str, names: NameLookup, films: FilmIndex
) -> tuple[str | None, str | None, str | None, str]:
    """(tconst, nconst, imdb_role, status) of the film a person is nominated for."""
    nconsts = names.get(name, [])
    if not nconsts:
        return None, None, None, "name_not_found_in_imdb"
    expected = OSCAR_TO_IMDB_ROLE.get(category, set())
    best: tuple[str | None, str | None, str | None] = (None, None, None)
    best_score = float("inf")
    for nconst in nconsts:
        for tconst, film_year, role in films.get(nconst, []):
            if year - 3 <= film_year <= year:
                role_match = 1 if (not expected or role in expected) else 0
                score = abs(film_year - (year - 1)) - role_match * 0.5
                if score < best_score:
                    best_score, best = score, (tconst, nconst, role)
    if best[0] is None:
        return None, None, None, "no_film_in_year_window"
    return *best, "matched"


def _title_candidates(film: str, year: int, titles: TitleLookup, titles_norm: TitleLookup) -> list[str]:
    """IMDb tconsts whose title matches `film` (exact, norm_title, then typography key), in [Y-3, Y],
    closest to Y-1 first."""
    for lookup, key in [(titles, film), (titles, norm_title(film)), (titles_norm, norm_key(norm_title(film)))]:
        scored = [(tc, abs(yr - (year - 1))) for tc, yr in lookup.get(key, []) if year - 3 <= yr <= year]
        if scored:
            return [tc for tc, _ in sorted(scored, key=lambda x: x[1])]
    return []


def match_person_with_film(
    name: str,
    film: str,
    year: int,
    titles: TitleLookup,
    titles_norm: TitleLookup,
    names: NameLookup,
    names_norm: NameLookup,
    cast: dict[tuple[str, str], str],
    n_credits: dict[str, int],
) -> tuple[str | None, str | None, str | None, str]:
    """(tconst, nconst, imdb_role, status) using the film named on Wikipedia.

    1. candidate films by title in the year window; candidate people by name (exact, then typography key,
       'X and Y' split);
    2. a (person, film) pair credited in title.principals wins ('matched_name_and_film');
    3. otherwise the closest film by title is kept and, among homonyms, the person with the most IMDb
       credits ('matched_film_only': person not in the film's top-billed cast).
    """
    films = _title_candidates(film, year, titles, titles_norm)
    if not films:
        return None, None, None, "film_title_not_found"
    people: list[str] = []
    for part in re.split(r"\s+and\s+", name):
        people += names.get(part, []) or names_norm.get(norm_key(part), [])
    for tconst in films:
        for nconst in people:
            if (nconst, tconst) in cast:
                return tconst, nconst, cast[(nconst, tconst)], "matched_name_and_film"
    best = max(people, key=lambda n: n_credits.get(n, 0), default=None)
    return films[0], best, None, "matched_film_only"


OVERRIDES_FILE = REFERENCE_DIR / "match_overrides.csv"
OVERRIDE_KEY = ["year", "category", "nominee", "winner"]


def apply_overrides(noms: pd.DataFrame, principals: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Hand-checked corrections (data/reference/match_overrides.csv), applied after automatic matching."""
    ov = pd.read_csv(OVERRIDES_FILE, dtype={"nconst": "string"})
    ov["year"] = ov["year"].astype("Int16")
    assert not ov.duplicated(OVERRIDE_KEY).any(), "duplicate override keys"
    hit = noms.reset_index().merge(ov, on=OVERRIDE_KEY, how="inner", suffixes=("", "_ov"), validate="one_to_one")
    assert len(hit) == len(ov), f"{len(ov) - len(hit)} override(s) match no nomination"
    roles = principals.drop_duplicates(["tconst", "nconst"]).set_index(["tconst", "nconst"])["category"]
    same_tconst = hit["tconst"] == hit["tconst_ov"]
    same_nconst = hit["nconst"].fillna("") == hit["nconst_ov"].fillna("")
    redundant = hit.loc[same_tconst & same_nconst, OVERRIDE_KEY].astype(str).values.tolist()
    for idx, tconst, nconst in hit[["index", "tconst_ov", "nconst_ov"]].itertuples(index=False, name=None):
        is_person = isinstance(nconst, str)
        role = roles.get((tconst, nconst)) if is_person else None
        noms.loc[idx, "tconst"] = tconst
        noms.loc[idx, "nconst"] = nconst if is_person else None
        noms.loc[idx, "imdb_role"] = None if role is None else str(role)
        noms.loc[idx, "match_status"] = "matched_override"
    report.add(
        STEP,
        "overrides",
        file=str(OVERRIDES_FILE.relative_to(REFERENCE_DIR.parents[1])),
        applied=len(hit),
        changed_previous_match=int((hit["tconst"].notna() & ~(same_tconst & same_nconst)).sum()),
        redundant_with_automatic_match=redundant,
    )
    return noms


def match_nominations(nominations: pd.DataFrame, imdb: dict[str, pd.DataFrame], report: Report) -> pd.DataFrame:
    """2000+ film/person nominations with tconst (and nconst for persons) and a match status."""
    movies, principals, names = imdb["movies"], imdb["principals"], imdb["names"]
    noms = nominations[(nominations["year"] >= MIN_CEREMONY) & (nominations["nominee_type"] != "other")].copy()
    report.add(
        STEP,
        "scope",
        nominations_2000plus=int((nominations["year"] >= MIN_CEREMONY).sum()),
        film_or_person=len(noms),
        other_categories_dropped=sorted(
            nominations.loc[
                (nominations["year"] >= MIN_CEREMONY) & (nominations["nominee_type"] == "other"), "category"
            ].unique()
        ),
    )

    title_lookup: TitleLookup = _group(movies.dropna(subset=["startYear"]), "primaryTitle", ["tconst", "startYear"])
    name_lookup: NameLookup = _group(names, "primaryName", ["nconst"])
    with_year = principals.merge(movies[["tconst", "startYear"]], on="tconst", how="inner", validate="many_to_one")
    film_index: FilmIndex = _group(
        with_year.dropna(subset=["startYear"]), "nconst", ["tconst", "startYear", "category"]
    )

    # Fallback lookups keyed by typography-insensitive keys (used only when the 2025 exact rules fail)
    movies_k = movies.dropna(subset=["startYear", "primaryTitle"]).assign(key=lambda d: d["primaryTitle"].map(norm_key))
    names_k = names.dropna(subset=["primaryName"]).assign(key=lambda d: d["primaryName"].map(norm_key))
    title_lookup_norm: TitleLookup = _group(movies_k, "key", ["tconst", "startYear"])
    name_lookup_norm: NameLookup = _group(names_k, "key", ["nconst"])

    cast_rows = principals.drop_duplicates(["nconst", "tconst"])
    cast = {(n, t): str(c) for n, t, c in cast_rows[["nconst", "tconst", "category"]].itertuples(index=False)}
    n_credits = {str(k): int(v) for k, v in principals["nconst"].value_counts().items()}

    results: list[MatchResult] = []
    strict_2025: list[str | None] = []
    rows = zip(
        noms["nominee"].astype(str).tolist(),
        noms["year"].astype(int).tolist(),
        noms["category"].astype(str).tolist(),
        noms["nominee_type"].astype(str).tolist(),
        noms["film"].tolist(),
        strict=True,
    )
    for nominee, year, category, kind, film in rows:
        if kind == "film":
            tconst, status = match_film(nominee, year, title_lookup)
            if tconst is None:
                keys = [norm_key(norm_title(nominee))]
                if ":" in nominee:  # 'Precious: Based on the Novel ...' -> 'Precious'
                    keys.append(norm_key(nominee.split(":")[0]))
                for k in keys:
                    fb, _ = match_film(k, year, title_lookup_norm)
                    if fb is not None:
                        tconst, status = fb, "matched_normalized"
                        break
            results.append((tconst, None, None, status))
            strict_2025.append(tconst if status == "matched" else None)
        else:
            res = match_person(nominee, year, category, name_lookup, film_index)
            strict_2025.append(res[0])
            if isinstance(film, str) and film:
                # 2026: the film named on Wikipedia replaces the 2025 'closest film' guess
                res = match_person_with_film(
                    nominee, film, year, title_lookup, title_lookup_norm,
                    name_lookup, name_lookup_norm, cast, n_credits,
                )  # fmt: skip
            if res[0] is None:
                # 'Joel Coen and Ethan Coen' -> first co-nominee found (same film)
                for part in re.split(r"\s+and\s+", nominee):
                    pfb = match_person(norm_key(part), year, category, name_lookup_norm, film_index)
                    if pfb[0] is not None:
                        res = (*pfb[:3], "matched_normalized")
                        break
            results.append(res)
    noms[["tconst", "nconst", "imdb_role", "match_status"]] = pd.DataFrame(results, index=noms.index)
    # Match given by the 2025 exact rules alone (before fallback, film-based matching and overrides)
    noms["tconst_2025"] = pd.Series(strict_2025, index=noms.index, dtype="string")
    noms = apply_overrides(noms, principals, report)

    # Probable reason refinement for unmatched films (diagnosis only)
    is_song = noms["category"].eq("Best Music (Original Song)") & noms["tconst"].isna()
    noms.loc[is_song, "match_status"] = "song_title_not_a_film_title"
    is_short = noms["category"].isin(SHORT_CATEGORIES) & noms["match_status"].eq("title_not_found_in_imdb_movies")
    noms.loc[is_short, "match_status"] = "short_film_not_in_imdb_movie_type"
    return noms


def report_match_rates(noms: pd.DataFrame, names: pd.DataFrame, report: Report) -> None:
    films = noms[noms["nominee_type"] == "film"]
    persons = noms[noms["nominee_type"] == "person"]
    study = noms[noms["category"].isin(STUDIO_CATEGORIES + TALENT_CATEGORIES)]
    scopes = [
        ("films (all film categories, 2000+)", films),
        ("persons (all person categories, 2000+)", persons),
        ("total (2000+)", noms),
        ("7 study categories", study),
        *[(f"category: {cat}", g) for cat, g in study.groupby("category")],
    ]
    for scope, g in scopes:
        # 'strict 2025' = exact rules only, comparable with last year; 'final' = + fallback + overrides
        match_rate(report, STEP, f"{scope} [strict 2025]", g["tconst_2025"])
        match_rate(report, STEP, f"{scope} [final]", g["tconst"])
    report.add(STEP, "match_status", counts=noms["match_status"].value_counts().to_dict())
    both = persons[persons["tconst"].notna() & persons["tconst_2025"].notna()]
    differs = both[both["tconst"] != both["tconst_2025"]]
    report.add(
        STEP,
        "person_film_vs_2025_rule",
        compared=len(both),
        same_film=len(both) - len(differs),
        different_film=len(differs),
        different_film_pct=round(100 * len(differs) / len(both), 2) if len(both) else None,
        examples=differs[["year", "category", "nominee", "film"]].head(10).astype(str).values.tolist(),
    )
    report.add(
        STEP, "unmatched_reasons", counts=noms.loc[noms["tconst"].isna(), "match_status"].value_counts().to_dict()
    )

    # Quality: role coherence and homonyms for persons
    matched_p = persons[persons["tconst"].notna()]
    expected = matched_p["category"].map(OSCAR_TO_IMDB_ROLE)
    role_ok = [role in exp for role, exp in zip(matched_p["imdb_role"], expected, strict=True)]
    homonyms = names.groupby("primaryName")["nconst"].nunique()
    ambiguous = persons["nominee"].isin(homonyms[homonyms > 1].index)
    report.add(
        STEP,
        "person_match_quality",
        matched=len(matched_p),
        role_mismatch=len(matched_p) - sum(role_ok),
        role_mismatch_examples=matched_p.loc[[not ok for ok in role_ok], ["year", "category", "nominee", "imdb_role"]]
        .head(10)
        .astype(str)
        .values.tolist(),
        nominee_names_with_several_imdb_ids=int(ambiguous.sum()),
    )
    report.add(
        STEP,
        "title_normalisation",
        film_nominees=len(films),
        changed_by_norm_title=int((films["nominee"].map(norm_title) != films["nominee"]).sum()),
    )


def integrate(
    nominations: pd.DataFrame, imdb: dict[str, pd.DataFrame], tmdb: pd.DataFrame, report: Report
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (matched nominations enriched with IMDb + TMDb, unmatched nominations)."""
    noms = match_nominations(nominations, imdb, report)
    report_match_rates(noms, imdb["names"], report)

    unmatched = noms[noms["tconst"].isna()][["year", "category", "nominee", "winner", "nominee_type", "match_status"]]
    unmatched = unmatched.rename(columns={"match_status": "probable_reason"})

    matched = noms[noms["tconst"].notna()].drop(columns="tconst_2025")
    movie_cols = ["tconst", "primaryTitle", "startYear", "runtimeMinutes", "genres", "averageRating", "numVotes"]
    matched = guarded_merge(
        report, STEP, "nominations <- imdb movies", matched, imdb["movies"][movie_cols], "tconst", "n-1"
    )
    tmdb_cols = ["tconst", "tmdb_id", "release_date", "original_language", "budget", "revenue", "error"]
    matched = guarded_merge(report, STEP, "nominations <- tmdb", matched, tmdb[tmdb_cols], "tconst", "n-1")
    matched = matched.rename(columns={"error": "tmdb_error"})

    profile_table(
        report,
        STEP,
        "nominations_matched",
        matched,
        grain="1 nomination 2000+ matched to an IMDb movie",
        key=["year", "category", "nominee", "tconst"],
        na_columns=["nconst", "runtimeMinutes", "averageRating", "numVotes", "tmdb_id", "release_date", "budget"],
    )
    movies = imdb["movies"]
    outliers(report, STEP, "imdb runtimeMinutes (movies)", movies["runtimeMinutes"], low=40, high=300)
    outliers(report, STEP, "imdb numVotes (movies)", movies["numVotes"], low=0)
    outliers(report, STEP, "tmdb budget (USD)", tmdb["budget"], low=10_000, high=500_000_000)
    return matched, unmatched
