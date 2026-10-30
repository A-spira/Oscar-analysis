"""Parsing, typing and normalisation of the raw sources.

- Oscar nominations (Wikipedia scrape): category normalisation, long format (1 row = 1 nominee).
- IMDb: chunked reads of the raw .tsv.gz, early filtering, compact parquet cache in data/interim/.
- TMDb: raw cache(s) -> 1 row per tconst, budget/revenue 0 treated as unknown.

Normalisation logic is taken from the 2025 notebook 01_Merge_datasets.ipynb.
"""

import re

import pandas as pd

from oscars.checks import Report, guarded_merge, profile_table
from oscars.paths import IMDB_DIR, INTERIM_DIR, SCRAPING_DIR, TMDB_DIR

STEP = "clean"

# Historical category variants -> one label (2025 mapping, footnote variants are handled by strip_footnotes)
CATEGORY_MAP = {
    # Acting
    "Best Actor": "Best Actor in a Leading Role",
    "Best Actress": "Best Actress in a Leading Role",
    # Writing
    "Best Writing (Screenplay Written Directly for the Screen)": "Best Writing (Original Screenplay)",
    "Best Writing (Screenplay Based on Material Previously Produced or Published)": "Best Writing (Adapted Screenplay)",
    "Best Writing (Screenplay -- Original)": "Best Writing (Original Screenplay)",  # was double-spaced
    # International film
    "Best Foreign Language Film": "Best International Feature Film",
    # Art Direction -> Production Design
    "Best Art Direction": "Best Production Design",
    "Best Art Direction (Black-and-White)": "Best Production Design",
    "Best Art Direction (Color)": "Best Production Design",
    # Sound
    "Best Sound Recording": "Best Sound",
    "Best Sound Mixing": "Best Sound",
    "Best Sound Effects Editing": "Best Sound Editing",
    # Makeup
    "Best Makeup": "Best Makeup and Hairstyling",
    # Documentaries
    "Best Documentary (Feature)": "Best Documentary Feature Film",
    "Best Documentary (Short Subject)": "Best Documentary Short Film",
    # Shorts
    "Best Short Subject (Animated)": "Best Short Film (Animated)",
    "Best Short Subject (Cartoon)": "Best Short Film (Animated)",
    "Best Animated Short Film": "Best Short Film (Animated)",
    "Best Live Action Short Film": "Best Short Film (Live Action)",
    # Directing variants (added in 2026: needed for the 1929+ prior-record history)
    "Best Director": "Best Directing",
    "Best Directing (Dramatic Picture)": "Best Directing",
    "Best Directing (Comedy Picture)": "Best Directing",
}

# Categories whose nominee is a film title
FILM_CATEGORIES = {
    "Best Picture",
    "Best Animated Feature Film",
    "Best International Feature Film",
    "Best Documentary Feature Film",
    "Best Documentary Short Film",
    "Best Short Film (Animated)",
    "Best Short Film (Live Action)",
    "Best Motion Picture",
    "Best Cinematography",
    "Best Costume Design",
    "Best Film Editing",
    "Best Visual Effects",
    "Best Production Design",
    "Best Sound",
    "Best Sound Editing",
    "Best Makeup and Hairstyling",
    "Best Music (Original Score)",
    "Best Music (Original Song)",
    "Best Writing (Original Screenplay)",
    "Best Writing (Adapted Screenplay)",
}

# Categories whose nominee is a person
PERSON_CATEGORIES = {
    "Best Actor in a Leading Role",
    "Best Actress in a Leading Role",
    "Best Actor in a Supporting Role",
    "Best Actress in a Supporting Role",
    "Best Directing",
}

# The 7 categories of the study
STUDIO_CATEGORIES = ["Best Picture", "Best Directing", "Best Writing (Original Screenplay)"]
TALENT_CATEGORIES = [
    "Best Actor in a Leading Role",
    "Best Actress in a Leading Role",
    "Best Actor in a Supporting Role",
    "Best Actress in a Supporting Role",
]

IMDB_MIN_YEAR = 1995  # matching window needs films up to 3 years before the 2000 ceremony (2025 choice)


def strip_footnotes(s: str) -> str:
    """Remove Wikipedia footnote markers such as '[13]' and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"\[\d+\]", "", s)).strip()


# ---------------------------------------------------------------------------
# Oscar nominations
# ---------------------------------------------------------------------------


def load_oscars(report: Report) -> pd.DataFrame:
    """Raw scrape, typed, with `category_clean`. Grain: 1 category x ceremony."""
    raw = pd.read_csv(SCRAPING_DIR / "all_data_oscars.csv", dtype={"winner": "string", "nominees": "string"})
    raw["year"] = raw["year"].astype("Int16")
    raw["ceremony_number"] = raw["ceremony_number"].astype("Int16")
    raw["category_clean"] = raw["category"].map(strip_footnotes).replace(CATEGORY_MAP)

    report.add(
        STEP,
        "category_normalisation",
        labels_raw=raw["category"].nunique(),
        labels_without_footnotes=raw["category"].map(strip_footnotes).nunique(),
        labels_clean=raw["category_clean"].nunique(),
    )
    profile_table(
        report,
        STEP,
        "oscars_raw",
        raw,
        grain="1 category x ceremony (winner + ' | '-joined nominees)",
        key=["year", "category_clean"],
        na_columns=["date", "winner", "nominees"],
    )
    return raw


def explode_nominations(raw: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Long format: 1 row = 1 nominee x category x ceremony, with a boolean `winner`."""
    rows: list[dict[str, object]] = []
    cols = ["ceremony_number", "year", "category_clean", "winner", "nominees"]
    for number, year, category, winner, nominees in raw[cols].itertuples(index=False, name=None):
        base = {"ceremony_number": number, "year": year, "category": category}
        rows.append({**base, "nominee": str(winner).strip(), "winner": True})
        if isinstance(nominees, str):
            for nominee in nominees.split("|"):
                if nominee.strip():
                    rows.append({**base, "nominee": nominee.strip(), "winner": False})
    long = pd.DataFrame(rows)
    long["year"] = long["year"].astype("Int16")
    long["nominee_type"] = long["category"].map(
        lambda c: "film" if c in FILM_CATEGORIES else ("person" if c in PERSON_CATEGORIES else "other")
    )

    profile_table(
        report,
        STEP,
        "nominations",
        long,
        grain="1 nominee x category x ceremony (all years)",
        key=["year", "category", "nominee"],
        na_columns=["nominee", "winner"],
    )
    dups = long[long.duplicated(["year", "category", "nominee"], keep=False)]
    report.add(
        STEP,
        "duplicates",
        table="nominations",
        rows_in_duplicate_groups=len(dups),
        examples=dups[["year", "category", "nominee"]].head(10).astype(str).values.tolist(),
        decision="kept: a person can be nominated twice in one category for two films (e.g. 1930s directors)",
    )
    winners = long[long["winner"]].groupby(["year", "category"]).size()
    report.add(STEP, "winners_per_category", groups=len(winners), groups_with_not_exactly_one=int((winners != 1).sum()))
    return long


def load_nominations(raw: pd.DataFrame, report: Report) -> pd.DataFrame:
    """Long format from the detailed scrape (1 row = 1 nominee, with the film named on Wikipedia).

    Must reproduce `explode_nominations(raw)` exactly on (year, category, nominee, winner); the
    detailed file only adds `film` (person categories) and `position`.
    """
    det = pd.read_csv(SCRAPING_DIR / "all_data_oscars_detailed.csv", dtype={"film": "string", "nominee": "string"})
    long = pd.DataFrame(
        {
            "ceremony_number": det["ceremony_number"].astype("Int16"),
            "year": det["year"].astype("Int16"),
            "category": det["category"].map(strip_footnotes).replace(CATEGORY_MAP),
            "nominee": det["nominee"].str.strip(),
            "winner": det["is_winner"].astype(bool),
            "position": det["position"].astype("Int16"),
            "film": det["film"].str.strip(),
        }
    )
    long["nominee_type"] = long["category"].map(
        lambda c: "film" if c in FILM_CATEGORIES else ("person" if c in PERSON_CATEGORIES else "other")
    )

    key = ["year", "category", "nominee", "winner"]
    ref = explode_nominations(raw, report)
    a = long[key].astype(str).value_counts()
    b = ref[key].astype(str).value_counts()
    diff = a.sub(b, fill_value=0)
    persons_2000 = long[(long["year"] >= 2000) & (long["nominee_type"] == "person")]
    report.add(
        STEP,
        "detailed_scrape_consistency",
        rows_detailed=len(long),
        rows_from_csv=len(ref),
        differing_keys=int((diff != 0).sum()),
        person_nominations_2000plus=len(persons_2000),
        with_film=int(persons_2000["film"].notna().sum()),
    )
    assert (diff == 0).all(), "detailed scrape does not reproduce all_data_oscars.csv"
    return long


# ---------------------------------------------------------------------------
# IMDb
# ---------------------------------------------------------------------------


def _tsv_chunks(name: str, **kwargs: object) -> "pd.io.parsers.TextFileReader":
    return pd.read_csv(IMDB_DIR / name, sep="\t", na_values="\\N", chunksize=1_000_000, **kwargs)  # type: ignore[call-overload,no-any-return]


def _parse_movies() -> tuple[pd.DataFrame, int]:
    """title.basics -> movies (titleType == movie, isAdult == 0, startYear >= IMDB_MIN_YEAR)."""
    n_raw = 0
    parts = []
    # all columns as str: some titles contain stray tabs that shift columns (2025 note)
    for ch in _tsv_chunks("title.basics.tsv.gz", dtype=str, on_bad_lines="skip"):
        n_raw += len(ch)
        ch = ch[ch["titleType"] == "movie"]
        year = pd.to_numeric(ch["startYear"], errors="coerce")
        adult = pd.to_numeric(ch["isAdult"], errors="coerce")
        parts.append(ch[(year >= IMDB_MIN_YEAR) & (adult == 0)])
    movies = pd.concat(parts, ignore_index=True)
    movies["startYear"] = pd.to_numeric(movies["startYear"], errors="coerce").astype("Int16")
    movies["runtimeMinutes"] = pd.to_numeric(movies["runtimeMinutes"], errors="coerce").astype("Int32")
    cols = ["tconst", "primaryTitle", "originalTitle", "startYear", "runtimeMinutes", "genres"]
    return movies[cols], n_raw


def build_imdb_cache(report: Report) -> None:
    """Parse the raw IMDb TSVs once and store filtered parquet files in data/interim/."""
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    movies, n_basics = _parse_movies()
    ratings = pd.read_csv(IMDB_DIR / "title.ratings.tsv.gz", sep="\t", na_values="\\N")
    movies = guarded_merge(report, STEP, "movies <- title.ratings", movies, ratings, "tconst", "1-1")
    movie_ids = set(movies["tconst"])
    crew = pd.concat(
        [c[c["tconst"].isin(movie_ids)] for c in _tsv_chunks("title.crew.tsv.gz", dtype=str)], ignore_index=True
    )
    movies = guarded_merge(report, STEP, "movies <- title.crew", movies, crew, "tconst", "1-1")
    movies["numVotes"] = movies["numVotes"].astype("Int32")
    movies["averageRating"] = movies["averageRating"].astype("Float32")

    principals = pd.concat(
        [
            c[c["tconst"].isin(movie_ids)]
            for c in _tsv_chunks("title.principals.tsv.gz", usecols=["tconst", "ordering", "nconst", "category"])
        ],
        ignore_index=True,
    )
    principals["ordering"] = principals["ordering"].astype("Int16")
    principals["category"] = principals["category"].astype("category")

    person_ids = set(principals["nconst"].dropna())
    names = pd.concat(
        [c[c["nconst"].isin(person_ids)] for c in _tsv_chunks("name.basics.tsv.gz", usecols=["nconst", "primaryName"])],
        ignore_index=True,
    )

    report.add(STEP, "imdb_filter", title_basics_rows=n_basics, movies_kept=len(movies), min_start_year=IMDB_MIN_YEAR)
    for name, df in [("movies", movies), ("principals", principals), ("names", names)]:
        df.to_parquet(INTERIM_DIR / f"imdb_{name}.parquet", index=False, compression="zstd")


def load_imdb(report: Report, from_raw: bool) -> dict[str, pd.DataFrame]:
    """Filtered IMDb tables, rebuilt from the raw TSVs when `from_raw` or when the cache is missing."""
    names = ["movies", "principals", "names"]
    if from_raw or not all((INTERIM_DIR / f"imdb_{n}.parquet").exists() for n in names):
        build_imdb_cache(report)
    imdb = {n: pd.read_parquet(INTERIM_DIR / f"imdb_{n}.parquet") for n in names}

    profile_table(
        report,
        STEP,
        "imdb_movies",
        imdb["movies"],
        grain=f"1 IMDb movie (titleType=movie, isAdult=0, startYear>={IMDB_MIN_YEAR})",
        key=["tconst"],
        na_columns=["primaryTitle", "startYear", "runtimeMinutes", "genres", "averageRating", "numVotes", "directors"],
    )
    profile_table(
        report,
        STEP,
        "imdb_principals",
        imdb["principals"],
        grain="1 credited person x movie (billing position)",
        key=["tconst", "ordering"],
        na_columns=["nconst", "category"],
    )
    profile_table(
        report,
        STEP,
        "imdb_names",
        imdb["names"],
        grain="1 IMDb person",
        key=["nconst"],
        na_columns=["primaryName"],
    )
    return imdb


# ---------------------------------------------------------------------------
# TMDb
# ---------------------------------------------------------------------------

TMDB_CACHES = ["tmdb_cache.parquet", "tmdb_universe_cache.parquet"]


def load_tmdb(report: Report) -> pd.DataFrame:
    """All available TMDb caches -> 1 row per tconst (successful fetch preferred over an error row)."""
    frames = []
    for name in TMDB_CACHES:
        path = TMDB_DIR / name
        if path.exists():
            df = pd.read_parquet(path)
            frames.append(df.assign(source=name))
            report.add(STEP, "tmdb_source", file=name, rows=len(df), errors=int(df["error"].notna().sum()))
        else:
            report.add(STEP, "tmdb_source", file=name, rows=0, status="missing")
    tmdb = pd.concat(frames, ignore_index=True)
    rows_before = len(tmdb)
    tmdb = tmdb.sort_values("error", na_position="first", kind="stable").drop_duplicates("tconst")

    for col in ["budget", "revenue"]:
        zeros = int((tmdb[col] == 0).sum())
        tmdb[col] = tmdb[col].where(tmdb[col] > 0).astype("Int64")  # 0 = unknown in TMDb
        report.add(STEP, "zero_to_na", column=col, zeros_set_to_na=zeros)
    tmdb["release_date"] = pd.to_datetime(tmdb["release_date"].replace("", pd.NA), errors="coerce")

    report.add(STEP, "tmdb_dedup", rows_before=rows_before, rows_after=len(tmdb))
    profile_table(
        report,
        STEP,
        "tmdb",
        tmdb,
        grain="1 IMDb tconst looked up on TMDb",
        key=["tconst"],
        na_columns=["tmdb_id", "release_date", "original_language", "budget", "revenue"],
    )
    return tmdb
