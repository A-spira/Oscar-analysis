"""Pipeline entry point: raw data -> data/output/.

Usage:
  uv run python -m oscars.pipeline --from-raw   # rebuild everything, re-parsing the raw IMDb files
  uv run python -m oscars.pipeline              # same, reusing the parsed IMDb cache in data/interim/
"""

import argparse

from oscars.build_analytical import build
from oscars.checks import Report
from oscars.clean import load_imdb, load_nominations, load_oscars, load_tmdb
from oscars.integrate import integrate
from oscars.paths import OUTPUT_DIR


def run(from_raw: bool) -> Report:
    report = Report()
    nominations = load_nominations(load_oscars(report), report)
    imdb = load_imdb(report, from_raw=from_raw)
    tmdb = load_tmdb(report)
    matched, unmatched = integrate(nominations, imdb, tmdb, report)
    film_universe, talent_universe = build(matched, imdb, tmdb, nominations, report)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    unmatched.to_csv(OUTPUT_DIR / "unmatched_nominations.csv", index=False)
    film_universe.to_csv(OUTPUT_DIR / "film_universe.csv", index=False)
    talent_universe.to_csv(OUTPUT_DIR / "talent_universe.csv", index=False)
    report.write(OUTPUT_DIR / "integration_report.json")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-raw", action="store_true", help="re-parse the raw IMDb files instead of the cache")
    args = parser.parse_args()
    report = run(from_raw=args.from_raw)
    for e in report.entries:
        print(e)


if __name__ == "__main__":
    main()
