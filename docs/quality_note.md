# Quality note

## 1. Scope and coverage

Wikipedia (98 ceremonies 1929–2026, 10,737 nominations), IMDb (snapshot 2026-10-29), TMDb API. Study: ceremonies
2000–2026, 7 categories (Picture, Director, Original Screenplay, 4 acting). Film universe: 23,957 rows (7,986
films × 3 categories); talent universe: 158,556 rows (37,086 people, 7,982 films). TMDb covers 100 % of eligible
films and 99.58 % of nominated film rows; budget is missing for 31.45 % of film rows.

## 2. Integration checks

- Grain: film × category × ceremony; actor/actress × film × category × ceremony. Both keys unique.
- Oscar → IMDb match rate 2000+: 81.10 % (strict 2025 rules) → 83.35 % final; 7 study categories 97.64 % →
  100 % (1,016 / 1,016). None of the 9 guarded joins changes the row count.
- 524 nominations remain unmatched, all outside the 7 categories: 286 short films, 127 song titles, 64 titles
  outside the [Y-3, Y] window, 47 titles not in IMDb movies.
- [Collection checks](collection_checks.md): 27 PASS, 1 FAIL (14 films outside the 7 categories lack TMDb data).

## 3. Key decisions

1. Eligible universe: IMDb movies 1999–2025, ≥ 60 min, ≥ 10,000 votes, rating ≥ 5.0, no documentaries:
   410,818 → 7,985 films.
2. Ceremony = release year + 1; nominated films take their actual ceremony: 20 films shifted.
3. Every real nominee outside the universe is re-injected: 2 film rows, 24 talent rows.
4. TMDb budget 0 → NA (0 means unknown): 2,703 budgets.
5. Prior record from strictly earlier ceremonies (no leakage): 285 of 540 talent nominees have ≥ 1.

Others: [`decision_log.md`](decision_log.md).

## 4. What cannot be trusted

- **Votes and ratings are post-hoc** (measured 2026-10-29). Median votes: 350,697 for Best Picture nominees vs
  35,848 for others; the votes threshold favours nominees.
- **Budget is missing unevenly**: NA for 2.3 % of nominated film rows vs 32.0 % of others (talent 7.4 % vs
  31.4 %): the budget result is **not reliable**.
- **TMDb `release_date` = first worldwide release** (festivals): of 269 nominated films, 26 are dated September,
  6 two or more years before their ceremony. Month and Q4 effects are approximate.
- **IMDb has no lead/supporting split**: every actor is a candidate in both; only 47.4 % (actors) and 39.3 %
  (actresses) of supporting nominees are billed 1st or 2nd.
- **`billing_order` NA on 22 rows, all nominees**: do not analyse it.
- **Prior record matched by name**: 83 nominee names have several IMDb ids; homonym risk not measured.
- **Genres overlap**: 90.7 % of biopics are dramas; 37.6 % of biopics vs 27.2 % of others are Q4 releases, so
  Biography and Q4 lifts are not independent.
- **Small segments**: 226 / 291 film and 292 / 374 talent segments have < 30 nominees, including every
  non-English segment (1–15): inconclusive.
- **Associations, not causal effects**: no control for the confounders above.

**What can be trusted**: 100 % match on the 7 categories, unique keys, no row-multiplying join, raw files
identical to the documented snapshot (sha256, row counts), and 27 of 28 collection checks PASS.
