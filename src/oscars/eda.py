"""Exploratory analysis of the two analytical views: nomination rate, lift and n per segment.

Entry point: `uv run python -m oscars.eda`. Reads data/output/{film,talent}_universe.csv and
integration_report.json, writes reports/eda/:

- tables/film_segments.csv, tables/talent_segments.csv : 1 row = category x variable x segment
  (n, nominated, nomination_rate, Wilson 95 % CI, overall_rate, lift, low_n). Tableau reads these.
- figures/*.png : built from those tables only, so figures and CSVs always agree.
- summary.md : top reliable lifts per view and data quality of the analysed variables.

Segments are descriptive associations, not causal effects. No model is fitted.
"""

import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from oscars.build_analytical import GENRES
from oscars.clean import STUDIO_CATEGORIES, TALENT_CATEGORIES
from oscars.metrics import segment_kpis
from oscars.paths import OUTPUT_DIR, PROJECT_ROOT

EDA_DIR = PROJECT_ROOT / "reports" / "eda"
LOW_N_NOMINATED = 30  # segments with fewer nominees are flagged low_n and kept out of the summary
UNKNOWN = "unknown"

# prior record: film view = max prior Directing nominations of the film's director(s);
# talent view = the person's prior acting nominations (both counted on strictly earlier ceremonies)
VIEWS = {
    "film": {"file": "film_universe.csv", "prior": "director_n_prior_noms", "prior_var": "director_prior_noms"},
    "talent": {"file": "talent_universe.csv", "prior": "n_prior_noms", "prior_var": "prior_noms"},
}
GENRE_VARS = [f"is_{g.lower()}" for g in GENRES]
SEGMENT_VARS = ["release_month", "is_q4_release", *GENRE_VARS, "budget_bucket", "original_language", "is_english"]
SHORT = {
    "Best Picture": "Picture",
    "Best Directing": "Director",
    "Best Writing (Original Screenplay)": "Original Screenplay",
    "Best Actor in a Leading Role": "Actor",
    "Best Actress in a Leading Role": "Actress",
    "Best Actor in a Supporting Role": "Supporting Actor",
    "Best Actress in a Supporting Role": "Supporting Actress",
}
ORDER = {
    "release_month": [str(m) for m in range(1, 13)] + [UNKNOWN],
    "budget_bucket": ["<5M", "5-20M", "20-50M", "50-100M", "100M+", UNKNOWN],
    "prior_noms": ["0", "1", "2+", UNKNOWN],
    "director_prior_noms": ["0", "1", "2+", UNKNOWN],
    "is_q4_release": ["True", "False", UNKNOWN],
    "is_english": ["True", "False", UNKNOWN],
}


# ---------------------------------------------------------------------------
# Segments and tables
# ---------------------------------------------------------------------------


def _as_label(s: pd.Series) -> pd.Series:
    """String segment labels; missing values become an explicit 'unknown' segment."""
    out = s.astype("string")
    out = out.str.replace(r"\.0$", "", regex=True)  # months read back as floats (10.0 -> '10')
    return out.fillna(UNKNOWN)


def prior_bucket(n: pd.Series) -> pd.Series:
    """Prior Oscar nominations in strictly earlier ceremonies: '0', '1', '2+' (missing -> unknown)."""
    x = pd.to_numeric(n, errors="coerce")
    out = pd.Series(UNKNOWN, index=n.index, dtype="string")
    out[x == 0] = "0"
    out[x == 1] = "1"
    out[x >= 2] = "2+"
    return out


def load_view(view: str) -> pd.DataFrame:
    """One analytical view with every segment variable as a string label."""
    cfg = VIEWS[view]
    df = pd.read_csv(OUTPUT_DIR / cfg["file"], low_memory=False)
    df["nominated"] = df["nominated"].astype(bool)
    for var in SEGMENT_VARS:
        df[var] = _as_label(df[var])
    df[cfg["prior_var"]] = prior_bucket(df[cfg["prior"]])
    return df


def segment_table(df: pd.DataFrame, view: str) -> pd.DataFrame:
    """Long table: 1 row = category x variable x segment, with KPIs, Wilson CI and the low_n flag."""
    frames = []
    for var in [*SEGMENT_VARS, VIEWS[view]["prior_var"]]:
        k = segment_kpis(df, var).rename(columns={var: "segment"})
        frames.append(k.assign(variable=var))
    t = pd.concat(frames, ignore_index=True)
    t["view"] = view
    t["low_n"] = t["nominated"] < LOW_N_NOMINATED
    cols = ["view", "category", "variable", "segment", "n", "nominated", "nomination_rate", "ci_low", "ci_high"]
    return t[[*cols, "overall_rate", "lift", "low_n"]]


# ---------------------------------------------------------------------------
# Figures (read the segment tables only)
# ---------------------------------------------------------------------------


def _ordered(sub: pd.DataFrame, variable: str) -> pd.DataFrame:
    order = ORDER.get(variable)
    if order is None:
        return sub.sort_values("n", ascending=False)
    rank = {v: i for i, v in enumerate(order)}
    return sub.assign(_r=sub["segment"].map(rank).fillna(len(order))).sort_values("_r").drop(columns="_r")


def plot_segments(
    table: pd.DataFrame,
    variable: str,
    categories: list[str],
    title: str,
    path: Path,
    segments: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> None:
    """Small multiples: one panel per category, nomination rate (%) per segment with 95 % CI,
    n under each bar and the category's overall rate as a dotted line."""
    fig, axes = plt.subplots(1, len(categories), figsize=(4.2 * len(categories), 4.4), squeeze=False)
    for ax, cat in zip(axes[0], categories, strict=True):
        sub = table[(table["category"] == cat) & (table["variable"] == variable) & (table["n"] > 0)]
        if segments is not None:
            sub = sub[sub["segment"].isin(segments)]
        sub = _ordered(sub, variable)
        x = range(len(sub))
        rate = 100 * sub["nomination_rate"]
        err = [rate - 100 * sub["ci_low"], 100 * sub["ci_high"] - rate]
        colors = ["#bbbbbb" if low else "#2b6cb0" for low in sub["low_n"]]
        ax.bar(list(x), rate, color=colors, yerr=err, capsize=3, error_kw={"elinewidth": 0.8})
        base = 100 * float(sub["overall_rate"].iloc[0]) if len(sub) else 0.0
        ax.axhline(base, color="black", linestyle=":", linewidth=1.2, label=f"overall {base:.2f} %")
        ticks = [labels.get(s, s) if labels else s for s in sub["segment"]]
        many = len(sub) > 6  # many bars: one rotated label per bar so the n values do not overlap
        sep = " " if many else "\n"
        ax.set_xticks(
            list(x),
            [f"{t}{sep}n={n:,}" for t, n in zip(ticks, sub["n"], strict=True)],
            fontsize=7,
            rotation=90 if many else 0,
        )
        top = 100 * float(sub["ci_high"].max()) if len(sub) else 1.0
        ax.set_ylim(0, max(top, base) * 1.15)
        ax.set_title(SHORT.get(cat, cat), fontsize=10)
        ax.set_ylabel("Nomination rate (%)")
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle(f"{title}\nbars: rate with 95 % Wilson CI; grey = fewer than {LOW_N_NOMINATED} nominees", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_genres(table: pd.DataFrame, categories: list[str], title: str, path: Path) -> None:
    """Nomination rate of films having each genre (is_<genre> == True), per category."""
    rows = table[table["variable"].isin(GENRE_VARS) & (table["segment"] == "True")].copy()
    rows["segment"] = rows["variable"].str.removeprefix("is_")
    rows["variable"] = "genre"
    plot_segments(rows, "genre", categories, title, path)


def plot_base_rates(tables: dict[str, pd.DataFrame], path: Path) -> None:
    """Overall nomination rate per category (both views), with the eligible n."""
    rows = []
    for t in tables.values():
        for cat, g in t[t["variable"] == "is_q4_release"].groupby("category", sort=False):
            rows.append((SHORT.get(str(cat), str(cat)), 100 * float(g["overall_rate"].iloc[0]), int(g["n"].sum())))
    fig, ax = plt.subplots(figsize=(9, 4.2))
    names, rates, ns = zip(*rows, strict=True)
    ax.barh(list(names), list(rates), color="#2b6cb0")
    for i, (r, n) in enumerate(zip(rates, ns, strict=True)):
        ax.text(r, i, f" {r:.2f} %  (n={n:,})", va="center", fontsize=8)
    ax.set_xlabel("Nomination rate (%) = nominated / eligible")
    ax.set_title("Base nomination rate per category, ceremonies 2000-2026")
    ax.invert_yaxis()
    ax.set_xlim(0, max(rates) * 1.6)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _fmt_row(r: pd.Series) -> str:
    return (
        f"| {SHORT.get(str(r['category']), r['category'])} | `{r['variable']}` = {r['segment']} | "
        f"{100 * r['nomination_rate']:.2f} % [{100 * r['ci_low']:.2f}-{100 * r['ci_high']:.2f}] | "
        f"{100 * r['overall_rate']:.2f} % | {r['lift']:.2f} | {int(r['nominated'])} / {int(r['n']):,} |"
    )


def top_lifts(table: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """The k highest lifts among reliable segments (not low_n, excluding the 'unknown' segments)."""
    ok = table[~table["low_n"] & (table["segment"] != UNKNOWN) & table["lift"].notna()]
    return ok.sort_values("lift", ascending=False).head(k)


def _quality_section(report: dict[str, Any]) -> list[str]:
    checks = report["checks"]
    lines = ["## Data quality for the analysed variables", ""]
    lines += ["Figures copied from `data/output/integration_report.json` (same pipeline run as the tables).", ""]
    for name in ["film_universe", "talent_universe"]:
        t = next(c for c in checks if c["check"] == "table" and c.get("table") == name)
        lines += [f"**{name}** — {t['rows']:,} rows, key `{', '.join(t['key'])}` unique: {t['key_unique']}", ""]
        lines += ["| Column | Type | % NA |", "|---|---|---|"]
        lines += [f"| `{c}` | {t['dtypes'][c]} | {t['na_pct'][c]} |" for c in t["na_pct"]]
        lines.append("")
    lines += [
        "**Outliers** (counted, nothing removed). Scope: all parsed IMDb movies (1995+) for runtime and votes, "
        "all TMDb rows for budget; the eligible universe itself requires runtime >= 60 and votes >= 10,000.",
        "",
        "| Column | Rule | Below | Above | Quantiles |",
    ]
    lines.append("|---|---|---|---|---|")
    for c in checks:
        if c["check"] == "outliers":
            q = ", ".join(f"q{float(k):g}={v:,}" for k, v in c["quantiles"].items())
            lines.append(f"| {c['column']} | {c['rule']} | {c['below']} | {c['above']} | {q} |")
    for c in checks:
        if c["check"] == "universe":
            cov = c["tmdb_coverage_pct"]
            lines.append(
                f"\nTMDb coverage in `{c['table']}`: {cov['nominated']} % of nominated rows, "
                f"{cov['not_nominated']} % of non-nominated rows."
            )
    return lines


def write_summary(tables: dict[str, pd.DataFrame], report: dict[str, Any], path: Path) -> None:
    lines = [
        "# EDA summary",
        "",
        "Nomination rate = nominated / eligible; lift = segment rate / overall rate of the category; "
        "95 % Wilson CI in brackets. Associations only, not causal effects. "
        f"Segments with fewer than {LOW_N_NOMINATED} nominees (`low_n`) and `unknown` segments are excluded below.",
        "",
    ]
    for view, t in tables.items():
        lines += [f"## Top 5 reliable lifts — {view} view", ""]
        lines += [
            "| Category | Segment | Rate [95 % CI] | Overall | Lift | Nominated / n |",
            "|---|---|---|---|---|---|",
        ]
        lines += [_fmt_row(r) for _, r in top_lifts(t).iterrows()]
        lines.append("")
    lines += _quality_section(report)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> dict[str, pd.DataFrame]:
    plt.switch_backend("Agg")  # write files only, never open a window
    tables_dir, figures_dir = EDA_DIR / "tables", EDA_DIR / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    tables = {view: segment_table(load_view(view), view) for view in VIEWS}
    for view, t in tables.items():
        t.to_csv(tables_dir / f"{view}_segments.csv", index=False)

    film, talent = tables["film"], tables["talent"]
    plot_base_rates(tables, figures_dir / "01_base_rates.png")
    plot_segments(
        film,
        "release_month",
        STUDIO_CATEGORIES,
        "Release month (TMDb release date) — studio view",
        figures_dir / "02_release_month_film.png",
        segments=ORDER["release_month"][:12],
    )
    plot_segments(
        talent,
        "is_q4_release",
        TALENT_CATEGORIES,
        "Q4 release (October-December) vs rest of the year — talent view",
        figures_dir / "03_q4_release_talent.png",
        segments=["True", "False"],
        labels={"True": "Q4", "False": "Jan-Sep"},
    )
    plot_genres(film, STUDIO_CATEGORIES, "Films with each genre — studio view", figures_dir / "04_genres_film.png")
    plot_segments(
        film, "budget_bucket", STUDIO_CATEGORIES, "Budget (USD) — studio view", figures_dir / "05_budget_film.png"
    )
    plot_segments(
        talent,
        "prior_noms",
        TALENT_CATEGORIES,
        "Prior Oscar acting nominations (earlier ceremonies only) — talent view",
        figures_dir / "06_prior_noms_talent.png",
    )
    plot_segments(
        film,
        "is_english",
        STUDIO_CATEGORIES,
        "Original language English vs other — studio view",
        figures_dir / "07_english_film.png",
        segments=["True", "False"],
        labels={"True": "English", "False": "other"},
    )

    report = json.loads((OUTPUT_DIR / "integration_report.json").read_text(encoding="utf-8"))
    write_summary(tables, report, EDA_DIR / "summary.md")
    return tables


def main() -> None:
    tables = run()
    for view, t in tables.items():
        print(
            f"{view}: {len(t)} segment rows, {int((~t['low_n']).sum())} reliable -> {EDA_DIR.relative_to(PROJECT_ROOT)}"
        )


if __name__ == "__main__":
    main()
