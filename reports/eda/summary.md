# EDA summary

Nomination rate = nominated / eligible; lift = segment rate / overall rate of the category; 95 % Wilson CI in brackets. Associations only, not causal effects. Segments with fewer than 30 nominees (`low_n`) and `unknown` segments are excluded below.

## Top 5 reliable lifts — film view

| Category | Segment | Rate [95 % CI] | Overall | Lift | Nominated / n |
|---|---|---|---|---|---|
| Director | `director_prior_noms` = 1 | 10.38 % [7.37-14.43] | 1.69 % | 6.14 | 30 / 289 |
| Picture | `director_prior_noms` = 2+ | 15.15 % [11.33-19.97] | 2.58 % | 5.87 | 40 / 264 |
| Picture | `director_prior_noms` = 1 | 14.19 % [10.63-18.68] | 2.58 % | 5.50 | 41 / 289 |
| Director | `release_month` = 12 | 5.81 % [4.30-7.82] | 1.69 % | 3.44 | 40 / 688 |
| Picture | `release_month` = 12 | 8.87 % [6.96-11.23] | 2.58 % | 3.44 | 61 / 688 |

## Top 5 reliable lifts — talent view

| Category | Segment | Rate [95 % CI] | Overall | Lift | Nominated / n |
|---|---|---|---|---|---|
| Actor | `prior_noms` = 2+ | 3.33 % [2.54-4.35] | 0.26 % | 12.66 | 51 / 1,531 |
| Supporting Actor | `prior_noms` = 2+ | 2.48 % [1.81-3.39] | 0.26 % | 9.43 | 38 / 1,532 |
| Actress | `prior_noms` = 2+ | 4.43 % [3.42-5.72] | 0.48 % | 9.17 | 55 / 1,242 |
| Supporting Actress | `prior_noms` = 2+ | 2.57 % [1.83-3.61] | 0.48 % | 5.33 | 32 / 1,244 |
| Supporting Actress | `release_month` = 12 | 2.30 % [1.77-2.99] | 0.48 % | 4.77 | 55 / 2,388 |

## Data quality for the analysed variables

Figures copied from `data/output/integration_report.json` (same pipeline run as the tables).

**film_universe** — 23,957 rows, key `tconst, year, category` unique: True

| Column | Type | % NA |
|---|---|---|
| `imdb_votes` | Int32 | 0.0 |
| `imdb_rating` | Float32 | 0.0 |
| `runtime_minutes` | Int32 | 0.0 |
| `release_month` | Int8 | 0.01 |
| `budget` | Int64 | 31.45 |
| `original_language` | str | 0.01 |
| `director_n_prior_noms` | int64 | 0.0 |

**talent_universe** — 158,556 rows, key `nconst, tconst, year, category` unique: True

| Column | Type | % NA |
|---|---|---|
| `imdb_votes` | Int32 | 0.0 |
| `imdb_rating` | Float32 | 0.0 |
| `runtime_minutes` | Int32 | 0.0 |
| `release_month` | Int8 | 0.0 |
| `budget` | Int64 | 31.32 |
| `original_language` | str | 0.0 |
| `billing_order` | Int16 | 0.01 |
| `person_name` | str | 0.0 |

**Outliers** (counted, nothing removed). Scope: all parsed IMDb movies (1995+) for runtime and votes, all TMDb rows for budget; the eligible universe itself requires runtime >= 60 and votes >= 10,000.

| Column | Rule | Below | Above | Quantiles |
|---|---|---|---|---|
| imdb runtimeMinutes (movies) | [40, 300] | 383 | 412 | q0=1, q0.01=45, q0.5=89, q0.99=168, q1=51,420 |
| imdb numVotes (movies) | [0, None] | 0 | 0 | q0=5.0, q0.01=6.0, q0.5=71.0, q0.99=100,138.78, q1=3,244,961.0 |
| tmdb budget (USD) | [10000, 500000000] | 12 | 1 | q0=1.0, q0.01=125,000.0, q0.5=20,000,000.0, q0.99=213,250,000.0, q1=658,800,000.0 |

TMDb coverage in `film_universe`: 99.58 % of nominated rows, 100.0 % of non-nominated rows.

TMDb coverage in `talent_universe`: 100.0 % of nominated rows, 100.0 % of non-nominated rows.
