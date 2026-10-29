# Oscar Analysis

Analysis of Academy Award nominations (1929–2026) built from Wikipedia, IMDb and TMDb data.

## Question & scope

TODO

## Data collection

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. Copy `.env.example` to `.env` and set your own
[TMDb API key](https://www.themoviedb.org/settings/api) in `TMDB_API_KEY`.

```bash
uv sync

# 1. Oscar nominations scraped from Wikipedia -> data/raw/scraping/all_data_oscars.{csv,json}
uv run python -m oscars.collect.scrape_wikipedia

# 2. IMDb non-commercial datasets -> data/raw/imdb/*.tsv.gz (~1.4 GB, not versioned)
uv run python -m oscars.collect.imdb_download --list   # show files and sizes only
uv run python -m oscars.collect.imdb_download

# 3. TMDb enrichment (needs steps 1 and 2) -> data/raw/tmdb/tmdb_cache.parquet
uv run python -m oscars.collect.tmdb_api --test tt0457430   # single test call, writes nothing
uv run python -m oscars.collect.tmdb_api                    # full run, incremental cache
```

Sources, extraction dates and reuse terms are documented in [`data/raw/MANIFEST.md`](data/raw/MANIFEST.md).

## Pipeline

TODO

## Tableau

TODO

## Contributions

TODO

## Credits

This repository builds on the 2025 academic project by Jonathan Bouniol, Anna Spira, Robin Levasseur
and Keira Chang (Oscar nomination prediction from IMDb and TMDb data).

This product uses the TMDb API but is not endorsed or certified by TMDb.
Information courtesy of IMDb (https://www.imdb.com), used with permission under the non-commercial license.
Oscar nominations data from Wikipedia, licensed under CC BY-SA 4.0.
