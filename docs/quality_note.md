# Quality note

## 1. Scope and coverage

Wikipedia (98 ceremonies 1929–2026, 10,737 nominations), IMDb (snapshot 2026-10-29), TMDb API. Study: ceremonies
2000–2026, 7 categories (Picture, Director, Original Screenplay, 4 acting). Film universe: 7,986 films, 23,957
film × category rows (Best Directing has one film fewer); talent universe: 158,556 rows (37,086 people, 7,982
films). TMDb: 100 % of eligible films, 99.58 % of nominated film rows; budget NA on 31.45 % of film rows.

## 2. Integration checks

- Grain: film × category × ceremony; actor/actress × film × category × ceremony. Both keys unique.
- Match rate 2000+: 81.10 % (strict 2025 rules) → 83.35 %; 7 categories: 97.64 % → 100 % (1,016 / 1,016).
  The 9 guarded joins keep row counts.
- 524 unmatched, all outside the 7 categories: 286 short films, 127 song titles, 64 outside the [Y-3, Y]
  window, 47 not in IMDb movies.
- [Collection checks](collection_checks.md): 27 PASS, 1 FAIL (14 films outside the 7 categories lack TMDb data).

## 3. Key decisions

1. Eligible universe: IMDb movies 1999–2025, ≥ 60 min, ≥ 10,000 votes, rating ≥ 5.0, no documentaries:
   410,818 → 7,985 films.
2. Ceremony = release year + 1; nominated films take their actual ceremony: 20 films shifted.
3. Nominees outside the universe are re-injected: 2 film rows, 24 talent rows.
4. TMDb budget 0 → NA (0 means unknown): 2,703 budgets.
5. Prior record from strictly earlier ceremonies (no leakage): 285 of 540 talent nominees have ≥ 1.

Others: [`decision_log.md`](decision_log.md).

## 4. What cannot be trusted

- **Votes and ratings are post-hoc** (2026-10-29): median 350,697 votes for Best Picture nominees vs 35,848;
  the threshold favours nominees.
- **Budget is missing unevenly**: NA for 2.3 % of nominated film rows vs 32.0 % of others (talent 7.4 % vs
  31.4 %): the budget result is **not reliable**.
- **TMDb `release_date` = first worldwide release** (festivals): 26 of 269 nominated films dated September,
  6 ≥ 2 years before their ceremony; month and Q4 effects approximate.
- **IMDb has no lead/supporting split**: every actor competes in both; only 47.4 % / 39.3 % of supporting
  actor / actress nominees are billed 1st–2nd.
- **`billing_order` NA on 22 rows, all nominees** (of 23 re-injected outside IMDb's cast; decision 24): do not
  analyse.
- **Prior record matched by name**: 83 nominee names have several IMDb ids; homonym risk not measured.
- **Genres overlap**: 90.7 % of biopics are dramas; 37.6 % of biopics vs 27.2 % of others released in Q4:
  Biography and Q4 lifts are not independent.
- **Small segments**: 226 / 291 film and 292 / 374 talent segments have < 30 nominees, including every
  non-English segment (1–15): inconclusive.
- **Best Picture field size changed**: 5 nominees/year in 2000–2009, 10 in 2010–2011, then 8–10: the base rate
  mixes two regimes.
- **Top-5 lifts are optimistic**: best of 147 reliable segments (65 film + 82 talent), no multiple-comparison
  correction.
- **Associations, not causal effects**: confounders above are not controlled.

**What can be trusted**: 100 % match on the 7 categories, unique keys, no row-multiplying join, raw files
identical to the documented snapshot (sha256, row counts), and 27 of 28 collection checks PASS.
