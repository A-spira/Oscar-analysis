"""Scraping des nominations Oscar depuis les pages Wikipedia "Nth Academy Awards".

Logique reprise de l'ancien notebook 00_Scraping.ipynb (projet 2025). Adaptations :
chemins relatifs a la racine du projet, User-Agent identifiable, pause entre requetes,
timeout HTTP.

Sortie : data/raw/scraping/all_data_oscars.csv et .json
(une ligne = une categorie x ceremonie, nommes separes par " | ").

Detailed mode (--detailed): writes data/raw/scraping/all_data_oscars_detailed.csv and .json
instead, one row per nominee, keeping the secondary text after " – " (`details`) and, for
person categories (acting, directing), the film title extracted from it (`film`).

Usage : uv run python -m oscars.collect.scrape_wikipedia [--start 1929] [--end 2026] [--detailed]
"""

import argparse
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag
from tqdm import tqdm

from oscars.paths import SCRAPING_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# User-Agent identifiable, conforme a la politique Wikimedia (pas de faux navigateur)
USER_AGENT = (
    "OscarAnalysisBot/0.1 (academic project; https://github.com/A-spira/Oscar-analysis) "
    f"python-requests/{requests.__version__}"
)
REQUEST_PAUSE_S = 1.0  # pause polie entre deux pages
REQUEST_TIMEOUT_S = 30

# Categories whose primary is a person and whose secondary starts with the film title
# ("Best Actor", "Best Actress in a Supporting Role", "Best Director", "Best Directing", ...)
PERSON_CATEGORY_PATTERN = re.compile(r"\b(actor|actress|director|directing)\b", re.IGNORECASE)
WINNER_MARKERS = "‡†"  # double dagger / dagger templates placed after the winner's name


@dataclass
class Award:
    category: str
    winner: str
    nominees: list[str]


def clean_spaces(text: str) -> str:
    """
    Removes whitespace between quotation marks and parentheses when parsing.

    Ie. Parsing results in ( Brazil ), cleaned to (Brazil)
    """

    text = re.sub(r'"\s*([^"]*?)\s*"', r'"\1"', text)
    text = re.sub(r"\(\s*([^)]*?)\s*\)", r"(\1)", text)
    return text


def generate_oscar_urls(start_year: int = 1929, end_year: int = 2026) -> list[dict]:
    """
    Generate URLs for every Oscar ceremony from start_year to end_year.
    """

    urls = []

    for year in range(start_year, end_year + 1):
        ceremony_num = year - 1928
        if 10 <= ceremony_num % 100 <= 13:
            suffix = "th"
        else:
            last_digit = ceremony_num % 10
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(last_digit, "th")

        url = f"https://en.wikipedia.org/wiki/{ceremony_num}{suffix}_Academy_Awards"

        urls.append(
            {
                "ceremony_number": ceremony_num,
                "year": year,
                "url": url,
            }
        )

    return urls


class WikipediaScraper:
    """
    Scrapes data from Oscars Wikipedia pages.
    """

    def __init__(self, url: str, session: requests.Session | None = None):
        self.url = url
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
        self.session = session
        self.soup = self.get_soup(self.url)

    def get_soup(self, url: str) -> BeautifulSoup:
        """
        Fetches and parses the HTML from the Wikipedia url.
        """

        try:
            response = self.session.get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT_S)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as e:
            logger.error(f"Failed to fetch {self.url}: {e}")
            raise

    def scrape_ceremony(self, detailed: bool = False) -> dict:
        """
        Scrape data from a single Oscar ceremony Wikipedia page.

        With detailed=True, each award also carries "entries": a list of (primary, details)
        pairs, winner first then nominees in page order.
        """

        logger.info(f"Scraping: {self.url}")

        ceremony_data: dict[str, Any] = {
            "url": self.url,
            "date": self.get_date(),
            "awards": [],
        }

        try:
            award_table_rows = self.get_award_table_rows()

            for row in award_table_rows[:]:
                cells = row.find_all("td")
                for cell in cells:
                    award_data = Award(
                        category=self.get_award(cell),
                        winner=self.get_winner(cell),
                        nominees=self.get_nominees(cell),
                    )
                    award_dict: dict[str, Any] = {
                        "category": award_data.category,
                        "winner": award_data.winner,
                        "nominees": award_data.nominees,
                    }
                    if detailed:
                        award_dict["entries"] = self.get_detailed_entries(cell)
                    ceremony_data["awards"].append(award_dict)

        except Exception as e:
            logger.error(f"Failed to parse ceremony {self.url}: {e}")

        return ceremony_data

    def get_award_table_rows(self) -> list[Tag]:
        """
        Extracts the Awards table and its corresponding rows for subsequent parsing.
        This could be titled differently across different pages.
        """

        possible_titles = ["Awards", "Winners and nominees"]

        for header_tag in ["h3", "h2"]:
            for title in possible_titles:
                header = self.soup.find(
                    header_tag, string=lambda s, title=title: s and s.lower().strip() == title.lower()
                )
                if header:
                    table = header.find_next("table", class_="wikitable")
                    if table:
                        return list(table.find_all("tr"))  # tr for rows.

        raise Exception("Awards section not found with expected headers.")

    def get_date(self) -> str | None:
        """
        Extracts the date of the Academy Awards ceremony from the Wikipedia infobox.
        """

        infobox = self.soup.find("table", class_="infobox vevent")
        if not infobox:
            return None

        for row in infobox.find_all("tr"):
            header = row.find("th")  # th for header.
            if header and "Date" in header.text:
                date_cell = row.find("td")
                if date_cell:
                    return date_cell.get_text(strip=True)

        return None

    def get_award(self, table_cell: Tag) -> str:
        """
        Extracts the award name from a table cell element. Returns "Unknown Award" if no award name is found.
        """

        award_div = table_cell.find("div")
        award_name = award_div.get_text(strip=True) if award_div else "Unknown Award"
        return award_name

    def split_nominee_text(self, nominee_text: str) -> tuple[str, str | None]:
        """
        Takes the nominee line item information and returns the primary winner and secondary information.

        Ie. In the 97th Oscars (2025):
        For Best Picture, Anora is the primary and Sean Baker (+ producers) is in the secondary.
        For Best Director, Sean Baker is the primary and Anora is the secondary.
        """

        if " – " in nominee_text:
            primary, secondary = nominee_text.split(" – ", 1)
        else:
            primary, secondary = nominee_text, None
        return (
            clean_spaces(primary.strip()),
            clean_spaces(secondary.strip()) if secondary else None,
        )

    def get_winner(self, table_cell: Tag) -> str:
        """
        Extracts the winner from the Wikipedia Awards table cells - usually bolded inside <ul><li>.
        """

        try:
            ul = table_cell.find("ul")
            main_li = ul.find("li") if isinstance(ul, Tag) else None
            bold = main_li.find("b") if isinstance(main_li, Tag) else None
            if not isinstance(bold, Tag):
                return "Unknown Winner"
            winner_text = bold.get_text(separator=" ", strip=True)
            return self.split_nominee_text(winner_text)[0]
        except (AttributeError, TypeError):
            return "Unknown Winner"

    def get_nominees(self, table_cell: Tag) -> list[str]:
        """
        Extracts the nominees from the Wikipedia Awards table cell.
        Assumes:
            - Winner is in the first <li>
            - Nominees (if any) are in a nested <ul> inside that first <li>
        """
        nominees = []

        try:
            # find all <ul> elements in the cell
            ul_elements = table_cell.find_all("ul")

            if not ul_elements or len(ul_elements) < 1:
                logger.warning("No <ul> found in table_cell: %s", table_cell.get_text(strip=True))
                return []

            # first <ul> contains the winner in <li>, and possibly a nested <ul> with nominees
            first_ul = ul_elements[0]
            first_li = first_ul.find("li")

            if not first_li:
                logger.warning("No <li> found in first <ul>: %s", first_ul)
                return []

            # look for a nested <ul> inside the winner <li> (this contains nominees)
            sub_ul = first_li.find("ul")

            if not sub_ul:
                logger.info("No nominees <ul> found in winner <li>; only winner present.")
                return []

            # Each <li> in sub_ul is a nominee
            nominee_items = sub_ul.find_all("li")
            for nominee in nominee_items:
                nominee_text = nominee.get_text(separator=" ", strip=True)
                parsed_name = self.split_nominee_text(nominee_text)[0]
                nominees.append(parsed_name)

        except Exception as e:
            logger.warning("Unexpected error while extracting nominees: %s", str(e))

        return nominees

    def get_detailed_entries(self, table_cell: Tag) -> list[tuple[str, str | None]]:
        """
        Returns (primary, details) for the winner then each nominee, in page order.

        The primary is parsed exactly as get_winner / get_nominees do. The winner's details are
        read from the whole winner line (outside the nested nominees list), because on some
        pages only the name is bolded and the film follows outside the <b> tag.
        """

        entries: list[tuple[str, str | None]] = [(self.get_winner(table_cell), self.get_winner_details(table_cell))]

        ul = table_cell.find("ul")
        first_li = ul.find("li") if isinstance(ul, Tag) else None
        sub_ul = first_li.find("ul") if isinstance(first_li, Tag) else None
        if not isinstance(sub_ul, Tag):
            return entries

        for nominee in sub_ul.find_all("li"):
            nominee_text = nominee.get_text(separator=" ", strip=True)
            primary, details = self.split_nominee_text(nominee_text)
            entries.append((primary, details))

        return entries

    def get_winner_details(self, table_cell: Tag) -> str | None:
        """
        Extracts the secondary text of the winner line (text after " – "), ignoring the nested
        nominees list and the winner dagger markers.
        """

        ul = table_cell.find("ul")
        first_li = ul.find("li") if isinstance(ul, Tag) else None
        if not isinstance(first_li, Tag):
            return None

        parts = []
        for child in first_li.children:
            if getattr(child, "name", None) == "ul":
                continue
            text = child.get_text(separator=" ", strip=True) if isinstance(child, Tag) else str(child).strip()
            if text:
                parts.append(text)
        line_text = " ".join(parts).strip(WINNER_MARKERS + " ")
        return self.split_nominee_text(line_text)[1]


def extract_film(category: str, details: str | None) -> str:
    """
    Film title for person categories: the details text before " as " (actors) or the whole
    details text (directors). Empty for film categories or when there are no details.
    """

    if not details or not PERSON_CATEGORY_PATTERN.search(category or ""):
        return ""
    film = details.split(" as ", 1)[0]
    return film.strip().strip(WINNER_MARKERS).strip()


def scrape_all_wiki(start_year: int = 1929, end_year: int = 2026, detailed: bool = False) -> list[dict]:
    urls = generate_oscar_urls(start_year, end_year)
    all_data = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        for i, entry in enumerate(tqdm(urls, desc="Scraping Oscars Wiki pages")):
            if i > 0:
                time.sleep(REQUEST_PAUSE_S)
            try:
                scraper = WikipediaScraper(entry["url"], session=session)
                data = scraper.scrape_ceremony(detailed=detailed)
                data["ceremony_number"] = entry["ceremony_number"]
                data["year"] = entry["year"]
                all_data.append(data)
            except Exception as e:
                logger.error(f"Failed to scrape {entry['url']}: {e}")

    return all_data


def to_dataframe(all_data: list[dict]) -> pd.DataFrame:
    """Aplatit la structure imbriquee : une ligne = une categorie x ceremonie."""
    rows = []
    for ceremony in all_data:
        for award in ceremony.get("awards", []):
            rows.append(
                {
                    "ceremony_number": ceremony.get("ceremony_number"),
                    "year": ceremony.get("year"),
                    "date": ceremony.get("date"),
                    "url": ceremony.get("url"),
                    "category": award.get("category"),
                    "winner": award.get("winner"),
                    "nominees": " | ".join(award.get("nominees", [])),
                }
            )
    return pd.DataFrame(rows)


def to_detailed_dataframe(all_data: list[dict]) -> pd.DataFrame:
    """One row per nominee; position 0 = winner, then nominees in page order."""
    rows = []
    for ceremony in all_data:
        for award in ceremony.get("awards", []):
            category = award.get("category")
            for position, (nominee, details) in enumerate(award.get("entries", [])):
                rows.append(
                    {
                        "ceremony_number": ceremony.get("ceremony_number"),
                        "year": ceremony.get("year"),
                        "date": ceremony.get("date"),
                        "url": ceremony.get("url"),
                        "category": category,
                        "position": position,
                        "is_winner": position == 0,
                        "nominee": nominee,
                        "details": details or "",
                        "film": extract_film(category, details),
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=1929)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="write one row per nominee (with details and film) to all_data_oscars_detailed.csv/.json",
    )
    args = parser.parse_args()

    if args.detailed:
        df_detailed = to_detailed_dataframe(scrape_all_wiki(args.start, args.end, detailed=True))
        SCRAPING_DIR.mkdir(parents=True, exist_ok=True)
        df_detailed.to_csv(SCRAPING_DIR / "all_data_oscars_detailed.csv", index=False, encoding="utf-8")
        df_detailed.to_json(
            SCRAPING_DIR / "all_data_oscars_detailed.json", orient="records", force_ascii=False, indent=2
        )
        print(f"Files written to: {SCRAPING_DIR.relative_to(SCRAPING_DIR.parents[2])}")
        print(f"Rows: {len(df_detailed)}")
        return

    df_all_data = to_dataframe(scrape_all_wiki(args.start, args.end))

    SCRAPING_DIR.mkdir(parents=True, exist_ok=True)
    df_all_data.to_csv(SCRAPING_DIR / "all_data_oscars.csv", index=False, encoding="utf-8")
    df_all_data.to_json(SCRAPING_DIR / "all_data_oscars.json", orient="records", force_ascii=False, indent=2)

    print(f"Fichiers enregistres dans : {SCRAPING_DIR.relative_to(SCRAPING_DIR.parents[2])}")
    print(f"Lignes: {len(df_all_data)}")


if __name__ == "__main__":
    main()
