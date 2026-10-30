"""Telechargement des IMDb Non-Commercial Datasets (.tsv.gz) dans data/raw/imdb/.

Source : https://datasets.imdbws.com/ (conditions : https://developer.imdb.com/non-commercial-datasets/,
usage personnel et non commercial uniquement). Le dossier data/raw/imdb/ est gitignore.

On ne telecharge que les fichiers utilises par l'ancien notebook 01_Merge_datasets.ipynb.
Les fichiers sont gardes compresses : pandas les lit directement (compression inferee).

Usage :
  uv run python -m oscars.collect.imdb_download --list   # tailles seulement, rien n'est telecharge
  uv run python -m oscars.collect.imdb_download          # telecharge (saute les fichiers deja complets)
"""

import argparse

import requests
from tqdm import tqdm

from oscars.paths import IMDB_DIR

BASE_URL = "https://datasets.imdbws.com/"
FILES = [
    "title.basics.tsv.gz",
    "title.ratings.tsv.gz",
    "title.crew.tsv.gz",
    "title.principals.tsv.gz",
    "name.basics.tsv.gz",
]
USER_AGENT = "OscarAnalysisBot/0.1 (academic project; https://github.com/A-spira/Oscar-analysis)"
CHUNK_SIZE = 1 << 20


def remote_size(session: requests.Session, name: str) -> int:
    r = session.head(BASE_URL + name, allow_redirects=True, timeout=30)
    r.raise_for_status()
    return int(r.headers["Content-Length"])


def download(session: requests.Session, name: str) -> None:
    dest = IMDB_DIR / name
    expected = remote_size(session, name)
    if dest.exists() and dest.stat().st_size == expected:
        print(f"{name}: deja present ({expected / 1e6:.1f} Mo), ignore")
        return

    # Ecriture dans un .part puis renommage : pas de fichier tronque en cas d'interruption
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(BASE_URL + name, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f, tqdm(total=expected, unit="B", unit_scale=True, desc=name) as bar:
            for chunk in r.iter_content(CHUNK_SIZE):
                f.write(chunk)
                bar.update(len(chunk))
    if tmp.stat().st_size != expected:
        raise OSError(f"{name}: taille {tmp.stat().st_size} != {expected} attendue")
    tmp.replace(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="affiche les fichiers et leur taille sans telecharger")
    args = parser.parse_args()

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        if args.list:
            total = 0
            for name in FILES:
                size = remote_size(session, name)
                total += size
                print(f"{name:28s} {size / 1e6:9.1f} Mo")
            print(f"{'TOTAL':28s} {total / 1e6:9.1f} Mo")
            return

        IMDB_DIR.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            download(session, name)

    for name in FILES:
        print(f"{name:28s} {(IMDB_DIR / name).stat().st_size / 1e6:9.1f} Mo")


if __name__ == "__main__":
    main()
