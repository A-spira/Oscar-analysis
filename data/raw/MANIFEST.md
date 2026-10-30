# Raw data manifest

Raw inputs collected by the scripts in `src/oscars/collect/`. Files in this folder are never edited by hand.
All times are UTC. Row counts exclude the header line.

## 1. Oscar nominations — Wikipedia

| Field | Value |
|---|---|
| Owner | Wikimedia Foundation / Wikipedia contributors |
| URL | `https://en.wikipedia.org/wiki/{N}th_Academy_Awards` (1st to 98th ceremony) |
| Access method | HTML scraping (requests + BeautifulSoup), identifiable User-Agent, 1 s pause between pages |
| Script | `src/oscars/collect/scrape_wikipedia.py` |
| Extracted | 2026-10-29 19:04–19:06 UTC |
| Filters | Ceremonies 1929–2026 (98 pages); first `wikitable` after the "Awards" / "Winners and nominees" heading |
| Files | `scraping/all_data_oscars.csv`, `scraping/all_data_oscars.json` (same content) |
| Rows | 2,194 (one row per category × ceremony), 7 columns, 119 distinct category labels |
| Reuse terms | Text licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): attribution required, derived datasets must be shared under the same license |

Columns: `ceremony_number`, `year`, `date`, `url`, `category`, `winner`, `nominees` (nominees joined by ` | `).

### 1.1 Oscar nominations — Wikipedia, detailed (one row per nominee)

| Field | Value |
|---|---|
| Owner | Wikimedia Foundation / Wikipedia contributors |
| URL | Same 98 pages as section 1 |
| Access method | Same scraper, parser, User-Agent, session and 1 s pause, run with `--detailed` (`uv run python -m oscars.collect.scrape_wikipedia --detailed`) |
| Script | `src/oscars/collect/scrape_wikipedia.py` |
| Extracted | 2026-10-30 00:10–00:13 UTC (a first run at 00:05 UTC lost 3 pages to timeouts and was replaced) |
| Filters | Ceremonies 1929–2026 (98 pages, 0 fetch errors); same table selection as section 1 |
| Files | `scraping/all_data_oscars_detailed.csv`, `scraping/all_data_oscars_detailed.json` (same content) |
| Rows | 10,737 (one row per nominee; 2,194 winners), 10 columns, 119 distinct category labels |
| Reuse terms | Text licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): attribution required, derived datasets must be shared under the same license |

Columns: `ceremony_number`, `year`, `date`, `url`, `category`, `position` (0 = winner, then nominees in page order), `is_winner`, `nominee` (text before " – ", parsed as in section 1), `details` (text after " – ", e.g. "The Devil Wears Prada as Miranda Priestly"), `film` (person categories only, meaning categories whose label contains Actor, Actress, Director or Directing: the `details` text before " as ", else all of `details`; empty for film categories).

Consistency: for each (year, category), the winner and the ` | `-joined nominees match `all_data_oscars.csv` in all 2,194 groups (0 differences). For acting and directing nominations from 2000 on, 675 of 675 have a non-empty `film`.

## 2. IMDb Non-Commercial Datasets

| Field | Value |
|---|---|
| Owner | IMDb.com, Inc. |
| URL | https://datasets.imdbws.com/ (documentation: https://developer.imdb.com/non-commercial-datasets/) |
| Access method | Direct HTTPS download of gzipped TSV files |
| Script | `src/oscars/collect/imdb_download.py` |
| Extracted | 2026-10-29 19:07–19:11 UTC |
| Filters | None at download (full files). Filtering happens when reading: `titleType == "movie"`, `startYear >= 1995`, `isAdult == 0` |
| Location | `imdb/` — **not versioned** (gitignored, ~1.4 GB) |
| Reuse terms | Personal and non-commercial use only; attribution "Information courtesy of IMDb (https://www.imdb.com). Used with permission." Redistribution of the files is not allowed |

| File | Size | Rows |
|---|---|---|
| `title.basics.tsv.gz` | 229.4 MB | 12,907,853 |
| `title.ratings.tsv.gz` | 8.7 MB | 1,726,786 |
| `title.crew.tsv.gz` | 83.8 MB | 12,907,853 |
| `title.principals.tsv.gz` | 788.9 MB | 102,574,272 |
| `name.basics.tsv.gz` | 312.1 MB | 15,773,333 |

## 3. TMDb API

| Field | Value |
|---|---|
| Owner | The Movie Database (TMDb) |
| URL | https://api.themoviedb.org/3 (`/find/{imdb_id}?external_source=imdb_id`, then `/movie/{tmdb_id}?append_to_response=keywords`) |
| Access method | REST API v3, personal API key read from `.env` (never stored in data or code), ~20 req/s, one retry on HTTP 429 |
| Script | `src/oscars/collect/tmdb_api.py` |
| Extracted | 2026-10-29 19:12–19:28 UTC |
| Filters | IMDb `tconst` of Oscar nominations from 2000 onwards (film categories matched by title, person categories matched by name through `title.principals`; "other" categories excluded) |
| File | `tmdb/tmdb_cache.parquet` (incremental cache, one row per `tconst`) |
| Rows | 1,182 films; 1,166 OK, 16 errors (`not_found_on_tmdb`) — 98.6 % success |
| Reuse terms | [TMDb API Terms of Use](https://www.themoviedb.org/api-terms-of-use): attribution required ("This product uses the TMDb API but is not endorsed or certified by TMDb."), non-commercial use under the free key |

Columns: `tconst`, `tmdb_id`, `overview`, `tagline`, `release_date`, `original_language`, `keywords`,
`production_countries`, `budget`, `revenue`, `tmdb_vote_average`, `tmdb_vote_count`, `error`.

## 4. Frozen IMDb snapshot (access route)

The exact IMDb files used for this analysis (downloaded 2026-10-29) are stored in a restricted Google Drive folder:
https://drive.google.com/drive/folders/15Rz_IXv0CU0vK-8-ibrAYuh-LMXD618J?usp=drive_link

Download the 5 files into `data/raw/imdb/` and verify them with `shasum -a 256 data/raw/imdb/*.tsv.gz`:

| File | sha256 |
|---|---|
| name.basics.tsv.gz | 918058b340241405b9d928b3704f0cc595854491ed829f19918c367619ee0922 |
| title.basics.tsv.gz | 653446629ce2665fde23610570555a0f451ded324ce26e2aaed08ba086b34400 |
| title.crew.tsv.gz | 55c5434f0c5526cba762b8d632e40274c7894aea1d8102c7dbd9d4253203b559 |
| title.principals.tsv.gz | 47cf4230606389da16682dd337e195a4541998c6936b09baf95d1c6f8e1d0439 |
| title.ratings.tsv.gz | 1db8e7365a5ecbedbf4218869a28d85fd7a492b5d5f417162909094e807d55b3 |

A fresh download is also possible with `uv run python -m oscars.collect.imdb_download`, but it returns the current IMDb version, so votes and ratings (and therefore results) can differ slightly.

## 5. TMDb API — eligible universe

| Field | Value |
|---|---|
| Owner | The Movie Database (TMDb) |
| URL / access method | Same as section 3: `/find/{imdb_id}?external_source=imdb_id`, then `/movie/{tmdb_id}?append_to_response=keywords`; key read from `.env`, ~20 req/s, one retry on HTTP 429, flush every 50 films |
| Script | `src/oscars/collect/tmdb_api.py --universe` (thresholds are CLI options: `--min-year`, `--max-year`, `--min-runtime`, `--min-votes`, `--min-rating`, `--exclude-genre`; `--dry-run` only counts) |
| Extracted | 2026-10-29 23:33 – 2026-10-30 00:34 UTC |
| Filters | IMDb `title.basics` × `title.ratings`: `titleType == "movie"`, `isAdult == 0`, `startYear` 1999–2025, `runtimeMinutes >= 60`, `numVotes >= 10,000`, `averageRating >= 5.0`, `genres` not containing "Documentary" → 7,985 films |
| File | `tmdb/tmdb_universe_cache.parquet` (incremental cache, one row per `tconst`; separate from `tmdb_cache.parquet`, which is left untouched) |
| Rows | 7,985 films: 912 copied from `tmdb_cache.parquet`, 7,073 fetched from the API. 7,985 OK, 0 errors — 100 % success (6 transient network timeouts in the first pass were dropped and fetched again). 68.6 % of films have `budget > 0` (TMDb stores unknown budgets as 0) |
| Reuse terms | Same as section 3 ([TMDb API Terms of Use](https://www.themoviedb.org/api-terms-of-use): attribution, non-commercial use) |

Columns: identical to section 3.
