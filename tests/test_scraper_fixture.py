from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from app.scraper import parse_card


class CandidateCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.current = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.current is None:
            if tag == "div" and "details-wrapper" in attributes.get("class", "").split():
                self.current = {"name": "", "profile_url": "", "text": []}
                self.depth = 1
            return

        if tag == "div":
            self.depth += 1
        if tag == "a" and "/v3/preview" in attributes.get("href", ""):
            self.current["name"] = attributes.get("title", "")
            self.current["profile_url"] = attributes["href"]

    def handle_data(self, data):
        if self.current is not None and data.strip():
            self.current["text"].append(data.strip())

    def handle_endtag(self, tag):
        if self.current is None or tag != "div":
            return
        self.depth -= 1
        if self.depth == 0:
            self.cards.append(self.current)
            self.current = None


def test_parse_card_from_anonymized_results_capture():
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "resdex_results_anonymized.html"
    )
    parser = CandidateCardParser()
    parser.feed(fixture.read_text(encoding="utf-8"))

    parsed = [
        parse_card(
            " ".join(card["text"]),
            card["name"],
            urljoin("https://resdex.naukri.com", card["profile_url"]),
        )
        for card in parser.cards
    ]

    assert len(parsed) == 5
    assert [candidate["name"] for candidate in parsed] == [
        "Candidate 01",
        "Candidate 02",
        "Candidate 03",
        "Candidate 04",
        "Candidate 05",
    ]
    assert [candidate["experience"] for candidate in parsed] == [
        "4y 8m",
        "6y 2m",
        "3y 4m",
        "8y 1m",
        "Fresher",
    ]
    assert [candidate["current_company"] for candidate in parsed] == [
        "Company One",
        "Company Two",
        "Company Three",
        "Company Four",
        None,
    ]
    assert [candidate["skills"] for candidate in parsed] == [
        ["Python", "FastAPI", "AWS"],
        ["Java", "Spring", "AWS"],
        ["Python", "Django", "PostgreSQL"],
        ["Python", "Kubernetes", "Docker"],
        ["Python", "Flask", "SQL"],
    ]
    assert parsed[0]["profile_url"] == (
        "https://resdex.naukri.com/v3/preview?uniqId=fixture-01"
    )
