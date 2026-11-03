# Oscar Analysis

Analysis of Academy Award nominations built from Wikipedia, IMDb and TMDb data. Nominations are collected for
every ceremony (1929–2026); the analysis covers the 2000–2026 ceremonies.

## Question & scope

**Decision makers**

1. **Studios**: which films to back with a *For Your Consideration* campaign, months before nominations.
2. **Luxury houses**: which actors and actresses to dress or sign before awards season.

**Question**: among eligible films and talents, which characteristics known about 6 months before
nominations (release month, genre, budget, original language, prior Oscar record) are associated with a
higher nomination rate? Ceremonies 2000–2026, 7 categories:

- **Studio view**: Best Picture, Best Director, Best Original Screenplay (`Best Picture`, `Best Directing`,
  `Best Writing (Original Screenplay)` in the data and in Tableau)
- **Talent view**: Best Actor, Best Actress, Best Supporting Actor, Best Supporting Actress
  (`Best Actor in a Leading Role`, `Best Actress in a Leading Role`, `Best Actor in a Supporting Role`,
  `Best Actress in a Supporting Role`)

**KPIs**

- `nomination_rate = nominated / eligible`
- `lift = segment rate / overall rate of the category`
- always report `n` (number of eligible rows in the segment)

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. The repository already contains the pipeline
outputs (`data/output/`), the EDA reports (`reports/eda/`) and the TMDb caches (`data/raw/tmdb/`), so levels 1
and 2 need no API key and level 1 needs no data download.

1. **Check the code and the results (about 2 min)**

   ```bash
   uv sync
   uv run pytest
   uv run ruff check src tests
   uv run mypy
   uv run python -m oscars.eda   # rebuilds reports/eda/ from data/output/
   ```

   Then open `tableau/oscar_dashboard.twbx` in Tableau Desktop or Tableau Public: the data is embedded in the
   workbook.

2. **Rebuild the tables from the raw data (about 5 min)**: put the IMDb files in `data/raw/imdb/` (see
   [IMDb frozen snapshot](#imdb-frozen-snapshot)), then

   ```bash
   uv run python -m oscars.pipeline --from-raw
   ```

3. **Collect everything again from the internet**: needs a TMDb API key, see [Data collection](#data-collection).

## Documentation

| File | Content |
|---|---|
| [`docs/quality_note.md`](docs/quality_note.md) | One-page quality note: scope, integration checks, key decisions and **what cannot be trusted** |
| [`docs/decision_log.md`](docs/decision_log.md) | Every data-quality decision, with its reason |
| [`docs/collection_checks.md`](docs/collection_checks.md) | Collection checks: expected vs observed, PASS / FAIL / SKIPPED |
| [`data/raw/MANIFEST.md`](data/raw/MANIFEST.md) | Sources, extraction dates, reuse terms and checksums |
| [`reports/eda/summary.md`](reports/eda/summary.md) | Top 5 reliable lifts per view and data quality of the analysed variables |

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

```bash
uv run python -m oscars.collect.verify_collection   # collection checks -> docs/collection_checks.md
```

It checks that we obtained the intended pages, files and entities: 98 ceremonies with no gap or duplicate and the
Best Picture winner re-read live on 3 pages, IMDb files identical to the MANIFEST checksums and row counts, TMDb
caches covering every eligible film, and pipeline figures cited in the docs equal to `integration_report.json`.
Each check reports expected vs observed and PASS / FAIL / SKIPPED; nothing is corrected.

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

## EDA

```bash
uv run python -m oscars.eda   # reads data/output/, writes reports/eda/ (needs the pipeline outputs)
```

| Output | Content |
|---|---|
| `reports/eda/tables/film_segments.csv`, `talent_segments.csv` | 1 row = category × variable × segment: `n`, `nominated`, `nomination_rate`, 95 % Wilson CI (`ci_low`, `ci_high`), `overall_rate`, `lift`, `low_n` (fewer than 30 nominees). Read by Tableau. |
| `reports/eda/figures/*.png` | 7 figures drawn from those tables only (rate with CI, `n` per bar, category rate as a dotted line) |
| `reports/eda/summary.md` | Top 5 reliable lifts per view and data quality of the analysed variables |

Segment variables: release month and Q4 release, main genres, budget bucket, original language and
English, prior Oscar record (0 / 1 / 2+ nominations in earlier ceremonies; director's record in the
studio view). Missing values form an explicit `unknown` segment. These are associations, not causal effects.

## Findings

Nomination rate with 95 % Wilson CI, lift = segment rate / overall rate of the category, and the number of
nominees / eligible rows. Source: `reports/eda/tables/` (ceremonies 2000–2026). Associations, not causal effects.

1. **Studio view — Q4 release.** Films released October–December are nominated about 2 to 2.4 times more
   often. Best Picture: **6.16 % [5.24–7.23], lift 2.39, 138 / 2,240**, against 1.17 % (67 / 5,745) for
   January–September; lift 2.32 for Director and 1.90 for Original Screenplay. December alone reaches
   8.87 % for Best Picture (lift 3.44, n = 688).
   *Limit:* the month comes from the TMDb release date, often the first (festival or limited) release,
   which is not necessarily the release that drives the campaign.
2. **Studio view — biographical films.** Biopics are nominated about 3 times more often. Best Picture:
   **8.23 % [6.35–10.61], lift 3.19, 53 / 644**; Director: 5.12 % [3.67–7.11], lift 3.03, 33 / 644.
   *Limit:* IMDb genres overlap (most biopics are also dramas), so the genre's own effect cannot be
   isolated; Original Screenplay has too few biopic nominees (21) to conclude.
3. **Talent view — prior Oscar record.** Two or more earlier Oscar nominations is the strongest signal.
   Best Actor: **3.33 % [2.54–4.35], lift 12.66, 51 / 1,531**, against 0.11 % with no earlier nomination;
   lift 9.17 for Actress (55 / 1,242), 9.43 for Supporting Actor and 5.33 for Supporting Actress.
   *Limit:* the history is matched by name (homonym risk not measured), and part of the effect reflects
   that previously nominated actors are cast in prestige films; 55 of the 135 Best Actor nominees had no
   earlier nomination.

**Bounded answer.** Among eligible films of the 2000–2026 ceremonies, a Q4 release (lift 1.9–2.4 across
the three studio categories) and a biographical subject (lift about 3 for Picture and Director) are the
clearest pre-nomination film characteristics associated with higher nomination rates, though the two overlap
(37.6 % of biopics vs 27.2 % of other films are Q4 releases). Budget is not a reliable result: about 31 % of
budgets are missing and almost no film without a budget is nominated (3 of 2,512 for Best Picture), so
against films with a known budget only, the Best Picture lift of the 100M+ bucket falls from 2.40 to 1.67
(see `docs/quality_note.md`). Non-English films have too few nominees to conclude. For talent, a record of two or more
earlier Oscar nominations is the strongest association (lift 5.3–12.7) and a Q4 release more than doubles
the rate (lift 2.2–2.6), but these are descriptive associations on IMDb popularity measured after the
fact, not causal effects.

<details>
<summary>Hypotheses for later (not tested)</summary>

1. Is the Q4 effect weaker since streaming platforms grew (test by ceremony decade)?
2. Do biopics released in Q4 combine both effects?
3. Do "prestige" distributors (Searchlight, A24, ...) matter? Distributor is not collected yet.
4. Festival or limited release vs wide release (TMDb release dates per country).
5. Are actors in films whose director was previously nominated nominated more often?

</details>

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

Workbook: [`tableau/oscar_dashboard.twbx`](tableau/oscar_dashboard.twbx), built with Tableau Desktop Public
Edition 2026.2 and saved locally (never published to Tableau Public: the IMDb data cannot be redistributed).
The packaged workbook embeds an extract of each data source, so it opens without the CSVs.

**Dashboards**

- **Dashboard 1 - Studios** helps movie studios decide which films to back with an FYC campaign: it shows how the nomination rate varies with the release period, the genre and the director's earlier nominations (one category at a time, chosen in the dropdown).
- **Dashboard 2 - Luxury houses** gives a similar view for actors and actresses: the nomination rate is much higher for talents already nominated in earlier ceremonies and for films released in October–December. These are associations, not causal effects, and they describe nominations, not wins.


**Data sources** (one Tableau data source per file, no joins; every indicator matches `oscars.metrics`)

| Data source | File | Grain (1 row =) | Used by |
|---|---|---|---|
| Film universe | `data/output/film_universe.csv` | 1 eligible film × studio category × ceremony | Dashboard 1: KPIs, release month, budget, director record, language |
| Film segments | `reports/eda/tables/film_segments.csv` | 1 category × variable × segment, rates and Wilson CI computed in Python | Dashboard 1: genre |
| Talent universe | `data/output/talent_universe.csv` | 1 actor/actress × eligible film × talent category × ceremony | Dashboard 2: KPIs, prior record, release period |

**Calculated fields** (same definition in Film universe and Talent universe unless stated)

| Field | Formula | Purpose |
|---|---|---|
| `Nominee` | `IIF([Nominated], 1, 0)` | `nominated` is boolean; 1/0 so it can be summed |
| `Eligible (n)` | `COUNT([Tconst])` | `n`, eligible rows in the segment |
| `Nominees` | `SUM([Nominee])` | nominated rows in the segment |
| `Nomination rate` | `SUM([Nominee]) / COUNT([Tconst])` | `nomination_rate = nominated / eligible` |
| `Category base rate` | `{FIXED [Category] : SUM([Nominee]) / COUNT([Tconst])}` | rate of the whole category, independent of the chart's split; used as `MIN(...)` for the dotted reference line |
| `Lift` (Talent universe) | `[Nomination rate] / MIN([Category base rate])` | `lift = segment rate / category rate` |
| `Prior record` (Film universe) | `IF [Director N Prior Noms] = 0 THEN "0" ELSEIF [Director N Prior Noms] = 1 THEN "1" ELSE "2+" END` | same buckets as `oscars.eda.prior_bucket` |
| `Prior record (talent)` (Talent universe) | `IF [N Prior Noms] = 0 THEN "0" ELSEIF [N Prior Noms] = 1 THEN "1" ELSE "2+" END` | same buckets, the person's prior acting nominations |
| `In selected category` (Film universe and Film segments) | `[Category] = [Studio Category]` | keeps the category chosen in the `Studio Category` parameter (list: Best Picture, Best Directing, Best Writing (Original Screenplay); default Best Picture) |

The genre view computes nothing in Tableau: it shows `nomination_rate`, `ci_low`, `ci_high`, `n`,
`overall_rate` and `low_n` from `film_segments.csv` as `AVG` (one row per bar).

**Filters that affect results**

- One category at a time, never "All": each film appears once per studio category and each actor in both Lead
  and Supporting, so categories are never aggregated. Sheet titles show the selected category.
  - Dashboard 1: `Studio Category` parameter (dropdown) with the filter `In selected category` = True on every
    Film universe sheet and on the genre sheet. A parameter rather than a shared filter because the dashboard
    reads two data sources: a filter belongs to one source, the parameter is read by both.
  - Dashboard 2: `Category` filter (single-value dropdown, default Best Actor in a Leading Role) applied to all
    Talent universe sheets.
- Studio – month: `Release Month` = Null excluded (1 Best Picture nominee has no TMDb release date). The base
  rate still includes it (`FIXED` is computed before this filter), as in Python.
- Studio – genre: `Variable` in the 10 genre flags (`is_drama` … `is_animation`) and `Segment` = `True`.
- No data source filter and no extract filter.

**Consistency checks** (Tableau must show the figures of `reports/eda/tables/`)

| View | Expected |
|---|---|
| Best Picture base rate | 2.58 % (206 / 7,986) |
| Best Picture, December release | 8.87 % (61 / 688) |
| Best Picture, biopics | 8.23 % [6.35–10.61], n = 644 |
| Best Actor, 2+ prior nominations | 3.33 %, lift 12.66 (51 / 1,531) |
| Best Actor base rate | 0.26 % (135 / 51,303) |

**Refresh or reconnect**

1. Rebuild the outputs: `uv run python -m oscars.pipeline`, then `uv run python -m oscars.eda`.
2. Open the workbook in Tableau Desktop or Tableau Public. For each data source: **Data → *source* → Edit Data
   Source…** and point it to the CSV above if the path changed, then refresh it (**Data → *source* → Refresh**).
3. Check the consistency table above, then **File → Save As… → Tableau Packaged Workbook (.twbx)**.

## Contributions

- **Anna Spira** ([A-spira](https://github.com/A-spira)): Data collection (Wikipedia scraper, IMDb download and TMDb
  API scripts, frozen IMDb snapshot with checksums and MANIFEST); the `oscars` pipeline (clean, integrate,
  build_analytical) with its integration checks; the EDA (segment tables, confidence intervals, figures, findings)
  (PRs #1, #3, #6, #8); review of the Tableau dashboards (#9, addressed in #10).
- **Giovanni Piardi** ([gpiardi](https://github.com/gpiardi)): Tableau workbook (`tableau/oscar_dashboard.twbx`:
  three dashboards, calculated fields, `Studio Category` parameter, reliability colouring, known-budget base rate)
  and the README Tableau section (PRs #9, #10); review of the analysis PRs #6–#8, including recomputing 14
  figures of the quality note for #7.
- **Both**: research question and decision makers, project pitch, pytest tests and Ruff/mypy configuration, quality
  note, decision log and collection checks (PRs #3, #5, #7), oral presentation.

**Workflow.** One branch per task (`feature/uv-pipeline`, `feature/quality-tests`, `feature/eda`,
`feature/quality-note`, `feature/tableau`, `fix/tableau-review`, …), merged into `main` through pull requests.
From the analysis phase on, each part was reviewed by the other partner: Giovanni reviewed and approved #6–#8
on GitHub (for #7 he recomputed 14 figures of the quality note); Anna reviewed the Tableau dashboards after #9
was merged, outside GitHub, and #10 addresses her review point by point (see its description).

## IMDb frozen snapshot

IMDb files are too large for GitHub and cannot be redistributed publicly. The exact files used are in a
restricted Google Drive folder:
https://drive.google.com/drive/folders/15Rz_IXv0CU0vK-8-ibrAYuh-LMXD618J?usp=drive_link

Place them in `data/raw/imdb/` before running the pipeline. Checksums and details: `data/raw/MANIFEST.md`, section 4.

## Credits

- Information courtesy of IMDb (https://www.imdb.com). Used with permission (non-commercial license).
- This product uses the TMDb API but is not endorsed or certified by TMDb.
- Oscar nominations data from Wikipedia, licensed under CC BY-SA 4.0.
