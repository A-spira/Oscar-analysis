"""Analytical tables: eligible universe, `nominated` target and descriptive variables.

- film_universe   : 1 row = 1 eligible film x studio category x ceremony
- talent_universe : 1 row = 1 actor/actress x eligible film x talent category x ceremony

Eligibility follows the 2025 "B+" strategy (notebook 05_Nomination_modeling.ipynb); thresholds are
parameters so a sensitivity analysis can rerun the build. Every real nominee is re-injected even when
its film misses the thresholds. Prior Oscar record only counts strictly earlier ceremonies.
"""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from oscars.checks import Report, guarded_merge, profile_table
from oscars.clean import STUDIO_CATEGORIES, TALENT_CATEGORIES
from oscars.integrate import norm_key

STEP = "build_analytical"

GENRES = ["Drama", "Biography", "Comedy", "History", "War", "Romance", "Crime", "Music", "Thriller", "Animation"]
BUDGET_BINS = [0, 5e6, 20e6, 50e6, 100e6, float("inf")]
BUDGET_LABELS = ["<5M", "5-20M", "20-50M", "50-100M", "100M+"]
ACTING_ROLE = {
    "Best Actor in a Leading Role": "actor",
    "Best Actor in a Supporting Role": "actor",
    "Best Actress in a Leading Role": "actress",
    "Best Actress in a Supporting Role": "actress",
}


@dataclass(frozen=True)
class UniverseParams:
    """Eligibility thresholds (2025 'B+' values by default)."""

    min_votes: int = 10_000
    min_rating: float = 5.0
    min_runtime: int = 60
    min_film_year: int = 1999
    max_film_year: int = 2025
    excluded_genre: str = "Documentary"


# ---------------------------------------------------------------------------
# Prior Oscar record (no leakage: strictly earlier ceremonies only)
# ---------------------------------------------------------------------------


def _history_key(name: str) -> str:
    """Name key for the history: drop '(posthumous nomination)'-style notes, then typography."""
    return norm_key(re.sub(r"\([^)]*\)", "", name))


class OscarHistory:
    """Nominations and wins per person name, all ceremonies since 1929, for one group of categories."""

    def __init__(self, nominations: pd.DataFrame, categories: list[str]) -> None:
        self.events: dict[str, list[tuple[int, bool]]] = defaultdict(list)
        sub = nominations[nominations["category"].isin(categories)]
        for nominee, year, winner in sub[["nominee", "year", "winner"]].itertuples(index=False, name=None):
            for part in re.split(r"\s+and\s+", str(nominee)):
                self.events[_history_key(part)].append((int(str(year)), bool(winner)))

    def prior(self, name: object, year: int) -> tuple[int, int]:
        """(nominations, wins) of `name` in ceremonies strictly before `year`."""
        if not isinstance(name, str):
            return 0, 0
        past = [w for y, w in self.events.get(_history_key(name), []) if y < year]
        return len(past), sum(past)


# ---------------------------------------------------------------------------
# Film-level descriptive variables
# ---------------------------------------------------------------------------


def film_variables(movies: pd.DataFrame, tmdb: pd.DataFrame, report: Report) -> pd.DataFrame:
    """1 row per film: IMDb + TMDb variables known before the nominations (see decision log for caveats)."""
    f = movies.rename(
        columns={
            "primaryTitle": "title",
            "startYear": "film_year",
            "runtimeMinutes": "runtime_minutes",
            "averageRating": "imdb_rating",
            "numVotes": "imdb_votes",
        }
    )[["tconst", "title", "film_year", "runtime_minutes", "genres", "imdb_rating", "imdb_votes", "directors"]]
    genres = f["genres"].fillna("")
    for g in GENRES:
        f[f"is_{g.lower()}"] = genres.str.split(",").map(lambda gs, g=g: g in gs)

    t = tmdb[["tconst", "release_date", "original_language", "budget"]].copy()
    f = guarded_merge(report, STEP, "films <- tmdb", f, t, "tconst", "1-1")
    f["tmdb_available"] = f["release_date"].notna() | f["original_language"].notna()
    f["release_month"] = f["release_date"].dt.month.astype("Int8")
    f["is_q4_release"] = (f["release_month"] >= 10).astype("boolean")
    budget = f["budget"].to_numpy(dtype="float64", na_value=np.nan)
    f["budget_bucket"] = pd.cut(budget, BUDGET_BINS, labels=BUDGET_LABELS, right=False)
    f["budget_bucket"] = pd.Series(f["budget_bucket"], index=f.index).astype("string").fillna("unknown")
    f["is_english"] = (f["original_language"] == "en").astype("boolean")
    return f


def _directors_prior(films: pd.DataFrame, names: pd.DataFrame, history: OscarHistory) -> pd.DataFrame:
    """Max prior Directing record over a film's directors, at the film's ceremony year."""
    name_of = names.set_index("nconst")["primaryName"]
    rows = []
    for tconst, year, directors in films[["tconst", "year", "directors"]].itertuples(index=False, name=None):
        noms, wins = [], []
        for nc in directors.split(",") if isinstance(directors, str) else []:
            n, w = history.prior(name_of.get(nc), int(str(year)))
            noms.append(n)
            wins.append(w)
        rows.append((tconst, year, max(noms, default=pd.NA), max(wins, default=pd.NA)))
    return pd.DataFrame(rows, columns=["tconst", "year", "director_n_prior_noms", "director_n_prior_wins"])


# ---------------------------------------------------------------------------
# Universes
# ---------------------------------------------------------------------------


def eligible_films(movies: pd.DataFrame, params: UniverseParams) -> pd.Series:
    """Boolean mask of the 'B+' eligible films."""
    return (
        movies["startYear"].between(params.min_film_year, params.max_film_year)
        & (movies["runtimeMinutes"] >= params.min_runtime)
        & (movies["numVotes"] >= params.min_votes)
        & (movies["averageRating"] >= params.min_rating)
        & ~movies["genres"].fillna("").str.contains(params.excluded_genre)
    ).fillna(False)


def ceremony_year_of_films(movies: pd.DataFrame, positives: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Ceremony year per film: startYear + 1, except for films nominated in another year (actual year wins)."""
    noms_year = positives.groupby("tconst")["year"].agg(["min", "nunique"])
    out = movies[["tconst", "startYear"]].copy()
    out["year"] = (out["startYear"] + 1).astype("Int16")
    actual = out["tconst"].map(noms_year["min"])
    shifted = actual.notna() & (actual != out["year"])
    report.add(
        STEP,
        "ceremony_alignment",
        rule="ceremony = startYear + 1; nominated films use their actual nomination ceremony",
        nominated_films=len(noms_year),
        films_shifted_to_actual_ceremony=int(shifted.sum()),
        films_nominated_in_several_ceremonies=int((noms_year["nunique"] > 1).sum()),
    )
    out["year"] = actual.where(actual.notna(), out["year"]).astype("Int16")
    return out[["tconst", "year"]]


def _reinjection_reason(row: pd.Series, params: UniverseParams) -> str:
    reasons = []
    if not params.min_film_year <= row["film_year"] <= params.max_film_year:
        reasons.append("film_year")
    if pd.isna(row["imdb_votes"]) or row["imdb_votes"] < params.min_votes:
        reasons.append("votes")
    if pd.isna(row["imdb_rating"]) or row["imdb_rating"] < params.min_rating:
        reasons.append("rating")
    if pd.isna(row["runtime_minutes"]) or row["runtime_minutes"] < params.min_runtime:
        reasons.append("runtime")
    if params.excluded_genre in str(row["genres"]):
        reasons.append("genre")
    return "+".join(reasons) or "other"


def build_film_universe(
    matched: pd.DataFrame,
    films: pd.DataFrame,
    names: pd.DataFrame,
    nominations: pd.DataFrame,
    years: pd.DataFrame,
    params: UniverseParams,
    report: Report,
) -> pd.DataFrame:
    pos = (
        matched[matched["category"].isin(STUDIO_CATEGORIES)]
        .groupby(["tconst", "year", "category"], as_index=False)
        .agg(won=("winner", "any"))
    )
    elig = films[eligible_films(films.rename(columns=_IMDB_NAMES), params)].merge(years, on="tconst")
    base = pd.concat([elig.assign(category=c) for c in STUDIO_CATEGORIES], ignore_index=True)
    base["in_eligible_universe"] = True

    keys = ["tconst", "year", "category"]
    missing = pos.merge(base[keys], on=keys, how="left", indicator=True)
    missing = missing[missing["_merge"] == "left_only"][keys]
    reinj = missing.merge(films, on="tconst", how="left", validate="many_to_one")
    reinj["in_eligible_universe"] = False
    reinj["reinjection_reason"] = reinj.apply(_reinjection_reason, axis=1, params=params)

    fu = pd.concat([base, reinj], ignore_index=True)
    fu = guarded_merge(report, STEP, "film_universe <- positives", fu, pos, keys, "1-1")
    fu["nominated"] = fu["won"].notna()
    fu["won"] = fu["won"].fillna(False).astype(bool)

    history = OscarHistory(nominations, ["Best Directing"])
    prior = _directors_prior(fu[["tconst", "year", "directors"]].drop_duplicates(["tconst", "year"]), names, history)
    fu = guarded_merge(report, STEP, "film_universe <- director prior record", fu, prior, ["tconst", "year"], "n-1")

    _report_universe(report, "film_universe", fu, pos, ["tconst", "year", "category"])
    return fu[_FILM_COLUMNS].sort_values(["year", "category", "tconst"], ignore_index=True)


def build_talent_universe(
    matched: pd.DataFrame,
    films: pd.DataFrame,
    principals: pd.DataFrame,
    names: pd.DataFrame,
    nominations: pd.DataFrame,
    years: pd.DataFrame,
    params: UniverseParams,
    report: Report,
) -> pd.DataFrame:
    pos = (
        matched[matched["category"].isin(TALENT_CATEGORIES)]
        .groupby(["nconst", "tconst", "year", "category"], as_index=False)
        .agg(won=("winner", "any"))
    )
    elig = films[eligible_films(films.rename(columns=_IMDB_NAMES), params)].merge(years, on="tconst")

    cast = principals[principals["category"].isin(["actor", "actress"]) & principals["tconst"].isin(elig["tconst"])]
    rows_before = len(cast)
    cast = cast.sort_values("ordering").drop_duplicates(["nconst", "tconst"])  # same actor billed twice
    report.add(STEP, "cast_dedup", key=["nconst", "tconst"], rows_before=rows_before, rows_after=len(cast))
    cast = cast.rename(columns={"ordering": "billing_order", "category": "imdb_role"})
    cast["imdb_role"] = cast["imdb_role"].astype(str)

    base = pd.concat(
        [cast[cast["imdb_role"] == role].assign(category=c) for c, role in ACTING_ROLE.items()], ignore_index=True
    )
    base = base.merge(elig, on="tconst", how="inner", validate="many_to_one")
    base["in_eligible_universe"] = True

    keys = ["nconst", "tconst", "year", "category"]
    missing = pos.merge(base[keys], on=keys, how="left", indicator=True)
    missing = missing[missing["_merge"] == "left_only"][keys]
    reinj = missing.merge(films, on="tconst", how="left", validate="many_to_one")
    billing = principals.sort_values("ordering").drop_duplicates(["nconst", "tconst"])
    reinj = reinj.merge(
        billing[["nconst", "tconst", "ordering"]].rename(columns={"ordering": "billing_order"}),
        on=["nconst", "tconst"],
        how="left",
        validate="many_to_one",
    )
    reinj["in_eligible_universe"] = False
    in_films = reinj["tconst"].isin(elig["tconst"])
    reinj["reinjection_reason"] = reinj.apply(_reinjection_reason, axis=1, params=params)
    reinj.loc[in_films, "reinjection_reason"] = "not_in_imdb_cast_list_or_role"

    tu = pd.concat([base, reinj], ignore_index=True)
    tu = guarded_merge(report, STEP, "talent_universe <- positives", tu, pos, keys, "1-1")
    tu["nominated"] = tu["won"].notna()
    tu["won"] = tu["won"].fillna(False).astype(bool)
    tu = guarded_merge(report, STEP, "talent_universe <- names", tu, names, "nconst", "n-1")
    tu = tu.rename(columns={"primaryName": "person_name"})

    history = OscarHistory(nominations, TALENT_CATEGORIES)
    prior = [history.prior(n, int(y)) for n, y in zip(tu["person_name"], tu["year"], strict=True)]
    tu[["n_prior_noms", "n_prior_wins"]] = pd.DataFrame(prior, index=tu.index)

    _report_universe(report, "talent_universe", tu, pos, keys)
    return tu[_TALENT_COLUMNS].sort_values(["year", "category", "tconst", "nconst"], ignore_index=True)


def _report_universe(report: Report, name: str, df: pd.DataFrame, pos: pd.DataFrame, key: list[str]) -> None:
    assert not df.duplicated(key).any(), f"{name}: duplicate business key"
    n_pos = int(df["nominated"].sum())
    assert n_pos == len(pos), f"{name}: {len(pos)} positives expected, {n_pos} found"
    by_cat = df.groupby("category").agg(
        rows=("nominated", "size"),
        nominated=("nominated", "sum"),
        reinjected=("in_eligible_universe", lambda s: int((~s).sum())),
    )
    by_cat["nomination_rate_pct"] = (100 * by_cat["nominated"] / by_cat["rows"]).round(2)
    coverage = df.groupby("nominated")["tmdb_available"].mean().mul(100).round(2)
    report.add(
        STEP,
        "universe",
        table=name,
        rows=len(df),
        nominated=n_pos,
        reinjected=int((~df["in_eligible_universe"]).sum()),
        reinjection_reasons=df["reinjection_reason"].value_counts().to_dict(),
        by_category=by_cat.reset_index().to_dict(orient="records"),
        tmdb_coverage_pct={"nominated": coverage.get(True), "not_nominated": coverage.get(False)},
    )
    profile_table(
        report,
        STEP,
        name,
        df,
        grain=_GRAINS[name],
        key=key,
        na_columns=[c for c in _NA_COLUMNS if c in df.columns],
    )


_IMDB_NAMES = {
    "film_year": "startYear",
    "runtime_minutes": "runtimeMinutes",
    "imdb_votes": "numVotes",
    "imdb_rating": "averageRating",
}
_GRAINS = {
    "film_universe": "1 eligible (or re-injected nominated) film x studio category x ceremony",
    "talent_universe": "1 actor/actress x eligible (or re-injected) film x talent category x ceremony",
}
_NA_COLUMNS = [
    "imdb_votes",
    "imdb_rating",
    "runtime_minutes",
    "release_month",
    "budget",
    "original_language",
    "billing_order",
    "director_n_prior_noms",
    "person_name",
]
_FILM_VARS = [
    "title",
    "film_year",
    "release_date",
    "release_month",
    "is_q4_release",
    "genres",
    *[f"is_{g.lower()}" for g in GENRES],
    "budget",
    "budget_bucket",
    "original_language",
    "is_english",
    "imdb_votes",
    "imdb_rating",
    "runtime_minutes",
    "tmdb_available",
]
_FILM_COLUMNS = [
    "year",
    "category",
    "tconst",
    "nominated",
    "won",
    "in_eligible_universe",
    "reinjection_reason",
    *_FILM_VARS,
    "directors",
    "director_n_prior_noms",
    "director_n_prior_wins",
]
_TALENT_COLUMNS = [
    "year",
    "category",
    "nconst",
    "person_name",
    "tconst",
    "nominated",
    "won",
    "in_eligible_universe",
    "reinjection_reason",
    "billing_order",
    "n_prior_noms",
    "n_prior_wins",
    *_FILM_VARS,
]


def build(
    matched: pd.DataFrame,
    imdb: dict[str, pd.DataFrame],
    tmdb: pd.DataFrame,
    nominations: pd.DataFrame,
    report: Report,
    params: UniverseParams | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = params or UniverseParams()
    report.add(STEP, "parameters", **asdict(params))
    films = film_variables(imdb["movies"], tmdb, report)
    # One ceremony year per film for both views, aligned on its actual nominations in the 7 categories
    study = matched[matched["category"].isin(STUDIO_CATEGORIES + TALENT_CATEGORIES)]
    years = ceremony_year_of_films(imdb["movies"], study[["tconst", "year"]], report)
    fu = build_film_universe(matched, films, imdb["names"], nominations, years, params, report)
    tu = build_talent_universe(matched, films, imdb["principals"], imdb["names"], nominations, years, params, report)
    return fu, tu
