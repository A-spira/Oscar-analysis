"""Scraping des nominations Oscar depuis les pages Wikipedia "Nth Academy Awards".

Logique reprise de l'ancien notebook 00_Scraping.ipynb (projet 2025). Adaptations :
chemins relatifs a la racine du projet, User-Agent identifiable, pause entre requetes,
timeout HTTP.

Sortie : data/raw/scraping/all_data_oscars.csv et .json
(une ligne = une categorie x ceremonie, nommes separes par " | ").

Usage : uv run python -m oscars.collect.scrape_wikipedia [--start 1929] [--end 2026]
"""

import argparse
import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
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


@dataclass
class Award:
    category: str
    winner: str
    nominees: List[str]


def clean_spaces(text: str) -> str:
    """
    Removes whitespace between quotation marks and parentheses when parsing.

    Ie. Parsing results in ( Brazil ), cleaned to (Brazil)
    """

    text = re.sub(r'"\s*([^"]*?)\s*"', r'"\1"', text)
    text = re.sub(r"\(\s*([^)]*?)\s*\)", r"(\1)", text)
    return text


def generate_oscar_urls(start_year: int = 1929, end_year: int = 2026) -> List[Dict]:
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

        urls.append({
            "ceremony_number": ceremony_num,
            "year": year,
            "url": url,
        })

    return urls


class WikipediaScraper:
    """
    Scrapes data from Oscars Wikipedia pages.
    """

    def __init__(self, url: str, session: Optional[requests.Session] = None):
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

    def scrape_ceremony(self) -> Dict:
        """
        Scrape data from a single Oscar ceremony Wikipedia page.
        """

        logger.info(f"Scraping: {self.url}")

        ceremony_data = {
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
                    ceremony_data["awards"].append({
                        "category": award_data.category,
                        "winner": award_data.winner,
                        "nominees": award_data.nominees,
                    })

        except Exception as e:
            logger.error(f"Failed to parse ceremony {self.url}: {e}")

        return ceremony_data

    def get_award_table_rows(self) -> List[BeautifulSoup]:
        """
        Extracts the Awards table and its corresponding rows for subsequent parsing.
        This could be titled differently across different pages.
        """

        possible_titles = ["Awards", "Winners and nominees"]

        for header_tag in ["h3", "h2"]:
            for title in possible_titles:
                header = self.soup.find(header_tag, string=lambda s: s and s.lower().strip() == title.lower())
                if header:
                    table = header.find_next("table", class_="wikitable")
                    if table:
                        return table.find_all("tr")  # tr for rows.

        raise Exception("Awards section not found with expected headers.")

    def get_date(self) -> Optional[str]:
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

    def get_award(self, table_cell) -> str:
        """
        Extracts the award name from a table cell element. Returns "Unknown Award" if no award name is found.
        """

        award_div = table_cell.find("div")
        award_name = award_div.get_text(strip=True) if award_div else "Unknown Award"
        return award_name

    def split_nominee_text(self, nominee_text: str) -> Tuple[str, Optional[str]]:
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
        return [
            clean_spaces(primary.strip()),
            clean_spaces(secondary.strip()) if secondary else None,
        ]

    def get_winner(self, table_cell) -> str:
        """
        Extracts the winner from the Wikipedia Awards table cells - usually bolded inside <ul><li>.
        """

        try:
            ul = table_cell.find("ul")
            main_li = ul.find("li")
            winner_text = main_li.find("b").get_text(separator=" ", strip=True)
            return self.split_nominee_text(winner_text)[0]
        except (AttributeError, TypeError):
            return "Unknown Winner"

    def get_nominees(self, table_cell) -> List[str]:
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


def scrape_all_wiki(start_year: int = 1929, end_year: int = 2026) -> List[Dict]:
    urls = generate_oscar_urls(start_year, end_year)
    all_data = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT})
        for i, entry in enumerate(tqdm(urls, desc="Scraping Oscars Wiki pages")):
            if i > 0:
                time.sleep(REQUEST_PAUSE_S)
            try:
                scraper = WikipediaScraper(entry["url"], session=session)
                data = scraper.scrape_ceremony()
                data["ceremony_number"] = entry["ceremony_number"]
                data["year"] = entry["year"]
                all_data.append(data)
            except Exception as e:
                logger.error(f"Failed to scrape {entry['url']}: {e}")

    return all_data


def to_dataframe(all_data: List[Dict]) -> pd.DataFrame:
    """Aplatit la structure imbriquee : une ligne = une categorie x ceremonie."""
    rows = []
    for ceremony in all_data:
        for award in ceremony.get("awards", []):
            rows.append({
                "ceremony_number": ceremony.get("ceremony_number"),
                "year": ceremony.get("year"),
                "date": ceremony.get("date"),
                "url": ceremony.get("url"),
                "category": award.get("category"),
                "winner": award.get("winner"),
                "nominees": " | ".join(award.get("nominees", [])),
            })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=1929)
    parser.add_argument("--end", type=int, default=2026)
    args = parser.parse_args()

    df_all_data = to_dataframe(scrape_all_wiki(args.start, args.end))

    SCRAPING_DIR.mkdir(parents=True, exist_ok=True)
    df_all_data.to_csv(SCRAPING_DIR / "all_data_oscars.csv", index=False, encoding="utf-8")
    df_all_data.to_json(SCRAPING_DIR / "all_data_oscars.json", orient="records", force_ascii=False, indent=2)

    print(f"Fichiers enregistres dans : {SCRAPING_DIR.relative_to(SCRAPING_DIR.parents[2])}")
    print(f"Lignes: {len(df_all_data)}")


if __name__ == "__main__":
    main()
