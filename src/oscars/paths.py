"""Project paths, all relative to the repo root (resolved at runtime)."""

from pathlib import Path

# src/oscars/paths.py -> parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SCRAPING_DIR = RAW_DIR / "scraping"
IMDB_DIR = RAW_DIR / "imdb"
TMDB_DIR = RAW_DIR / "tmdb"
INTERIM_DIR = DATA_DIR / "interim"  # parsed IMDb cache, gitignored
OUTPUT_DIR = DATA_DIR / "output"
REFERENCE_DIR = DATA_DIR / "reference"  # hand-curated, versioned (e.g. match overrides)
DOCS_DIR = PROJECT_ROOT / "docs"

ENV_FILE = PROJECT_ROOT / ".env"
