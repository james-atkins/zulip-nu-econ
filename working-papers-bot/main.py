#!/usr/bin/env python3

import argparse
import os
import sys
import time

from urllib.parse import urldefrag, urljoin
from typing import Iterator, NamedTuple, Optional, Any

import jinja2
import requests
import zulip

from bs4 import BeautifulSoup

TIMEOUT = 5
SLEEP_TIME = 0.1
TOPIC = "working papers"

STREAM_SEARCH_TERMS: dict[str, list[tuple[str, str]]] = {
    "field/appliedmicro": [],
    "field/development": [
        ("programs", "Development Economics"),
        ("topic", "Development and Growth"),
        ("topic", "Development"),
    ],
    "field/finance": [
        ("programs", "Asset Pricing"),
        ("programs", "Corporate Finance"),
        ("groups", "Behavioral Finance"),
        ("groups", "Household Finance"),
        ("topics", "Financial Markets"),
        ("topics", "Financial Institutions"),
        ("topics", "Corporate Finance"),
        ("topics", "Behavioral Finance"),
        ("topics", "Portfolio Selection and Asset Pricing"),
    ],
    "field/health": [
        ("programs", "Economics of Health"),
        ("topics", "Health"),
    ],
    "field/history": [
        ("programs", "Development of the American Economy"),
        ("topics", "Macroeconomic History"),
        ("topics", "Financial History"),
        ("topics", "Labor and Health History"),
        ("topics", "Other History"),
    ],
    "field/io": [
        ("programs", "Industrial Organization"),
        ("topics", "Industrial Organization"),
        ("topics", "Market Structure and Firm Performance"),
        ("topics", "Firm Behavior"),
        ("topics", "Nonprofits"),
        ("topics", "Antitrust"),
        ("topics", "Regulatory Economics"),
        ("topics", "Industry Studies"),
    ],
    "field/labor": [
        ("programs", "Labor Studies"),
        ("groups", "Personnel Economics"),
        ("topics", "Labor Economics"),
        ("topics", "Demography and Aging"),
        ("topics", "Labor Supply and Demand"),
        ("topics", "Labor Compensation"),
        ("topics", "Labor Market Structures"),
        ("topics", "Labor Relations"),
        ("topics", "Unemployment and Immigration"),
        ("topics", "Labor Discrimination"),
    ],
    "field/macro": [
        ("programs", "International Finance and Macroeconomics"),
        ("programs", "Monetary Economics"),
        ("programs", "Economic Fluctuations and Growth"),
        ("programs", "International Finance and Macroeconomics"),
        ("topics", "Macroeconomics"),
        ("topics", "Macroeconomic Models"),
        ("topics", "Consumption and Investment"),
        ("topics", "Business Cycles"),
        ("topics", "Money and Interest Rates"),
        ("topics", "Monetary Policy"),
        ("topics", "Fiscal Policy"),
    ],
    "field/metrics": [
        ("topics", "Econometrics"),
        ("topics", "Estimation Methods"),
    ],
    "field/microtheory": [
        ("groups", "Market Design"),
    ],
    "field/organizational": [
        ("groups", "Organizational Economics"),
    ],
    "field/political": [
        ("programs", "Political Economy"),
        ("programs", "Law and Economics"),
        ("topics", "Law and Economics"),
    ],
    "field/public": [
        ("programs", "Public Economics"),
        ("groups", "Economics of Crime"),
        ("topics", "Public Economics"),
        ("topics", "Taxation"),
        ("topics", "Public Goods"),
        ("topics", "National Fiscal Issues"),
        ("topics", "Subnational Fiscal Issues"),
    ],
}


class Author(NamedTuple):
    name: str
    url: Optional[str]


class WorkingPaper(NamedTuple):
    url: str
    title: str
    authors: tuple[Author, ...]
    abstract: str


_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(os.path.realpath(__file__))),
    autoescape=True,
)

template = _env.get_template("digest.md.jinja2")


def _fix_url(url: str) -> str:
    defragged, _ = urldefrag(url)

    if defragged.startswith("/"):
        return urljoin("https://www.nber.org/", defragged)
    else:
        return defragged
        

def get_new_working_papers(session: requests.Session, facet: str, term: str) -> Iterator[WorkingPaper]:
    url = "https://www.nber.org/api/v1/working_page_listing/contentType/working_paper/_/_/search"
    params = {
        "facet": f"{facet}:{term}",
        "page": str(1),
        "perPage": str(50),
        "sortBy": "public_date",
    }
    resp = session.get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    data = resp.json()

    for result in data["results"]:
        if result["type"] != "working_paper":
            continue

        if not result.get("newthisweek", False):
            continue
        
        title = result["title"]
        url = _fix_url(result["url"])
        abstract = result["abstract"]
        raw_authors = result["authors"]

        authors = []
        for raw_author in raw_authors:
            soup = BeautifulSoup(raw_author, features="lxml")
            name = soup.text
            a = soup.find("a")
            if a:
                author_url = _fix_url(a["href"])
            else:
                author_url = None

            authors.append(Author(name=name, url=author_url))

        yield WorkingPaper(
            url=url,
            title=title,
            authors=tuple(authors),
            abstract=abstract,
        )


def make_messages(session: requests.Session) -> Iterator[dict[str, Any]]:
    for stream, search_terms in STREAM_SEARCH_TERMS.items():
        working_papers: dict[str, WorkingPaper] = {}

        for facet, term in search_terms:
            for working_paper in get_new_working_papers(session, facet, term):
                working_papers[working_paper.url] = working_paper
            time.sleep(SLEEP_TIME)

        if not working_papers:
            continue

        content = template.render(papers=working_papers.values())

        yield {
            "type": "stream",
            "to": stream,
            "topic": "working papers",
            "content": content,
        }


def print_message(request: dict[str, Any]):
    to = request["to"]
    topic = request["topic"]
    content = request["content"]

    print(f"To: {to}\nTopic: {topic}\n\n{content}")    


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", default=False, action='store_true')
    args = parser.parse_args()

    config_file = os.getenv("ZULIPRC")
    if config_file is None:
        print("error: could not find configuration file", file=sys.stderr)
        sys.exit(1)

    client = zulip.Client(config_file=config_file)

    with requests.Session() as session:
        messages = list(make_messages(session))

        if args.dry_run:
            for message in messages:
                print_message(message)
        else:
            for message in messages:
                result = client.send_message(message)

                if result["result"] != "success":
                    print(f"could not send message to {message['to']}: {result['msg']}", file=sys.stderr)
                    sys.exit(1)

