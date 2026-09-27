from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup


VIGNETTE_URL = (
    "https://bmp.asfinag.at/en/vignette-and-section-tolls/vignette/"
)
SECTION_TOLL_URL = (
    "https://bmp.asfinag.at/en/vignette-and-section-tolls/section-toll/"
)

OUTPUT_FILE = Path(__file__).resolve().parents[1] / "austria_tolls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_text(url: str) -> str:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return " ".join(soup.get_text(" ", strip=True).split())


def euro_number(raw: str) -> float:
    cleaned = raw.strip().replace("\u00a0", " ")

    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    return round(float(cleaned), 2)


def normalize_for_search(text: str) -> str:
    return (
        text.replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )


def extract_passenger_car_price_from_block(
    block: str,
    label: str,
) -> float:
    patterns = [
        r"Passenger car\s*\(Category B\)\s*:\s*EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        r"Passenger car\s*\(Category B\).*?EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return euro_number(match.group(1))

    raise RuntimeError(
        f"Nisam pronašao Passenger car cijenu za {label}"
    )


def find_best_label_block(
    text: str,
    label_pattern: str,
    next_label_patterns: list[str],
    label: str,
) -> str:
    matches = list(
        re.finditer(
            label_pattern,
            text,
            flags=re.IGNORECASE,
        )
    )

    candidates: list[str] = []

    for match in matches:
        start = match.start()
        search_start = match.end()
        end = min(len(text), start + 1600)

        for next_pattern in next_label_patterns:
            next_match = re.search(
                next_pattern,
                text[search_start:end],
                flags=re.IGNORECASE,
            )
            if next_match:
                candidate_end = search_start + next_match.start()
                if candidate_end > start:
                    end = min(end, candidate_end)

        block = text[start:end]

        if re.search(
            r"Passenger car\s*\(Category B\)",
            block,
            flags=re.IGNORECASE,
        ):
            candidates.append(block)

    if not candidates:
        raise RuntimeError(
            f"Nisam pronašao pouzdan blok za {label}"
        )

    # Biramo najkraći validni blok da ne "preskoči" u sljedeći proizvod.
    return min(candidates, key=len)


def read_vignettes(text: str) -> dict[str, float]:
    text = normalize_for_search(text)

    one_day = find_best_label_block(
        text,
        r"1-day vignette",
        [
            r"10-day vignette",
            r"2-month vignette",
            r"Annual vignette",
        ],
        "1-day vignette",
    )

    ten_day = find_best_label_block(
        text,
        r"10-day vignette",
        [
            r"2-month vignette",
            r"Annual vignette",
        ],
        "10-day vignette",
    )

    two_month = find_best_label_block(
        text,
        r"2-month vignette",
        [
            r"Annual vignette",
        ],
        "2-month vignette",
    )

    annual = find_best_label_block(
        text,
        r"Annual vignette",
        [],
        "Annual vignette",
    )

    return {
        "1_day": extract_passenger_car_price_from_block(
            one_day,
            "1-day vignette",
        ),
        "10_days": extract_passenger_car_price_from_block(
            ten_day,
            "10-day vignette",
        ),
        "2_months": extract_passenger_car_price_from_block(
            two_month,
            "2-month vignette",
        ),
        "1_year": extract_passenger_car_price_from_block(
            annual,
            "Annual vignette",
        ),
    }


def section_block(
    text: str,
    start_pattern: str,
    end_patterns: list[str],
    label: str,
) -> str:
    start_match = re.search(
        start_pattern,
        text,
        flags=re.IGNORECASE,
    )

    if not start_match:
        raise RuntimeError(
            f"Nisam pronašao sekciju: {label}"
        )

    start = start_match.start()
    search_start = start_match.end()
    end = len(text)

    for pattern in end_patterns:
        m = re.search(
            pattern,
            text[search_start:],
            flags=re.IGNORECASE,
        )
        if m:
            end = min(end, search_start + m.start())

    return text[start:end]


def first_single_trip_price(
    block: str,
    label: str,
) -> float:
    match = re.search(
        r"Single Trip(?:\s+[A-Za-z0-9()/ -]+?)?\s*(?:\||:)?\s*EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        block,
        flags=re.IGNORECASE,
    )

    if not match:
        # Fallback za tekst bez tabelarnih separatora.
        match = re.search(
            r"Single Trip.*?EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

    if not match:
        raise RuntimeError(
            f"Nisam pronašao Single Trip cijenu za {label}"
        )

    return euro_number(match.group(1))


def single_trip_price_near_heading(
    text: str,
    heading_pattern: str,
    label: str,
    max_distance: int = 700,
) -> float:
    matches = list(
        re.finditer(
            heading_pattern,
            text,
            flags=re.IGNORECASE,
        )
    )

    # Idemo od posljednje pojave naslova unazad.
    # Tako izbjegavamo navigaciju/TOC na vrhu stranice.
    for heading in reversed(matches):
        block = text[
            heading.start():
            min(len(text), heading.start() + max_distance)
        ]

        match = re.search(
            r"Single Trip\s*(?:\||:)?\s*EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
            block,
            flags=re.IGNORECASE,
        )

        if match:
            return euro_number(match.group(1))

    raise RuntimeError(
        f"Nisam pronašao Single Trip cijenu za {label}"
    )


def read_section_tolls(text: str) -> dict[str, dict[str, object]]:
    text = normalize_for_search(text)

    a9 = section_block(
        text,
        r"Bosruck Toll Station and Gleinalm Toll Station\s*\(A\s*9\)",
        [
            r"Tauern/Katschberg Toll Station\s*\(A\s*10\)",
        ],
        "A9 Bosruck/Gleinalm",
    )

    a10 = section_block(
        text,
        r"Tauern/Katschberg Toll Station\s*\(A\s*10\)",
        [
            r"Karawanken Toll Station\s*\(A\s*11\)",
        ],
        "A10 Tauern/Katschberg",
    )

    a11 = section_block(
        text,
        r"Karawanken Toll Station\s*\(A\s*11\)",
        [
            r"Brenner Motorway\s*\(A\s*13\)",
        ],
        "A11 Karawanken",
    )

    a13 = section_block(
        text,
        r"Brenner Motorway\s*\(A\s*13\)",
        [
            r"Arlberg",
        ],
        "A13 Brenner",
    )

    s16_price = single_trip_price_near_heading(
        text,
        r"Arlberg Tunnel\s*\(S\s*16\)",
        "S16 Arlberg",
    )

    bosruck_match = re.search(
        r"Single Trip Bosruck Toll Station\s*(?:\||:)?\s*EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        a9,
        flags=re.IGNORECASE,
    )

    gleinalm_match = re.search(
        r"Single Trip Gleinalm Toll Station\s*(?:\||:)?\s*EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        a9,
        flags=re.IGNORECASE,
    )

    if not bosruck_match:
        raise RuntimeError(
            "Nisam pronašao cijenu A9 Bosruck"
        )

    if not gleinalm_match:
        raise RuntimeError(
            "Nisam pronašao cijenu A9 Gleinalm"
        )

    return {
        "a9_bosruck": {
            "name": "A9 Bosruck",
            "price": euro_number(
                bosruck_match.group(1)
            ),
        },
        "a9_gleinalm": {
            "name": "A9 Gleinalm",
            "price": euro_number(
                gleinalm_match.group(1)
            ),
        },
        "a10_tauern_katschberg": {
            "name": "A10 Tauern/Katschberg",
            "price": first_single_trip_price(
                a10,
                "A10 Tauern/Katschberg",
            ),
        },
        "a11_karawanken": {
            "name": "A11 Karawanken (smjer Slovenija)",
            "price": first_single_trip_price(
                a11,
                "A11 Karawanken",
            ),
        },
        "a13_brenner": {
            "name": "A13 Brenner",
            "price": first_single_trip_price(
                a13,
                "A13 Brenner",
            ),
        },
        "s16_arlberg": {
            "name": "S16 Arlberg",
            "price": s16_price,
        },
    }


def validate(data: dict) -> None:
    # Stroga zaštita od pogrešnog parsiranja stranice.
    expected_vignettes = {
        "1_day": (5.0, 20.0),
        "10_days": (8.0, 25.0),
        "2_months": (20.0, 60.0),
        "1_year": (70.0, 180.0),
    }

    for key, (low, high) in expected_vignettes.items():
        value = float(
            data["vignettes"]["car"][key]
        )

        if not low <= value <= high:
            raise RuntimeError(
                f"Sumnjiva cijena {key}: {value}"
            )

    expected_special = {
        "a9_bosruck": (3.0, 15.0),
        "a9_gleinalm": (5.0, 25.0),
        "a10_tauern_katschberg": (8.0, 30.0),
        "a11_karawanken": (5.0, 20.0),
        "a13_brenner": (8.0, 30.0),
        "s16_arlberg": (8.0, 30.0),
    }

    for key, (low, high) in expected_special.items():
        value = float(
            data["special_tolls"][key]["price"]
        )

        if not low <= value <= high:
            raise RuntimeError(
                f"Sumnjiva posebna putarina {key}: {value}"
            )


def prices_only(data: dict) -> dict:
    return {
        "vignettes": data.get("vignettes"),
        "special_tolls": data.get("special_tolls"),
    }


def main() -> None:
    vignette_text = fetch_text(VIGNETTE_URL)
    section_text = fetch_text(SECTION_TOLL_URL)

    vignettes = read_vignettes(vignette_text)
    special_tolls = read_section_tolls(section_text)

    print(
        "ASFINAG Austrija vinjete:",
        vignettes,
    )
    print(
        "ASFINAG posebne putarine:",
        {
            key: item["price"]
            for key, item in special_tolls.items()
        },
    )

    new_data = {
        "updated": date.today().isoformat(),
        "vignettes": {
            "car": vignettes,
        },
        "special_tolls": special_tolls,
        "sources": {
            "vignette": VIGNETTE_URL,
            "section_toll": SECTION_TOLL_URL,
        },
    }

    validate(new_data)

    old_data = None

    if OUTPUT_FILE.exists():
        try:
            old_data = json.loads(
                OUTPUT_FILE.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            old_data = None

    if (
        old_data is not None
        and prices_only(old_data)
        == prices_only(new_data)
    ):
        print(
            "Cijene Austrije nisu promijenjene."
        )
        return

    OUTPUT_FILE.write_text(
        json.dumps(
            new_data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Ažuriran {OUTPUT_FILE.name}: "
        f"{new_data['updated']}"
    )


if __name__ == "__main__":
    main()
