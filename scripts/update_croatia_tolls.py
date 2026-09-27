import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

A3_URL = "https://www.hac.hr/hr/cestarina/cjenik/a3"
BREGANA_URL = "https://www.hac.hr/hr/cestarina/cjenik/bregana"

OUTPUT = Path("croatia_tolls.json")

SOURCE_TO_APP = {
    "Ivanić Grad": "Ivanić Grad",
    "Križ": "Križ",
    "Popovača": "Popovača",
    "Kutina": "Kutina",
    "Lipovljani": "Lipovljani",
    "Novska": "Novska",
    "Okučani": "Okučani",
    "Nova Gradiška": "Nova Gradiška",
    "Lužani": "Lužani",
    "Sl. Brod zapad": "Slavonski Brod zapad",
    "Sl. Brod istok": "Slavonski Brod istok",
    "ČCP Svilaj": "ČCP Svilaj",
    "Velika Kopanica": "Velika Kopanica",
    "Đakovo": "Đakovo",
    "Babina Greda": "Babina Greda",
    "Županja": "Županja",
    "Čepin": "Čepin",
    "Spačva": "Spačva",
    "Osijek": "Osijek",
    "Lipovac": "Lipovac",
    "Sudaraž": "Sudaraž",
}

USER_AGENT = "Mozilla/5.0 StaSadTollUpdater/1.0"


def http_session():
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )

    session = requests.Session()

    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.7",
    })

    session.mount(
        "https://",
        HTTPAdapter(max_retries=retry),
    )

    return session


def fetch_html(url):
    response = http_session().get(
        url,
        timeout=30,
    )

    response.raise_for_status()

    return response.text


def parse_price(text):
    cleaned = text.replace("\xa0", " ").strip()

    match = re.search(
        r"(\d+(?:[.,]\d+)?)",
        cleaned,
    )

    if not match:
        raise ValueError(
            f"Ne mogu pročitati cijenu: {text}"
        )

    return float(
        match.group(1).replace(",", ".")
    )


def extract_category_i_rows(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    found = {}

    for row in soup.find_all("tr"):
        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(
                ["th", "td"]
            )
        ]

        if len(cells) < 3:
            continue

        station = " ".join(cells[0].split())
        if "Brod zapad" in station:
        station = "Sl. Brod zapad"

        if "Brod istok" in station:
        station = "Sl. Brod istok"

        if station in SOURCE_TO_APP or station == "Bregana":
            found[station] = parse_price(
                cells[2]
            )

    return found


def load_existing():
    if not OUTPUT.exists():
        return {}

    with OUTPUT.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def validate_prices(
    new_tolls,
    old_tolls,
):
    expected = set(
        SOURCE_TO_APP.values()
    )

    received = set(new_tolls)

    missing = sorted(
        expected - received
    )

    if missing:
        raise RuntimeError(
            "Nedostaju stanice: "
            + ", ".join(missing)
        )

    for name, value in new_tolls.items():
        if not 0.50 <= value <= 40.00:
            raise RuntimeError(
                f"Sumnjiva cijena "
                f"{name}: {value}"
            )

    for name, old in old_tolls.items():
        if name not in new_tolls:
            continue

        new = new_tolls[name]

        allowed_change = max(
            2.00,
            old * 0.25,
        )

        if abs(new - old) > allowed_change:
            raise RuntimeError(
                f"Prevelika promjena "
                f"{name}: "
                f"{old} -> {new}"
            )


def main():
    a3_html = fetch_html(A3_URL)

    bregana_html = fetch_html(
        BREGANA_URL
    )

    a3_rows = extract_category_i_rows(
        a3_html
    )

    bregana_rows = extract_category_i_rows(
        bregana_html
    )

    if "Bregana" not in bregana_rows:
        raise RuntimeError(
            "Bregana nije pronađena."
        )

    bregana_fee = bregana_rows[
        "Bregana"
    ]

    new_tolls = {}

    for hac_name, app_name in SOURCE_TO_APP.items():
        if hac_name not in a3_rows:
            raise RuntimeError(
                f"Nema stanice: {hac_name}"
            )

        new_tolls[app_name] = round(
            bregana_fee
            + a3_rows[hac_name],
            2,
        )

    existing = load_existing()

    old_tolls_raw = existing.get(
        "tolls",
        {},
    )

    old_tolls = {
        str(key): float(value)
        for key, value
        in old_tolls_raw.items()
        if isinstance(
            value,
            (int, float),
        )
    }

    validate_prices(
        new_tolls,
        old_tolls,
    )

    if old_tolls == new_tolls:
        print(
            "Cijene nisu promijenjene."
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

    payload = {
        "version": old_version + 1,
        "updated": date.today().isoformat(),
        "country": "Croatia",
        "currency": "EUR",
        "vehicleCategory": "I",
        "tolls": new_tolls,
    }

    with OUTPUT.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.write("\n")

    print(
        "croatia_tolls.json ažuriran."
    )


if __name__ == "__main__":
    main()
