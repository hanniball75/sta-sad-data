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

    return " ".join(
        soup.get_text(" ", strip=True).split()
    )


def euro_number(raw: str) -> float:
    cleaned = raw.strip()

    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")

    return round(float(cleaned), 2)


def extract_price(
    text: str,
    patterns: list[str],
    label: str,
) -> float:
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return euro_number(match.group(1))

    raise RuntimeError(
        f"Nisam pronašao cijenu za: {label}"
    )


def read_vignettes(text: str) -> dict[str, float]:
    return {
        "1_day": extract_price(
            text,
            [
                r"1-day vignette.*?Passenger car\s*\(Category B\).*?EUR\s*([0-9]+[.,][0-9]{2})",
                r"1-day vignette.*?Passenger car.*?EUR\s*([0-9]+[.,][0-9]{2})",
            ],
            "1-day vignette",
        ),
        "10_days": extract_price(
            text,
            [
                r"10-day vignette.*?Passenger car\s*\(Category B\).*?EUR\s*([0-9]+[.,][0-9]{2})",
                r"10-day vignette.*?Passenger car.*?EUR\s*([0-9]+[.,][0-9]{2})",
            ],
            "10-day vignette",
        ),
        "2_months": extract_price(
            text,
            [
                r"2-month vignette.*?Passenger car\s*\(Category B\).*?EUR\s*([0-9]+[.,][0-9]{2})",
                r"2-month vignette.*?Passenger car.*?EUR\s*([0-9]+[.,][0-9]{2})",
            ],
            "2-month vignette",
        ),
        "1_year": extract_price(
            text,
            [
                r"Annual vignette.*?Passenger car\s*\(Category B\).*?EUR\s*([0-9]+[.,][0-9]{2})",
                r"Annual vignette.*?Passenger car.*?EUR\s*([0-9]+[.,][0-9]{2})",
            ],
            "annual vignette",
        ),
    }


def read_section_tolls(text: str) -> dict[str, dict[str, object]]:
    def p(patterns: list[str], label: str) -> float:
        return extract_price(text, patterns, label)

    return {
        "a9_bosruck": {
            "name": "A9 Bosruck",
            "price": p(
                [
                    r"Bosruck Toll Station.*?Single Trip Bosruck Toll Station.*?EUR\s*([0-9]+[.,][0-9]{2})",
                    r"Single Trip Bosruck Toll Station.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "A9 Bosruck",
            ),
        },
        "a9_gleinalm": {
            "name": "A9 Gleinalm",
            "price": p(
                [
                    r"Gleinalm Toll Station.*?Single Trip Gleinalm Toll Station.*?EUR\s*([0-9]+[.,][0-9]{2})",
                    r"Single Trip Gleinalm Toll Station.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "A9 Gleinalm",
            ),
        },
        "a10_tauern_katschberg": {
            "name": "A10 Tauern/Katschberg",
            "price": p(
                [
                    r"Tauern/Katschberg Toll Station.*?Single Trip.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "A10 Tauern/Katschberg",
            ),
        },
        "a11_karawanken": {
            "name": "A11 Karawanken (smjer Slovenija)",
            "price": p(
                [
                    r"Karawanken Toll Station.*?Single Trip.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "A11 Karawanken",
            ),
        },
        "a13_brenner": {
            "name": "A13 Brenner",
            "price": p(
                [
                    r"Brenner Motorway.*?Single Trip.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "A13 Brenner",
            ),
        },
        "s16_arlberg": {
            "name": "S16 Arlberg",
            "price": p(
                [
                    r"Arlberg Tunnel.*?Single Trip.*?EUR\s*([0-9]+[.,][0-9]{2})",
                ],
                "S16 Arlberg",
            ),
        },
    }


def validate(data: dict) -> None:
    expected_vignettes = {
        "1_day": (5.0, 30.0),
        "10_days": (5.0, 40.0),
        "2_months": (10.0, 80.0),
        "1_year": (50.0, 250.0),
    }

    for key, (low, high) in expected_vignettes.items():
        value = data["vignettes"]["car"][key]
        if not low <= value <= high:
            raise RuntimeError(
                f"Sumnjiva cijena {key}: {value}"
            )

    for key, item in data["special_tolls"].items():
        value = float(item["price"])
        if not 1.0 <= value <= 100.0:
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

    print("ASFINAG Austrija vinjete:", vignettes)
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
                OUTPUT_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            old_data = None

    if old_data is not None:
        if prices_only(old_data) == prices_only(new_data):
            print("Cijene Austrije nisu promijenjene.")
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
