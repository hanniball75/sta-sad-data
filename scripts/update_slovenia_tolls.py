import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT = Path("slovenia_tolls.json")

DARS_URL = "https://shop.asfinag.at/en/toll-products/slovenia/?type=car"

KARAVANKE_URL = (
    "https://www.gov.si/novice/"
    "2025-10-02-171-redna-seja-vlade-republike-slovenije/"
)


def http_session():
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(
            429,
            500,
            502,
            503,
            504,
        ),
        allowed_methods=frozenset(["GET"]),
    )

    session = requests.Session()

    session.headers.update({
        "User-Agent":
            "Mozilla/5.0 StaSadSloveniaUpdater/1.0",
        "Accept-Language":
            "en-US,en;q=0.9,sl;q=0.8",
    })

    session.mount(
        "https://",
        HTTPAdapter(max_retries=retry),
    )

    return session


def fetch_text(url):
    response = http_session().get(
        url,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    text = soup.get_text(
        " ",
        strip=True,
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    )


def euro_values(text):
    matches = re.findall(
        r"(\d{1,3}[,.]\d{1,2})\s*€",
        text,
    )

    return [
        float(
            value.replace(",", ".")
        )
        for value in matches
    ]


def find_price_near(
    text,
    keywords,
    min_price,
    max_price,
):
    lower = text.lower()

    positions = []

    for keyword in keywords:
        pos = lower.find(
            keyword.lower()
        )

        if pos >= 0:
            positions.append(pos)

    if not positions:
        return None

    for pos in positions:
        start = max(
            0,
            pos - 250,
        )

        end = min(
            len(text),
            pos + 450,
        )

        section = text[
            start:end
        ]

        prices = euro_values(
            section
        )

        for price in prices:
            if (
                min_price
                <= price
                <= max_price
            ):
                return price

    return None


def read_dars_prices():
    text = fetch_text(
        DARS_URL
    )

    seven_days = find_price_near(
        text,
        [
            "7-day",
            "7 day",
            "weekly",
            "7 days",
        ],
        5.0,
        40.0,
    )

    one_month = find_price_near(
        text,
        [
            "1-month",
            "1 month",
            "monthly",
            "month",
        ],
        10.0,
        80.0,
    )

    annual = find_price_near(
        text,
        [
            "annual",
            "1 year",
            "yearly",
            "year",
        ],
        50.0,
        300.0,
    )

    if (
        seven_days is None
        or one_month is None
        or annual is None
    ):
        raise RuntimeError(
            "SIGURNOSNA BLOKADA: "
            "DARS cijene nisu "
            "pouzdano prepoznate."
        )

    return {
        "7_days": seven_days,
        "1_month": one_month,
        "1_year": annual,
    }


def read_karavanke_price():
    text = fetch_text(
        KARAVANKE_URL
    )

    lower = text.lower()

    pos = lower.find(
        "karavanke"
    )

    if pos < 0:
        raise RuntimeError(
            "SIGURNOSNA BLOKADA: "
            "Karavanke nisu pronađene."
        )

    start = max(
        0,
        pos - 500,
    )

    end = min(
        len(text),
        pos + 1000,
    )

    section = text[
        start:end
    ]

    prices = euro_values(
        section
    )

    for price in prices:
        if 5.0 <= price <= 20.0:
            return price

    # Slovenačka službena objava
    # ponekad piše "9 evrov"
    match = re.search(
        r"(\d{1,2}(?:[,.]\d{1,2})?)"
        r"\s*(?:evrov|euro|eur)",
        section.lower(),
    )

    if match:
        price = float(
            match.group(1)
            .replace(",", ".")
        )

        if 5.0 <= price <= 20.0:
            return price

    raise RuntimeError(
        "SIGURNOSNA BLOKADA: "
        "cijena Karavanki "
        "nije prepoznata."
    )


def load_existing():
    if not OUTPUT.exists():
        return {}

    with OUTPUT.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def validate_change(
    old,
    new,
    name,
):
    if old is None:
        return

    allowed_change = max(
        2.0,
        old * 0.25,
    )

    if abs(new - old) > allowed_change:
        raise RuntimeError(
            "SIGURNOSNA BLOKADA: "
            f"prevelika promjena za "
            f"{name}: "
            f"{old} -> {new}"
        )


def main():
    existing = load_existing()

    dars_prices = read_dars_prices()

    karavanke_price = (
        read_karavanke_price()
    )

    old_2a = (
        existing
        .get("vignettes", {})
        .get("2A", {})
    )

    validate_change(
        old_2a.get("7_days"),
        dars_prices["7_days"],
        "Slovenija 2A 7 dana",
    )

    validate_change(
        old_2a.get("1_month"),
        dars_prices["1_month"],
        "Slovenija 2A 1 mjesec",
    )

    validate_change(
        old_2a.get("1_year"),
        dars_prices["1_year"],
        "Slovenija 2A godišnja",
    )

    old_tunnel = (
        existing
        .get("special_tolls", {})
        .get("karavanke", {})
        .get("price")
    )

    validate_change(
        old_tunnel,
        karavanke_price,
        "Karavanke",
    )

    new_data = dict(existing)

    new_data["country"] = (
        "Slovenia"
    )

    new_data["currency"] = (
        "EUR"
    )

    vignettes = dict(
        new_data.get(
            "vignettes",
            {},
        )
    )

    vignettes["2A"] = {
        "7_days":
            dars_prices["7_days"],
        "1_month":
            dars_prices["1_month"],
        "1_year":
            dars_prices["1_year"],
    }

    new_data["vignettes"] = (
        vignettes
    )

    special = dict(
        new_data.get(
            "special_tolls",
            {},
        )
    )

    special["karavanke"] = {
        "name":
            "Predor Karavanke",
        "price":
            karavanke_price,
        "vehicle":
            "passenger_car",
        "separate_from_vignette":
            True,
    }

    new_data[
        "special_tolls"
    ] = special

    old_comparable = {
        "vignettes":
            existing.get(
                "vignettes",
                {},
            ),
        "special_tolls":
            existing.get(
                "special_tolls",
                {},
            ),
    }

    new_comparable = {
        "vignettes":
            new_data[
                "vignettes"
            ],
        "special_tolls":
            new_data[
                "special_tolls"
            ],
    }

    if (
        old_comparable
        == new_comparable
    ):
        print(
            "Cijene Slovenije "
            "nisu promijenjene."
        )
        return

    old_version = existing.get(
        "version",
        0,
    )

    try:
        old_version = int(
            old_version
        )
    except:
        old_version = 0

    new_data["version"] = (
        old_version + 1
    )

    new_data["updated"] = (
        date.today().isoformat()
    )

    with OUTPUT.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            new_data,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.write("\n")

    print(
        "slovenia_tolls.json "
        "je ažuriran."
    )

    print(
        "2A 7 dana:",
        dars_prices["7_days"],
    )

    print(
        "2A 1 mjesec:",
        dars_prices["1_month"],
    )

    print(
        "2A godišnja:",
        dars_prices["1_year"],
    )

    print(
        "Karavanke:",
        karavanke_price,
    )


if __name__ == "__main__":
    main()
