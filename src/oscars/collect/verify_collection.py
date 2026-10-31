"""Collection checks: did we get the intended pages, files, dates and entities?

Each check states what is expected, what is observed and a status (PASS / FAIL / SKIPPED). Nothing is
corrected: the script only reads the raw data, the pipeline outputs and the docs, then writes
`docs/collection_checks.md` (and `docs/tmdb_errors.csv`) and prints a summary.

Network: 3 Wikipedia pages only (Best Picture cross-check), same identifiable User-Agent as the scraper,
1 s pause between requests. If the network fails, those checks are SKIPPED.

Usage: uv run python -m oscars.collect.verify_collection
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag

from oscars.checks import Report
from oscars.clean import (
    STUDIO_CATEGORIES,
    TALENT_CATEGORIES,
    load_imdb,
    load_nominations,
    load_oscars,
    normalize_categories,
)
from oscars.collect.scrape_wikipedia import REQUEST_PAUSE_S, REQUEST_TIMEOUT_S, USER_AGENT
from oscars.integrate import match_nominations, norm_key
from oscars.paths import DOCS_DIR, IMDB_DIR, INTERIM_DIR, OUTPUT_DIR, PROJECT_ROOT, RAW_DIR, SCRAPING_DIR, TMDB_DIR

Status = Literal["PASS", "FAIL", "SKIPPED"]

# Expected values, as documented in data/raw/MANIFEST.md (sections 1, 3 and 5)
FIRST_CEREMONY, LAST_CEREMONY = 1, 98
FIRST_YEAR, LAST_YEAR = 1929, 2026
WIKI_ROWS, WIKI_COLUMNS = 2194, 7
TMDB_EXPECTED = {"tmdb_cache.parquet": (1182, 16), "tmdb_universe_cache.parquet": (7985, 0)}
IMDB_FILES = [
    "title.basics.tsv.gz",
    "title.ratings.tsv.gz",
    "title.crew.tsv.gz",
    "title.principals.tsv.gz",
    "name.basics.tsv.gz",
]
WITNESS_TCONST = "tt0457430"  # Pan's Labyrinth, also the TMDb test call of the README
WITNESS_FILES = ["title.basics.tsv.gz", "title.ratings.tsv.gz"]
ONLINE_CEREMONIES = [2007, 2020, 2026]

CHECKS_MD = DOCS_DIR / "collection_checks.md"
TMDB_ERRORS_CSV = DOCS_DIR / "tmdb_errors.csv"


@dataclass
class Check:
    source: str
    name: str
    expected: str
    observed: str
    status: Status


def _status(ok: bool) -> Status:
    return "PASS" if ok else "FAIL"


def _fmt(n: float) -> str:
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


# ---------------------------------------------------------------------------
# 1. Wikipedia
# ---------------------------------------------------------------------------


@dataclass
class SequenceIssues:
    missing: list[int]
    duplicated: list[int]
    out_of_range: list[int]

    @property
    def ok(self) -> bool:
        return not (self.missing or self.duplicated or self.out_of_range)


def sequence_issues(values: Iterable[int], first: int, last: int) -> SequenceIssues:
    """Gaps, duplicates and out-of-range values of a sequence that must be exactly first..last, once each."""
    counts = Counter(values)
    return SequenceIssues(
        missing=[v for v in range(first, last + 1) if v not in counts],
        duplicated=sorted(v for v, c in counts.items() if c > 1),
        out_of_range=sorted(v for v in counts if not first <= v <= last),
    )


def _describe(issues: SequenceIssues) -> str:
    if issues.ok:
        return "no gap, no duplicate"
    return f"missing {issues.missing}, duplicated {issues.duplicated}, out of range {issues.out_of_range}"


def best_picture_from_infobox(html: str) -> str | None:
    """Best Picture winner as written in the ceremony infobox (independent of the award table we scrape)."""
    box = BeautifulSoup(html, "html.parser").find("table", class_="infobox")
    if not isinstance(box, Tag):
        return None
    for row in box.find_all("tr"):
        th, td = row.find("th"), row.find("td")
        if isinstance(th, Tag) and isinstance(td, Tag) and th.get_text(" ", strip=True) == "Best Picture":
            return td.get_text(" ", strip=True)
    return None


def check_wikipedia(online: bool) -> list[Check]:
    src = "Wikipedia"
    raw = pd.read_csv(SCRAPING_DIR / "all_data_oscars.csv", dtype={"winner": "string", "nominees": "string"})
    ceremonies = raw[["ceremony_number", "year"]].drop_duplicates()
    by_number = sequence_issues(ceremonies["ceremony_number"], FIRST_CEREMONY, LAST_CEREMONY)
    by_year = sequence_issues(ceremonies["year"], FIRST_YEAR, LAST_YEAR)
    offset_ok = bool((ceremonies["year"] - ceremonies["ceremony_number"] == FIRST_YEAR - FIRST_CEREMONY).all())
    per_ceremony = raw.groupby("ceremony_number").size()
    empty = raw["winner"].fillna("").str.strip().eq("") & raw["nominees"].fillna("").str.strip().eq("")

    checks = [
        Check(
            src,
            "Ceremonies",
            f"{LAST_CEREMONY} ceremonies, numbers {FIRST_CEREMONY}-{LAST_CEREMONY}, years {FIRST_YEAR}-{LAST_YEAR}, "
            "year = number + 1928",
            f"{len(ceremonies)} ceremonies; numbers: {_describe(by_number)}; years: {_describe(by_year)}; "
            f"year = number + 1928: {'yes' if offset_ok else 'no'}",
            _status(len(ceremonies) == LAST_CEREMONY and by_number.ok and by_year.ok and offset_ok),
        ),
        Check(
            src,
            "all_data_oscars.csv shape",
            f"{_fmt(WIKI_ROWS)} rows x {WIKI_COLUMNS} columns",
            f"{_fmt(len(raw))} rows x {raw.shape[1]} columns",
            _status(raw.shape == (WIKI_ROWS, WIKI_COLUMNS)),
        ),
        Check(
            src,
            "Categories per ceremony",
            "every ceremony has >= 1 category",
            f"min {per_ceremony.min()}, max {per_ceremony.max()} categories per ceremony",
            _status(int(per_ceremony.min()) >= 1 and len(per_ceremony) == LAST_CEREMONY),
        ),
        Check(
            src,
            "Empty rows",
            "0 rows with neither winner nor nominees",
            f"{int(empty.sum())} rows",
            _status(not empty.any()),
        ),
    ]

    # Same comparison as the pipeline (clean.load_nominations), reported here instead of asserted
    report = Report()
    try:
        load_nominations(load_oscars(report), report)
    except AssertionError:
        pass
    consistency = next(e for e in report.entries if e["check"] == "detailed_scrape_consistency")
    checks.append(
        Check(
            src,
            "all_data_oscars.csv vs all_data_oscars_detailed.csv",
            "same (year, category, nominee, winner) rows, 0 differing keys",
            f"{_fmt(consistency['rows_from_csv'])} vs {_fmt(consistency['rows_detailed'])} rows, "
            f"{consistency['differing_keys']} differing keys",
            _status(
                consistency["differing_keys"] == 0 and consistency["rows_from_csv"] == consistency["rows_detailed"]
            ),
        )
    )

    raw["category_clean"] = normalize_categories(raw["category"])
    best_picture = raw[raw["category_clean"] == "Best Picture"].set_index("year")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for i, year in enumerate(ONLINE_CEREMONIES):
        csv_winner = str(best_picture.loc[year, "winner"])
        name = f"Online cross-check {year}: Best Picture"
        expected = f"infobox winner on the live page = CSV winner ({csv_winner})"
        if not online:
            checks.append(Check(src, name, expected, "network disabled (--offline)", "SKIPPED"))
            continue
        if i:
            time.sleep(REQUEST_PAUSE_S)
        try:
            resp = session.get(str(best_picture.loc[year, "url"]), timeout=REQUEST_TIMEOUT_S)
            resp.raise_for_status()
        except requests.RequestException as exc:
            checks.append(Check(src, name, expected, f"network error ({type(exc).__name__})", "SKIPPED"))
            continue
        live = best_picture_from_infobox(resp.text)
        if live is None:
            checks.append(Check(src, name, expected, "no Best Picture row in the infobox", "FAIL"))
            continue
        checks.append(
            Check(src, name, expected, f"live infobox: {live}", _status(norm_key(live) == norm_key(csv_winner)))
        )
    return checks


# ---------------------------------------------------------------------------
# 2. IMDb
# ---------------------------------------------------------------------------


def manifest_imdb() -> tuple[dict[str, str], dict[str, int]]:
    """sha256 (section 4) and row counts (section 2) of the IMDb files, read from data/raw/MANIFEST.md."""
    text = (RAW_DIR / "MANIFEST.md").read_text(encoding="utf-8")
    sha = {m[1]: m[2] for m in re.finditer(r"^\| `?([\w.]+\.tsv\.gz)`? \| ([0-9a-f]{64}) \|", text, re.M)}
    rows = {
        m[1]: int(m[2].replace(",", ""))
        for m in re.finditer(r"^\| `([\w.]+\.tsv\.gz)` \| [\d.]+ MB \| ([\d,]+) \|", text, re.M)
    }
    return sha, rows


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def scan_gzip(path: Path, first_field: str | None = None) -> tuple[int, bool]:
    """(data rows excluding the header, whether a line starts with `first_field` + tab)."""
    needle = f"\n{first_field}\t".encode() if first_field else None
    newlines, found, tail = 0, False, b""
    with gzip.open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            newlines += chunk.count(b"\n")
            if needle and not found:
                found = needle in tail + chunk
                tail = chunk[-len(needle) :]
    return newlines - 1, found


def check_imdb() -> list[Check]:
    src = "IMDb"
    present = [name for name in IMDB_FILES if (IMDB_DIR / name).exists()]
    checks = [
        Check(
            src,
            "Files present",
            f"{len(IMDB_FILES)} files in data/raw/imdb/",
            f"{len(present)} present"
            + ("" if len(present) == len(IMDB_FILES) else f", missing {set(IMDB_FILES) - set(present)}"),
            _status(len(present) == len(IMDB_FILES)),
        )
    ]
    sha_ref, rows_ref = manifest_imdb()
    for name in IMDB_FILES:
        path = IMDB_DIR / name
        if not path.exists():
            checks.append(Check(src, f"{name}", "file present", "missing", "SKIPPED"))
            continue
        digest = sha256_of(path)
        checks.append(
            Check(
                src,
                f"{name} sha256",
                f"MANIFEST section 4: {sha_ref.get(name, 'not documented')[:12]}…",
                f"{digest[:12]}…",
                _status(sha_ref.get(name) == digest),
            )
        )
        witness = WITNESS_TCONST if name in WITNESS_FILES else None
        n_rows, found = scan_gzip(path, witness)
        checks.append(
            Check(
                src,
                f"{name} rows",
                f"MANIFEST section 2: {_fmt(rows_ref[name]) if name in rows_ref else 'not documented'}",
                _fmt(n_rows),
                _status(rows_ref.get(name) == n_rows),
            )
        )
        if witness:
            checks.append(
                Check(
                    src,
                    f"{name} witness film",
                    f"{WITNESS_TCONST} (Pan's Labyrinth) present",
                    "present" if found else "absent",
                    _status(found),
                )
            )
    return checks


# ---------------------------------------------------------------------------
# 3. TMDb
# ---------------------------------------------------------------------------


def matched_nominations() -> pd.DataFrame | None:
    """(category, nominee, tconst) of the 2000+ nominations matched to IMDb, recomputed with the pipeline
    matching (no file written).

    Needs the parsed IMDb cache in data/interim/ (built by the pipeline); None when it is missing.
    """
    if not all((INTERIM_DIR / f"imdb_{n}.parquet").exists() for n in ["movies", "principals", "names"]):
        return None
    report = Report()
    nominations = load_nominations(load_oscars(report), report)
    noms = match_nominations(nominations, load_imdb(report, from_raw=False), report)
    return noms.loc[noms["tconst"].notna(), ["category", "nominee", "tconst", "match_status"]]


def check_tmdb() -> tuple[list[Check], pd.DataFrame]:
    src = "TMDb"
    checks: list[Check] = []
    caches = {name: pd.read_parquet(TMDB_DIR / name) for name in TMDB_EXPECTED}
    for name, (exp_rows, exp_errors) in TMDB_EXPECTED.items():
        df = caches[name]
        n_errors, dups = int(df["error"].notna().sum()), int(df["tconst"].duplicated().sum())
        checks.append(
            Check(
                src,
                name,
                f"{_fmt(exp_rows)} rows, unique tconst, {exp_errors} errors",
                f"{_fmt(len(df))} rows, {dups} duplicated tconst, {n_errors} errors",
                _status(len(df) == exp_rows and dups == 0 and n_errors == exp_errors),
            )
        )

    cached = set(pd.concat([c["tconst"] for c in caches.values()]))
    usecols = ["tconst", "in_eligible_universe"]
    universe = pd.concat(
        [pd.read_csv(OUTPUT_DIR / f, usecols=usecols) for f in ["film_universe.csv", "talent_universe.csv"]]
    )
    eligible = set(universe.loc[universe["in_eligible_universe"], "tconst"])
    missing = sorted(eligible - cached)
    checks.append(
        Check(
            src,
            "Eligible universe covered",
            "every eligible tconst (data/output universes) is in one of the 2 caches",
            f"{_fmt(len(eligible) - len(missing))} / {_fmt(len(eligible))} present"
            + (f"; missing: {', '.join(missing[:10])}" if missing else ""),
            _status(not missing),
        )
    )

    matched = matched_nominations()
    study = STUDIO_CATEGORIES + TALENT_CATEGORIES
    scopes: dict[str, list[str] | None] = {"7 study categories": study, "all categories": None}
    for scope, categories in scopes.items():
        name = f"Matched nominations covered ({scope})"
        expected = f"every tconst of a matched 2000+ nomination ({scope}) is in one of the 2 caches"
        if matched is None:
            checks.append(Check(src, name, expected, "IMDb cache missing in data/interim/", "SKIPPED"))
            continue
        in_scope = matched if categories is None else matched[matched["category"].isin(categories)]
        tconsts = set(in_scope["tconst"])
        absent = in_scope[~in_scope["tconst"].isin(cached)]
        observed = f"{_fmt(len(tconsts) - absent['tconst'].nunique())} / {_fmt(len(tconsts))} tconst present"
        if len(absent):
            statuses = ", ".join(f"{k} {v}" for k, v in absent["match_status"].value_counts().items())
            observed += (
                f"; {absent['tconst'].nunique()} missing ({len(absent)} nominations, match status: {statuses}; "
                f"{int(absent['category'].isin(study).sum())} in the 7 study categories)"
                f": {', '.join(sorted(absent['tconst'].unique()))}"
            )
        checks.append(Check(src, name, expected, observed, _status(absent.empty)))

    errors = pd.concat([c[c["error"].notna()][["tconst", "error"]] for c in caches.values()])
    titles = pd.read_parquet(INTERIM_DIR / "imdb_movies.parquet", columns=["tconst", "primaryTitle"])
    errors = errors.merge(titles, on="tconst", how="left", validate="one_to_one")
    errors = errors.rename(columns={"primaryTitle": "imdb_title", "error": "reason"})[
        ["tconst", "imdb_title", "reason"]
    ]
    return checks, errors


# ---------------------------------------------------------------------------
# 4. Pipeline figures cited in the docs
# ---------------------------------------------------------------------------


def _entry(report: list[dict[str, Any]], check: str, **match: Any) -> dict[str, Any]:
    return next(e for e in report if e["check"] == check and all(e.get(k) == v for k, v in match.items()))


def _rate(report: list[dict[str, Any]], scope: str) -> float:
    return float(_entry(report, "match_rate", scope=scope)["match_rate_pct"])


def _category_rows(report: list[dict[str, Any]], category: str) -> float:
    by_category = _entry(report, "universe", table="film_universe")["by_category"]
    return float(next(c["rows"] for c in by_category if c["category"] == category))


Observed = Callable[[list[dict[str, Any]]], list[float | tuple[float, ...]]]

# (document, cited text, figures of integration_report.json in the order they appear in the cited text).
# A tuple means "every value must equal the cited figure" (e.g. one figure cited for 3 categories).
CLAIMS: list[tuple[str, str, Observed]] = [
    (
        "README.md",
        "7 categories",
        lambda r: [float(sum(e["check"] == "match_rate" and e["scope"].startswith("category:") for e in r) / 2)],
    ),
    (
        "docs/decision_log.md",
        "119 → 114 labels",
        lambda r: [
            _entry(r, "category_normalisation")["labels_raw"],
            _entry(r, "category_normalisation")["labels_without_footnotes"],
        ],
    ),
    (
        "docs/decision_log.md",
        "114 → 92 labels",
        lambda r: [
            _entry(r, "category_normalisation")["labels_without_footnotes"],
            _entry(r, "category_normalisation")["labels_clean"],
        ],
    ),
    (
        "docs/decision_log.md",
        "8 duplicate keys kept",
        lambda r: [_entry(r, "table", table="nominations")["duplicate_keys"]],
    ),
    ("docs/decision_log.md", "26 duplicate keys", lambda r: [_entry(r, "table", table="oscars_raw")["duplicate_keys"]]),
    (
        "docs/decision_log.md",
        "12,907,853 → 410,818 titles",
        lambda r: [_entry(r, "imdb_filter")["title_basics_rows"], _entry(r, "imdb_filter")["movies_kept"]],
    ),
    (
        "docs/decision_log.md",
        "budget: 2,703 zeros → NA; revenue: 2,066",
        lambda r: [
            _entry(r, "zero_to_na", column="budget")["zeros_set_to_na"],
            _entry(r, "zero_to_na", column="revenue")["zeros_set_to_na"],
        ],
    ),
    (
        "docs/decision_log.md",
        "9,167 → 8,255 rows",
        lambda r: [_entry(r, "tmdb_dedup")["rows_before"], _entry(r, "tmdb_dedup")["rows_after"]],
    ),
    (
        "docs/decision_log.md",
        "films 76.59 %, persons 97.63 %, total 81.10 %",
        lambda r: [
            _rate(r, "films (all film categories, 2000+) [strict 2025]"),
            _rate(r, "persons (all person categories, 2000+) [strict 2025]"),
            _rate(r, "total (2000+) [strict 2025]"),
        ],
    ),
    ("docs/decision_log.md", "+57 matches", lambda r: [_entry(r, "match_status")["counts"]["matched_normalized"]]),
    (
        "docs/decision_log.md",
        "4 after decision 23; 7 study categories 97.64 % → 100 %",
        lambda r: [
            _entry(r, "overrides")["applied"],
            23,
            7,
            _rate(r, "7 study categories [strict 2025]"),
            _rate(r, "7 study categories [final]"),
        ],
    ),
    (
        "docs/decision_log.md",
        "strict total 81.10 %, final 83.35 %",
        lambda r: [_rate(r, "total (2000+) [strict 2025]"), _rate(r, "total (2000+) [final]")],
    ),
    (
        "docs/decision_log.md",
        "7,986 films for Best Picture and Original Screenplay, 7,985 for Best Directing",
        lambda r: [
            (_category_rows(r, "Best Picture"), _category_rows(r, "Best Writing (Original Screenplay)")),
            _category_rows(r, "Best Directing"),
        ],
    ),
    (
        "docs/decision_log.md",
        "20 films shifted; 0 film nominated in several ceremonies",
        lambda r: [
            _entry(r, "ceremony_alignment")["films_shifted_to_actual_ceremony"],
            _entry(r, "ceremony_alignment")["films_nominated_in_several_ceremonies"],
        ],
    ),
    (
        "docs/decision_log.md",
        "film view: 2 rows (votes + rating); talent view: 24 rows (23 not in IMDb's top-billed cast, 1 votes)",
        lambda r: [
            _entry(r, "universe", table="film_universe")["reinjected"],
            _entry(r, "universe", table="talent_universe")["reinjected"],
            _entry(r, "universe", table="talent_universe")["reinjection_reasons"]["not_in_imdb_cast_list_or_role"],
            _entry(r, "universe", table="talent_universe")["reinjection_reasons"]["votes"],
        ],
    ),
    (
        "docs/decision_log.md",
        "81,407 → 79,266 cast rows",
        lambda r: [_entry(r, "cast_dedup")["rows_before"], _entry(r, "cast_dedup")["rows_after"]],
    ),
    (
        "docs/decision_log.md",
        "10,737 = 10,737 rows, 0 differing keys; 675/675",
        lambda r: [
            _entry(r, "detailed_scrape_consistency")["rows_detailed"],
            _entry(r, "detailed_scrape_consistency")["rows_from_csv"],
            _entry(r, "detailed_scrape_consistency")["differing_keys"],
            _entry(r, "detailed_scrape_consistency")["with_film"],
            _entry(r, "detailed_scrape_consistency")["person_nominations_2000plus"],
        ],
    ),
    (
        "docs/decision_log.md",
        "647 name+film, 22 film-only; 244 of 659 (37.03 %)",
        lambda r: [
            _entry(r, "match_status")["counts"]["matched_name_and_film"],
            _entry(r, "match_status")["counts"]["matched_film_only"],
            _entry(r, "person_film_vs_2025_rule")["different_film"],
            _entry(r, "person_film_vs_2025_rule")["compared"],
            _entry(r, "person_film_vs_2025_rule")["different_film_pct"],
        ],
    ),
    ("docs/decision_log.md", "4 overrides left", lambda r: [_entry(r, "overrides")["applied"]]),
    (
        "docs/decision_log.md",
        "23 rows; `billing_order` NA",
        lambda r: [
            _entry(r, "universe", table="talent_universe")["reinjection_reasons"]["not_in_imdb_cast_list_or_role"]
        ],
    ),
]


def _equal(cited: str, observed: float) -> bool:
    decimals = len(cited.split(".")[1]) if "." in cited else 0
    return round(float(observed), decimals) == float(cited.replace(",", ""))


def compare_claim(text: str, observed: list[float | tuple[float, ...]]) -> list[tuple[str, str]]:
    """(cited, observed) pairs that differ; observed values are compared at the precision of the cited text."""
    cited = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if len(cited) != len(observed):
        return [(text, f"{len(observed)} report figures for {len(cited)} cited numbers")]
    diffs = []
    for c, o in zip(cited, observed, strict=True):
        values = o if isinstance(o, tuple) else (o,)
        if not all(_equal(c, v) for v in values):
            diffs.append((c, " / ".join(_fmt(v) for v in values)))
    return diffs


def check_pipeline_claims() -> tuple[list[Check], list[dict[str, str]]]:
    report: list[dict[str, Any]] = json.loads((OUTPUT_DIR / "integration_report.json").read_text(encoding="utf-8"))[
        "checks"
    ]
    rows: list[dict[str, str]] = []
    for doc, text, observed in CLAIMS:
        found = text in (PROJECT_ROOT / doc).read_text(encoding="utf-8")
        diffs = compare_claim(text, observed(report)) if found else [(text, "cited text not found in the document")]
        rows.append(
            {
                "document": doc,
                "cited": text,
                "report": "; ".join(
                    _fmt(v) if not isinstance(v, tuple) else " / ".join(_fmt(x) for x in v) for v in observed(report)
                ),
                "differences": "; ".join(f"{c} → {o}" for c, o in diffs),
                "status": _status(not diffs),
            }
        )
    checks = []
    for doc in dict.fromkeys(d for d, _, _ in CLAIMS):
        doc_rows = [r for r in rows if r["document"] == doc]
        bad = [r for r in doc_rows if r["status"] == "FAIL"]
        checks.append(
            Check(
                "Pipeline",
                f"Figures cited in {doc}",
                "every cited figure = data/output/integration_report.json",
                f"{len(doc_rows) - len(bad)} / {len(doc_rows)} cited texts match" + (" (see details)" if bad else ""),
                _status(not bad),
            )
        )
    return checks, rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_markdown(checks: list[Check], claims: list[dict[str, str]], n_errors: int, run_at: str) -> None:
    counts = Counter(c.status for c in checks)
    lines = [
        "# Collection checks",
        "",
        f"Generated by `uv run python -m oscars.collect.verify_collection` on {run_at} UTC. "
        "Do not edit by hand: re-run the command.",
        "",
        "Each check compares what the collection was meant to produce (documented in `data/raw/MANIFEST.md`) "
        "with what is on disk. Nothing is corrected by this script.",
        "",
        f"**Summary: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIPPED']} SKIPPED.**",
        "",
        "| # | Source | Check | Expected | Observed | Status |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {i} | {c.source} | {_md_cell(c.name)} | {_md_cell(c.expected)} | {_md_cell(c.observed)} | **{c.status}** |"
        for i, c in enumerate(checks, 1)
    ]
    lines += [
        "",
        "## Pipeline figures cited in the docs",
        "",
        "Every figure of `README.md` and `docs/decision_log.md` that comes from "
        "`data/output/integration_report.json`, compared at the precision written in the document.",
        "",
        "| Document | Cited text | Report value(s) | Differences (cited → report) | Status |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['document']} | {_md_cell(r['cited'])} | {_md_cell(r['report'])} | {_md_cell(r['differences']) or '—'} "
        f"| **{r['status']}** |"
        for r in claims
    ]
    lines += [
        "",
        "## TMDb lookups in error",
        "",
        f"{n_errors} tconst returned an error when looked up on TMDb; the list (tconst, IMDb title, reason) is in "
        "[`tmdb_errors.csv`](tmdb_errors.csv).",
        "",
    ]
    CHECKS_MD.write_text("\n".join(lines), encoding="utf-8")


def run(online: bool = True) -> list[Check]:
    run_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    checks = check_wikipedia(online)
    checks += check_imdb()
    tmdb_checks, errors = check_tmdb()
    checks += tmdb_checks
    claim_checks, claims = check_pipeline_claims()
    checks += claim_checks

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    errors.to_csv(TMDB_ERRORS_CSV, index=False)
    write_markdown(checks, claims, len(errors), run_at)
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip the 3 Wikipedia requests (checks SKIPPED)")
    checks = run(online=not parser.parse_args().offline)
    for c in checks:
        print(f"[{c.status:7}] {c.source:9} {c.name}: {c.observed}")
    counts = Counter(c.status for c in checks)
    print(f"\n{counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIPPED']} SKIPPED -> {CHECKS_MD}")
    sys.exit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
