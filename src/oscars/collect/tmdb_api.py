"""TMDb enrichment: raw cache data/raw/tmdb/tmdb_cache.parquet.

Logic taken from the old notebook 01_Merge_datasets.ipynb (sections 1, 2, 4.1, 4.2 and 6):
  1. Oscar nominations 2000+ (Wikipedia scrape), normalised categories, long format;
  2. filtered IMDb loading (films 1995+, no adult titles);
  3. minimal Oscar <-> IMDb matching (film title / person name) to get the tconst;
  4. for each tconst: /find/{tconst} then /movie/{tmdb_id}?append_to_response=keywords,
     with an incremental parquet cache (flush every 50 iterations) and retry on 429.
Only the raw TMDb cache is written: no merge or cleaning here.

The API key is read from .env (TMDB_API_KEY) and masked in every error message.

Usage:
  uv run python -m oscars.collect.tmdb_api --test tt0457430   # 1 test call, nothing is written
  uv run python -m oscars.collect.tmdb_api                    # full enrichment (incremental)
  uv run python -m oscars.collect.tmdb_api --universe         # whole eligible universe -> tmdb_universe_cache.parquet
"""

import argparse
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

from oscars.paths import ENV_FILE, IMDB_DIR, SCRAPING_DIR, TMDB_DIR

TMDB_BASE_URL = "https://api.themoviedb.org/3"
RATE_LIMIT_SLEEP = 0.05  # 20 req/s, below the TMDb limit (~50 req/s)
TMDB_CACHE_PATH = TMDB_DIR / "tmdb_cache.parquet"
TMDB_UNIVERSE_CACHE_PATH = TMDB_DIR / "tmdb_universe_cache.parquet"
SAVE_EVERY = 50

# ---------------------------------------------------------------------------
# 1. Oscar nominations (long format)
# ---------------------------------------------------------------------------

# Category normalisation (groups the historical variants)
CATEGORY_MAP = {
    # Acting
    "Best Actor": "Best Actor in a Leading Role",
    "Best Actress": "Best Actress in a Leading Role",
    # Writing
    "Best Writing (Screenplay Written Directly for the Screen)": "Best Writing (Original Screenplay)",
    "Best Writing (Screenplay Based on Material Previously Produced or Published)": "Best Writing (Adapted Screenplay)",
    "Best Writing (Adapted Screenplay)[22]": "Best Writing (Adapted Screenplay)",
    "Best Writing (Adapted Screenplay)[24]": "Best Writing (Adapted Screenplay)",
    "Best  Writing (Screenplay -- Original)": "Best Writing (Original Screenplay)",
    # International film
    "Best Foreign Language Film": "Best International Feature Film",
    "Best Foreign Language Film[14]": "Best International Feature Film",
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
}

# Categories whose nominee is a film (nominee = film title)
FILM_CATEGORIES = {
    "Best Picture",
    "Best Animated Feature Film",
    "Best International Feature Film",
    "Best Documentary Feature Film",
    "Best Documentary Short Film",
    "Best Short Film (Animated)",
    "Best Short Film (Live Action)",
    "Best Motion Picture",
    # Technical categories whose nominee is usually a film
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
    "Best Director",
}

# Mapping between Oscar category and IMDb role in title.principals
OSCAR_TO_IMDB_ROLE = {
    "Best Actor in a Leading Role": {"actor"},
    "Best Actress in a Leading Role": {"actress"},
    "Best Actor in a Supporting Role": {"actor"},
    "Best Actress in a Supporting Role": {"actress"},
    "Best Directing": {"director"},
    "Best Director": {"director"},
}


def explode_oscar_nominations(df: pd.DataFrame) -> pd.DataFrame:
    """Turn the Oscar dataset into long format: one row per nominee."""
    rows = []
    for _, row in df.iterrows():
        base = {
            "ceremony_number": row["ceremony_number"],
            "year": row["year"],
            "category": row["category_clean"],
        }
        # The winner
        rows.append({**base, "nominee": row["winner"].strip(), "winner": True})
        # The other nominees
        if pd.notna(row["nominees"]):
            for nominee in row["nominees"].split("|"):
                nominee = nominee.strip()
                if nominee:
                    rows.append({**base, "nominee": nominee, "winner": False})
    return pd.DataFrame(rows)


def load_oscar_nominations() -> pd.DataFrame:
    """Nominations 2000+, normalised categories, long format, film/person categories only."""
    oscars_raw = pd.read_csv(SCRAPING_DIR / "all_data_oscars.csv")
    oscars = oscars_raw[oscars_raw["year"] >= 2000].copy()
    oscars["category_clean"] = oscars["category"].replace(CATEGORY_MAP)

    oscars_long = explode_oscar_nominations(oscars)
    oscars_long["nominee_type"] = oscars_long["category"].apply(
        lambda c: "film" if c in FILM_CATEGORIES else ("person" if c in PERSON_CATEGORIES else "other")
    )
    return oscars_long[oscars_long["nominee_type"] != "other"].copy()


# ---------------------------------------------------------------------------
# 2. IMDb loading (early filtering, chunked reads)
# ---------------------------------------------------------------------------


def _read_tsv_chunks(name: str, **kwargs: Any) -> "pd.io.parsers.TextFileReader":
    return pd.read_csv(IMDB_DIR / name, sep="\t", na_values="\\N", chunksize=500_000, **kwargs)


def load_imdb_movies() -> pd.DataFrame:
    """title.basics: feature films (movie) 1995+, no adult titles."""
    # Everything as str to avoid parsing errors (some titles contain tabs)
    chunks = _read_tsv_chunks("title.basics.tsv.gz", dtype=str, on_bad_lines="skip")
    movies = pd.concat([c[c["titleType"] == "movie"] for c in chunks], ignore_index=True)

    movies["startYear"] = pd.to_numeric(movies["startYear"], errors="coerce").astype("Int64")
    movies["runtimeMinutes"] = pd.to_numeric(movies["runtimeMinutes"], errors="coerce").astype("Int64")
    movies["isAdult"] = pd.to_numeric(movies["isAdult"], errors="coerce").astype("Int64")

    return movies[(movies["startYear"] >= 1995) & (movies["isAdult"] == 0)].copy()


def load_imdb_principals(movie_ids: set) -> pd.DataFrame:
    """title.principals filtered on our films (job/characters excluded to save memory)."""
    chunks = _read_tsv_chunks(
        "title.principals.tsv.gz",
        usecols=["tconst", "ordering", "nconst", "category"],
        dtype={"category": "category"},
    )
    return pd.concat([c[c["tconst"].isin(movie_ids)] for c in chunks], ignore_index=True)


def load_imdb_names(person_ids: set) -> pd.DataFrame:
    """name.basics filtered on the people present in principals."""
    chunks = _read_tsv_chunks("name.basics.tsv.gz", usecols=["nconst", "primaryName"])
    return pd.concat([c[c["nconst"].isin(person_ids)] for c in chunks], ignore_index=True)


# ---------------------------------------------------------------------------
# 3. Oscar <-> IMDb matching (minimal: only the tconst are kept)
# ---------------------------------------------------------------------------


def norm_title(s: str) -> str:
    # Remove Wikipedia footnotes in square brackets
    s = re.sub(r"\[\d+\]", "", s)
    # Remove the country/language suffixes of international categories
    s = re.sub(r"\s*\([^)]*\)\s+in\s+[A-Za-z\s]+$", "", s, flags=re.I)
    # Remove the producer/director suffixes after a dash
    s = re.sub(r"\s+[-–—]\s+[A-Z][^-–—]*$", "", s)
    # Optional: the old cleanup of a trailing parenthesis, if present
    s = s.split(" (")[0].strip() if " (" in s and s.endswith(")") else s
    return s.strip()


def match_film_to_imdb(title: str, oscar_year: int, lookup: dict[str, list[tuple[str, int]]]) -> str | None:
    """Find the most likely IMDb tconst for the title of an Oscar-nominated film.

    The Oscar ceremony of year N rewards films released in N-1 (sometimes N-2).
    We look for the film with the closest year in the window [N-3, N].
    """
    clean_title = norm_title(title)

    candidates = lookup.get(title, [])
    if not candidates and clean_title != title:
        candidates = lookup.get(clean_title, [])

    if not candidates:
        return None

    # Filter on the time window and take the closest film
    scored = [(tc, yr, abs(yr - (oscar_year - 1))) for tc, yr in candidates if oscar_year - 3 <= yr <= oscar_year]
    if not scored:
        return None

    scored.sort(key=lambda x: x[2])
    return scored[0][0]  # tconst of the best match


def match_person_to_film(
    person_name: str,
    oscar_year: int,
    oscar_category: str,
    name_lookup: dict[str, list[str]],
    film_index: dict[str, list[tuple[str, int, str]]],
) -> tuple[str | None, str | None]:
    """Find the tconst of the film for which a person is nominated at the Oscars.

    1. Find the person's nconst from their name
    2. Among their films, look for the one of the matching year with the right role
    """
    nconst_list = name_lookup.get(person_name, [])
    if not nconst_list:
        return None, None

    expected_roles = OSCAR_TO_IMDB_ROLE.get(oscar_category, set())

    best_match = None
    best_score = float("inf")
    best_nconst = None

    for nconst in nconst_list:
        films = film_index.get(nconst, [])
        for tconst, film_year, role in films:
            if film_year is None:
                continue
            year_diff = abs(film_year - (oscar_year - 1))
            # The film must be in the right time window
            if oscar_year - 3 <= film_year <= oscar_year:
                # Bonus if the role matches
                role_match = 1 if (not expected_roles or role in expected_roles) else 0
                score = year_diff - role_match * 0.5  # Priority to the correct role
                if score < best_score:
                    best_score = score
                    best_match = tconst
                    best_nconst = nconst

    return best_match, best_nconst


def _group_to_lists(df: pd.DataFrame, key: str, values: list) -> dict:
    """{key: [tuple(values), ...]} keeping row order (same as the notebook's groupby().apply)."""
    out: dict = {}
    for row in df[[key, *values]].itertuples(index=False, name=None):
        out.setdefault(row[0], []).append(row[1:] if len(values) > 1 else row[1])
    return out


def matched_tconsts() -> list:
    """Unique tconst of the Oscar nominations 2000+ matched to IMDb (order: films then persons)."""
    oscars = load_oscar_nominations()
    print(f"Oscar nominations 2000+ (film/person)   : {len(oscars)}")

    movies = load_imdb_movies()
    print(f"IMDb films 1995+, no adult titles       : {len(movies):,}")
    principals = load_imdb_principals(set(movies["tconst"]))
    print(f"Principals for these films              : {len(principals):,}")
    names = load_imdb_names(set(principals["nconst"].dropna()))
    print(f"People loaded                           : {len(names):,}")

    title_lookup = _group_to_lists(movies.dropna(subset=["startYear"]), "primaryTitle", ["tconst", "startYear"])
    name_lookup = _group_to_lists(names, "primaryName", ["nconst"])
    principals_with_year = principals.merge(movies[["tconst", "startYear"]], on="tconst", how="inner")
    person_film_index = _group_to_lists(principals_with_year, "nconst", ["tconst", "startYear", "category"])

    oscar_films = oscars[oscars["nominee_type"] == "film"].copy()
    oscar_films["tconst"] = [
        match_film_to_imdb(n, int(y), title_lookup)
        for n, y in zip(oscar_films["nominee"], oscar_films["year"], strict=True)
    ]
    oscar_persons = oscars[oscars["nominee_type"] == "person"].copy()
    oscar_persons["tconst"] = [
        match_person_to_film(n, int(y), c, name_lookup, person_film_index)[0]
        for n, y, c in zip(oscar_persons["nominee"], oscar_persons["year"], oscar_persons["category"], strict=True)
    ]

    for label, part in [("film", oscar_films), ("person", oscar_persons)]:
        n = part["tconst"].notna().sum()
        print(f"Matching categories {label:8s}          : {n}/{len(part)} ({n / len(part):.1%})")

    oscar_all = pd.concat([oscar_films, oscar_persons], ignore_index=True)
    return oscar_all["tconst"].dropna().unique().tolist()


# ---------------------------------------------------------------------------
# 4. TMDb API
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    load_dotenv(ENV_FILE)
    key = os.getenv("TMDB_API_KEY")
    if not key:
        raise SystemExit("TMDB_API_KEY not found in .env")
    return key


def _mask(text: str, api_key: str) -> str:
    """Remove the API key from any text (requests exceptions contain the full URL)."""
    text = re.sub(r"(api_key=)[^&\s]+", r"\1***", text)
    return text.replace(api_key, "***")


def _tmdb_get(session: requests.Session, api_key: str, path: str, **params: str) -> tuple[int | None, dict[str, Any]]:
    """TMDb GET call with a simple retry on 429 (rate limit). Returns (status_code, json)."""
    params["api_key"] = api_key
    status = None
    try:
        r = session.get(f"{TMDB_BASE_URL}{path}", params=params, timeout=10)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 1)))
            r = session.get(f"{TMDB_BASE_URL}{path}", params=params, timeout=10)
        status = r.status_code
        r.raise_for_status()
        return status, r.json()
    except requests.RequestException as e:
        return status, {"_error": _mask(str(e), api_key)}


def fetch_tmdb(session: requests.Session, api_key: str, tconst: str) -> dict[str, Any]:
    """Fetch overview, keywords, budget, etc. for an IMDb tconst (2 TMDb calls)."""
    # Step 1: resolve tconst -> tmdb_id
    _, find = _tmdb_get(session, api_key, f"/find/{tconst}", external_source="imdb_id")
    if "_error" in find:
        return {"tconst": tconst, "error": find["_error"]}

    movies = find.get("movie_results", [])
    if not movies:
        return {"tconst": tconst, "error": "not_found_on_tmdb"}

    tmdb_id = movies[0]["id"]
    time.sleep(RATE_LIMIT_SLEEP)

    # Step 2: details + keywords in 1 call (append_to_response)
    _, detail = _tmdb_get(session, api_key, f"/movie/{tmdb_id}", append_to_response="keywords")
    if "_error" in detail:
        return {"tconst": tconst, "tmdb_id": tmdb_id, "error": detail["_error"]}

    return {
        "tconst": tconst,
        "tmdb_id": tmdb_id,
        "overview": detail.get("overview"),
        "tagline": detail.get("tagline"),
        "release_date": detail.get("release_date"),
        "original_language": detail.get("original_language"),
        "keywords": [k["name"] for k in detail.get("keywords", {}).get("keywords", [])],
        "production_countries": [c["iso_3166_1"] for c in detail.get("production_countries", [])],
        "budget": detail.get("budget"),
        "revenue": detail.get("revenue"),
        "tmdb_vote_average": detail.get("vote_average"),
        "tmdb_vote_count": detail.get("vote_count"),
        "error": None,
    }


def test_call(api_key: str, tconst: str) -> None:
    """Test call on a single film: HTTP codes, title, budget, number of keywords. Writes nothing."""
    with requests.Session() as session:
        status, find = _tmdb_get(session, api_key, f"/find/{tconst}", external_source="imdb_id")
        print(f"/find/{tconst}      -> HTTP {status}")
        if "_error" in find or not find.get("movie_results"):
            print(f"Failed: {find.get('_error', 'not_found_on_tmdb')}")
            return
        tmdb_id = find["movie_results"][0]["id"]
        status, detail = _tmdb_get(session, api_key, f"/movie/{tmdb_id}", append_to_response="keywords")
        print(f"/movie/{tmdb_id}         -> HTTP {status}")
        if "_error" in detail:
            print(f"Failed: {detail['_error']}")
            return
        print(f"Title    : {detail.get('title')} ({detail.get('original_title')})")
        print(f"Budget   : {detail.get('budget')}")
        print(f"Keywords : {len(detail.get('keywords', {}).get('keywords', []))}")


def enrich(api_key: str) -> None:
    # Load the existing cache (resume after an interruption / instant reruns)
    if TMDB_CACHE_PATH.exists():
        cache_df = pd.read_parquet(TMDB_CACHE_PATH)
        print(f"Existing cache found: {len(cache_df)} films already fetched")
    else:
        cache_df = pd.DataFrame()
        print("No existing cache, starting from scratch")

    # Unique tconst to fetch (not in the cache)
    all_tconsts = matched_tconsts()
    already_fetched = set(cache_df["tconst"]) if not cache_df.empty else set()
    to_fetch = [t for t in all_tconsts if t not in already_fetched]

    print(f"Unique films in total        : {len(all_tconsts)}")
    print(f"Already in the cache         : {len(already_fetched)}")
    print(f"To fetch from TMDb           : {len(to_fetch)}")
    print(f"Time estimate (~20 req/s)    : ~{len(to_fetch) * 2 * RATE_LIMIT_SLEEP:.0f}s")

    # Incremental fetch with periodic saves (resilient to interruptions)
    TMDB_DIR.mkdir(parents=True, exist_ok=True)
    new_records = []
    if to_fetch:
        with requests.Session() as session:
            for i, tconst in enumerate(to_fetch, 1):
                rec = fetch_tmdb(session, api_key, tconst)
                new_records.append(rec)
                time.sleep(RATE_LIMIT_SLEEP)

                if i % SAVE_EVERY == 0 or i == len(to_fetch):
                    # Flush to disk every SAVE_EVERY iterations
                    partial_df = pd.concat([cache_df, pd.DataFrame(new_records)], ignore_index=True)
                    partial_df.to_parquet(TMDB_CACHE_PATH, index=False)
                    err_count = pd.DataFrame(new_records)["error"].notna().sum()
                    print(f"  [{i:4d}/{len(to_fetch)}] flush -> cache: {len(partial_df)} | errors: {err_count}")

    tmdb_df = pd.read_parquet(TMDB_CACHE_PATH)
    n_errors = tmdb_df["error"].notna().sum()
    print(f"\nTotal films in the TMDb cache  : {len(tmdb_df)}")
    print(f"Errors                         : {n_errors} ({100 * n_errors / len(tmdb_df):.1f}%)")
    print(f"Success                        : {100 * (1 - n_errors / len(tmdb_df)):.1f}%")
    if n_errors > 0:
        print("\nError types:")
        print(tmdb_df[tmdb_df["error"].notna()]["error"].value_counts().to_string())


# ---------------------------------------------------------------------------
# 5. Eligible universe (all films that could have been nominated)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseFilters:
    """Thresholds defining the eligible film universe (IMDb basics + ratings)."""

    min_year: int = 1999
    max_year: int = 2025
    min_runtime: int = 60
    min_votes: int = 10_000
    min_rating: float = 5.0
    exclude_genre: str = "Documentary"


def load_universe_tconsts(filters: UniverseFilters) -> list[str]:
    """tconst of the eligible universe, read in chunks with the same parsing as `load_imdb_movies`."""
    basics_parts: list[pd.DataFrame] = []
    for chunk in _read_tsv_chunks("title.basics.tsv.gz", dtype=str, on_bad_lines="skip"):
        chunk = chunk[chunk["titleType"] == "movie"]
        year = pd.to_numeric(chunk["startYear"], errors="coerce")
        runtime = pd.to_numeric(chunk["runtimeMinutes"], errors="coerce")
        is_adult = pd.to_numeric(chunk["isAdult"], errors="coerce")
        no_excluded_genre = ~chunk["genres"].fillna("").str.contains(filters.exclude_genre, regex=False)
        keep = (
            (is_adult == 0)
            & year.between(filters.min_year, filters.max_year)
            & (runtime >= filters.min_runtime)
            & no_excluded_genre
        )
        basics_parts.append(chunk.loc[keep, ["tconst"]])
    basics = pd.concat(basics_parts, ignore_index=True)

    ratings = pd.read_csv(IMDB_DIR / "title.ratings.tsv.gz", sep="\t", na_values="\\N")
    ratings = ratings[(ratings["numVotes"] >= filters.min_votes) & (ratings["averageRating"] >= filters.min_rating)]

    universe = basics.merge(ratings[["tconst"]], on="tconst", how="inner")
    return universe["tconst"].drop_duplicates().sort_values().tolist()


def _print_summary(df: pd.DataFrame) -> None:
    n_errors = int(df["error"].notna().sum())
    print(f"\nTotal films in cache : {len(df)}")
    print(f"Errors               : {n_errors} ({100 * n_errors / len(df):.1f}%)")
    print(f"Success              : {100 * (1 - n_errors / len(df)):.1f}%")
    if n_errors > 0:
        print("\nErrors by type:")
        print(df[df["error"].notna()]["error"].value_counts().to_string())


def enrich_universe(api_key: str, filters: UniverseFilters) -> None:
    """Enrich every film of the eligible universe into TMDB_UNIVERSE_CACHE_PATH.

    Rows already in the nominee cache (tmdb_cache.parquet, read-only here) are copied instead of re-fetched.
    The universe cache is incremental: rerunning only fetches tconst missing from it.
    """
    universe = load_universe_tconsts(filters)
    universe_set = set(universe)
    print(f"Eligible universe ({filters}) : {len(universe):,} films")

    if TMDB_UNIVERSE_CACHE_PATH.exists():
        cache_df = pd.read_parquet(TMDB_UNIVERSE_CACHE_PATH)
        print(f"Existing universe cache        : {len(cache_df)} rows")
    else:
        cache_df = pd.DataFrame()

    already = set(cache_df["tconst"]) if not cache_df.empty else set()
    if TMDB_CACHE_PATH.exists():
        nominee_cache = pd.read_parquet(TMDB_CACHE_PATH)
        reused = nominee_cache[nominee_cache["tconst"].isin(universe_set - already)]
        print(f"Reused from nominee cache      : {len(reused)} rows")
        if not reused.empty:
            cache_df = reused.copy() if cache_df.empty else pd.concat([cache_df, reused], ignore_index=True)
            already |= set(reused["tconst"])

    to_fetch = [t for t in universe if t not in already]
    print(f"To fetch from TMDb             : {len(to_fetch)}")
    print(f"Time estimate (~20 req/s)      : ~{len(to_fetch) * 2 * RATE_LIMIT_SLEEP:.0f}s")

    TMDB_DIR.mkdir(parents=True, exist_ok=True)
    cache_df.to_parquet(TMDB_UNIVERSE_CACHE_PATH, index=False)
    new_records: list[dict] = []
    if to_fetch:
        with requests.Session() as session:
            for i, tconst in enumerate(to_fetch, 1):
                new_records.append(fetch_tmdb(session, api_key, tconst))
                time.sleep(RATE_LIMIT_SLEEP)

                if i % SAVE_EVERY == 0 or i == len(to_fetch):
                    partial_df = pd.concat([cache_df, pd.DataFrame(new_records)], ignore_index=True)
                    partial_df.to_parquet(TMDB_UNIVERSE_CACHE_PATH, index=False)
                    err_count = pd.DataFrame(new_records)["error"].notna().sum()
                    print(f"  [{i:5d}/{len(to_fetch)}] flush -> cache: {len(partial_df)} | errors: {err_count}")

    tmdb_df = pd.read_parquet(TMDB_UNIVERSE_CACHE_PATH)
    tmdb_df = tmdb_df[tmdb_df["tconst"].isin(universe_set)]
    _print_summary(tmdb_df)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test", metavar="TCONST", help="a single test call, does not write the cache")
    parser.add_argument("--universe", action="store_true", help="enrich the whole eligible universe (new cache)")
    parser.add_argument("--dry-run", action="store_true", help="with --universe: only count films, no API call")
    defaults = UniverseFilters()
    parser.add_argument("--min-year", type=int, default=defaults.min_year)
    parser.add_argument("--max-year", type=int, default=defaults.max_year)
    parser.add_argument("--min-runtime", type=int, default=defaults.min_runtime, help="minutes")
    parser.add_argument("--min-votes", type=int, default=defaults.min_votes, help="IMDb numVotes")
    parser.add_argument("--min-rating", type=float, default=defaults.min_rating, help="IMDb averageRating")
    parser.add_argument("--exclude-genre", default=defaults.exclude_genre)
    args = parser.parse_args()

    if args.universe:
        filters = UniverseFilters(
            min_year=args.min_year,
            max_year=args.max_year,
            min_runtime=args.min_runtime,
            min_votes=args.min_votes,
            min_rating=args.min_rating,
            exclude_genre=args.exclude_genre,
        )
        if args.dry_run:
            print(f"Eligible universe ({filters}) : {len(load_universe_tconsts(filters)):,} films")
            return
        enrich_universe(load_api_key(), filters)
        return

    api_key = load_api_key()
    if args.test:
        test_call(api_key, args.test)
    else:
        enrich(api_key)


if __name__ == "__main__":
    main()
