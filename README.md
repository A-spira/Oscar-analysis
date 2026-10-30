# Oscar Analysis

Analysis of Academy Award nominations (1929–2026) built from Wikipedia, IMDb and TMDb data.

## Question & scope

**Decision makers**

1. **Studios**: which films to back with a *For Your Consideration* campaign, months before nominations.
2. **Luxury houses**: which actors and actresses to dress or sign before awards season.

**Question**: among eligible films and talents, which characteristics known about 6 months before
nominations (release month, genre, budget, original language, prior Oscar record) are associated with a
higher nomination rate? Ceremonies 2000–2026, 7 categories:

- **Studio view**: Best Picture, Best Director, Best Original Screenplay
- **Talent view**: Best Actor, Best Actress, Best Supporting Actor, Best Supporting Actress

**KPIs**

- `nomination_rate = nominated / eligible`
- `lift = segment rate / overall rate of the category`
- always report `n` (number of eligible rows in the segment)

## Data collection

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. Create a `.env` file at the project root containing
`TMDB_API_KEY=<your key>` ([TMDb API key](https://www.themoviedb.org/settings/api)); it is gitignored.

```bash
uv sync

# 1. Oscar nominations scraped from Wikipedia -> data/raw/scraping/all_data_oscars.{csv,json}
uv run python -m oscars.collect.scrape_wikipedia
#    same scrape, 1 row per nominee with the film of person nominations -> all_data_oscars_detailed.{csv,json}
uv run python -m oscars.collect.scrape_wikipedia --detailed

# 2. IMDb non-commercial datasets -> data/raw/imdb/*.tsv.gz (~1.4 GB, not versioned)
uv run python -m oscars.collect.imdb_download --list   # show files and sizes only
uv run python -m oscars.collect.imdb_download

# 3. TMDb enrichment (needs steps 1 and 2), incremental caches in data/raw/tmdb/
uv run python -m oscars.collect.tmdb_api --test tt0457430   # single test call, writes nothing
uv run python -m oscars.collect.tmdb_api                    # nominated films -> tmdb_cache.parquet
uv run python -m oscars.collect.tmdb_api --universe         # eligible universe -> tmdb_universe_cache.parquet
```

Sources, extraction dates and reuse terms are documented in [`data/raw/MANIFEST.md`](data/raw/MANIFEST.md).

## Pipeline

Raw data → analytical tables, no network access and no notebook logic:

```bash
uv run python -m oscars.pipeline --from-raw   # full rebuild, re-parses the raw IMDb files (~5 min)
uv run python -m oscars.pipeline              # same, reuses the parsed IMDb cache in data/interim/
```

| Module | Role |
|---|---|
| `src/oscars/clean.py` | Parsing, types, category normalisation, long format (1 row = 1 nominee); IMDb filtered cache; TMDb caches (budget 0 → NA) |
| `src/oscars/integrate.py` | Oscar ↔ IMDb matching (films by title, persons by name + the film named on Wikipedia), TMDb merge, match rates |
| `src/oscars/build_analytical.py` | Eligible universe (`UniverseParams` thresholds), re-injection of all real nominees, descriptive variables |
| `src/oscars/checks.py` | Reusable checks: table grain and key uniqueness, guarded joins (no row multiplication), match rates, outliers |
| `src/oscars/metrics.py` | Study KPIs: `nomination_rate`, `lift`, per-segment table with `n` (NaN when undefined) |
| `src/oscars/pipeline.py` | Entry point |

Outputs in `data/output/`:

| File | Grain (1 row =) |
|---|---|
| `film_universe.csv` | 1 eligible film × studio category × ceremony |
| `talent_universe.csv` | 1 actor/actress × eligible film × talent category × ceremony |
| `unmatched_nominations.csv` | 1 Oscar nomination 2000+ not matched to IMDb, with its probable reason |
| `integration_report.json` | All counts, match rates and quality checks of the run |

Hand-checked match corrections live in `data/reference/match_overrides.csv`; every data-quality decision
is logged in [`docs/decision_log.md`](docs/decision_log.md).

## Code quality

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest
```

| Tool | Scope | Config | Justified exceptions |
|---|---|---|---|
| Ruff (lint) | `src/`, `tests/` | `[tool.ruff]`, `[tool.ruff.lint]` in `pyproject.toml`: rules E, F, I, UP, B, line length 120 | None: no rule ignored, no `noqa` |
| Ruff (format) | `src/`, `tests/` | `[tool.ruff.format]`: double quotes, spaces, LF | None |
| mypy | `src/`, `tests/` (`files` in `[tool.mypy]`) | Python 3.12, `disallow_untyped_defs`, `warn_unused_ignores`, `warn_redundant_casts`, `no_implicit_optional`, `strict_equality`; stubs from `pandas-stubs`, `types-requests`, `types-tqdm` | None: no `ignore_missing_imports`, no per-module override, no `# type: ignore` |
| pytest | `tests/` (`[tool.pytest.ini_options]`) | Hand-built DataFrames, no real data read | — |

The tests protect the business rules: KPI calculation and its undefined cases (`test_metrics.py`),
label normalisation and TMDb unknown budgets (`test_transformations.py`), joins that must keep the
grain (`test_joins.py`) and the no-leakage prior Oscar record (`test_history.py`).

## Tableau

TODO

## Contributions

TODO

## IMDb frozen snapshot

IMDb files are too large for GitHub and cannot be redistributed publicly. The exact files used are in a
restricted Google Drive folder:
https://drive.google.com/drive/folders/15Rz_IXv0CU0vK-8-ibrAYuh-LMXD618J?usp=drive_link

Place them in `data/raw/imdb/` before running the pipeline. Checksums and details: `data/raw/MANIFEST.md`, section 4.

## Credits

- Information courtesy of IMDb (https://www.imdb.com). Used with permission (non-commercial license).
- This product uses the TMDb API but is not endorsed or certified by TMDb.
- Oscar nominations data from Wikipedia, licensed under CC BY-SA 4.0.
