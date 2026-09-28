from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.hak.hr/info/stanje-na-cestama/"
BIHAMK_URL = "https://bihamk.ba/spi/stanje-na-cesti-u-bih/granicni-prijelazi"
OUTPUT = Path("border_waits.json")
HAK_TZ = ZoneInfo("Europe/Zagreb")
BIHAMK_TZ = ZoneInfo("Europe/Sarajevo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36 "
        "sta-sad-border-waits/1.0"
    ),
    "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.8",
}

CROSSINGS = {
    "gornji_varos_gradiska": {
        "name": "Gornji Varoš – Gradiška",
        "aliases": [
            "Gornji Varoš (Gradiška (novi most))",
            "Gornji Varoš",
        ],
        "bihamk_names": ["GP Gradiška", "Gradiška"],
    },
    "slavonski_brod_brod": {
        "name": "Slavonski Brod – Brod",
        "aliases": [
            "Slavonski Brod (Bosanski Brod)",
            "Slavonski Brod",
        ],
        "bihamk_names": ["GP Brod", "Brod"],
    },
    "svilaj": {
        "name": "Svilaj",
        "aliases": ["Svilaj"],
        "bihamk_names": ["GP Svilaj", "Svilaj"],
    },
    "zupanja_orasje": {
        "name": "Županja – Orašje",
        "aliases": [
            "Županja (Orašje)",
            "Županja",
        ],
        "bihamk_names": ["GP Orašje", "Orašje"],
    },
}


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split()).strip()


def normalize(text: str) -> str:
    value = clean(text).lower()
    table = str.maketrans(
        {
            "č": "c",
            "ć": "c",
            "ž": "z",
            "š": "s",
            "đ": "d",
        }
    )
    return value.translate(table)


def wait_minutes(label: str) -> Optional[int]:
    text = normalize(label)

    if not text or "nema podataka" in text:
        return None

    # "do 30 min", "30 min"
    m = re.search(r"(\d+)\s*min", text)
    minutes = int(m.group(1)) if m else 0

    # "1 h", "1 h 30 min", "do 10 sati"
    h = re.search(r"(\d+)\s*(?:h|sat|sata|sati)", text)
    if h:
        minutes += int(h.group(1)) * 60

    if minutes > 0:
        return minutes

    # Tekst poput "višesatna čekanja" nema dovoljno preciznu brojku.
    return None


def parse_hak_datetime(value: str) -> Optional[str]:
    """Pretvori HAK lokalno vrijeme u ISO 8601 s vremenskom zonom."""
    m = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s+"
        r"(\d{1,2}):(\d{2})(?::(\d{2}))?",
        value,
    )
    if not m:
        return None

    day, month, year, hour, minute, second = m.groups()

    dt = datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second or 0),
        tzinfo=HAK_TZ,
    )

    return dt.isoformat(timespec="seconds")


def parse_cell(raw: str) -> dict:
    text = clean(raw)

    if not text or "nema podataka" in normalize(text):
        return {
            "label": "Nema podataka",
            "wait_minutes": None,
            "observed_at": None,
            "observed_text": None,
        }

    observed_at = None
    observed_text = None

    # HAK koristi više varijanti:
    # "2 h T: 28.9.2026. 18:11:23"
    # "2 h Vrijeme podatka: 28.09.2026 18:11:23"
    patterns = [
        r"\bT:\s*(\d{1,2}\.\d{1,2}\.\d{4}\.?\s+\d{1,2}:\d{2}(?::\d{2})?)",
        r"\bVrijeme\s+podatka:\s*(\d{1,2}\.\d{1,2}\.\d{4}\.?\s+\d{1,2}:\d{2}(?::\d{2})?)",
    ]

    match = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            break

    if match:
        observed_text = clean(match.group(1))
        observed_at = parse_hak_datetime(observed_text)
        label = clean(text[: match.start()])
    else:
        label = text

    # Ako HAK doda "L: 6 km" prije vremena, ne želimo da to postane dio čekanja.
    label = re.sub(
        r"\bL:\s*[\d.,]+\s*km\b",
        "",
        label,
        flags=re.I,
    )
    label = clean(label).rstrip(" .;-")

    if not label:
        label = "Nema podataka"

    return {
        "label": label,
        "wait_minutes": wait_minutes(label),
        "observed_at": observed_at,
        "observed_text": observed_text,
    }


def source_timestamp(page_text: str) -> tuple[Optional[str], Optional[str]]:
    # HAK tekst npr. "Izvor: MUP (28.09.2026. 10:23)"
    matches = re.findall(
        r"Izvor:\s*MUP\s*\((\d{1,2}\.\d{1,2}\.\d{4}\.?\s+\d{1,2}:\d{2}(?::\d{2})?)\)",
        page_text,
        flags=re.I,
    )
    if not matches:
        return None, None

    raw = clean(matches[-1])
    return raw, parse_hak_datetime(raw)


def row_for_aliases(soup: BeautifulSoup, aliases: list[str]):
    wanted = [normalize(a) for a in aliases]

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue

        first = normalize(cells[0].get_text(" ", strip=True))

        if any(alias == first or alias in first for alias in wanted):
            return row

    return None


def parse_crossing(soup: BeautifulSoup, spec: dict) -> dict:
    row = row_for_aliases(soup, spec["aliases"])

    if row is None:
        return {
            "name": spec["name"],
            "hak_name": spec["aliases"][0],
            "available": False,
            "to_bih": {
                "label": "Nema podataka",
                "wait_minutes": None,
                "observed_at": None,
                "observed_text": None,
            },
            "to_croatia": {
                "label": "Nema podataka",
                "wait_minutes": None,
                "observed_at": None,
                "observed_text": None,
            },
        }

    cells = [
        clean(cell.get_text(" ", strip=True))
        for cell in row.find_all(["th", "td"])
    ]

    if len(cells) < 3:
        raise RuntimeError(
            f"HAK red za {spec['name']} ima premalo ćelija: {cells!r}"
        )

    # HAK tablica je iz perspektive Hrvatske:
    # prvi podatak = ULAZ u Hrvatsku (BiH -> HR)
    # drugi podatak = IZLAZ iz Hrvatske (HR -> BiH)
    # eventualne dodatne ćelije odnose se na druge kategorije vozila
    # i za sada ih namjerno ne koristimo.
    to_croatia = parse_cell(cells[1])
    to_bih = parse_cell(cells[2])

    return {
        "name": spec["name"],
        "hak_name": cells[0],
        "available": True,
        "to_bih": to_bih,
        "to_croatia": to_croatia,
    }


def parse_local_datetime(
    value: str,
    tz: ZoneInfo,
) -> Optional[str]:
    m = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s+"
        r"(?:u\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?",
        value,
        flags=re.I,
    )
    if not m:
        return None

    day, month, year, hour, minute, second = m.groups()

    dt = datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second or 0),
        tzinfo=tz,
    )

    return dt.isoformat(timespec="seconds")


def bihamk_report_time(page_text: str) -> tuple[Optional[str], Optional[str]]:
    # BIHAMK npr. "Stanje na cestama 28.09.2026. u 20:00 sati"
    m = re.search(
        r"Stanje\s+na\s+cestama\s+"
        r"(\d{1,2}\.\d{1,2}\.\d{4}\.?\s+u\s+\d{1,2}:\d{2})\s+sati",
        page_text,
        flags=re.I,
    )
    if not m:
        return None, None

    raw = clean(m.group(1))
    return raw, parse_local_datetime(raw, BIHAMK_TZ)


def bihamk_section_text(
    soup: BeautifulSoup,
    names: list[str],
) -> Optional[str]:
    wanted = [normalize(name) for name in names]

    headings = soup.find_all(["h2", "h3", "h4"])

    for heading in headings:
        title = clean(heading.get_text(" ", strip=True))
        normalized = normalize(title)

        if not any(
            normalized == name or name in normalized
            for name in wanted
        ):
            continue

        parts: list[str] = []

        for element in heading.find_all_next():
            if element is heading:
                continue

            if element.name in ["h2", "h3", "h4"]:
                break

            if element.name in ["p", "li"]:
                text = clean(element.get_text(" ", strip=True))
                if text and text not in parts:
                    parts.append(text)

        return clean(" ".join(parts))

    return None


def parse_bihamk_wait(
    section_text: Optional[str],
    observed_at: Optional[str],
    observed_text: Optional[str],
) -> dict:
    if not section_text:
        return {
            "available": False,
            "label": "Nema podataka",
            "wait_minutes": None,
            "bound": None,
            "scope": "unspecified",
            "observed_at": observed_at,
            "observed_text": observed_text,
            "raw_text": None,
        }

    text = clean(section_text)
    normalized = normalize(text)

    scope = "both_or_unspecified"

    # BIHAMK govori iz perspektive BiH:
    # "na ulazu" = ulazak u BiH
    # "na izlazu" = izlazak iz BiH
    if "na ulazu" in normalized and "na izlazu" not in normalized:
        scope = "to_bih"
    elif "na izlazu" in normalized and "na ulazu" not in normalized:
        scope = "to_croatia"

    # Najčešća BIHAMK formulacija:
    # "Zadržavanja putničkih vozila nisu duža od 30 minuta."
    m = re.search(
        r"(?:nisu|nije)\s+du[žz]a\s+od\s+(\d+)\s+min",
        normalized,
        flags=re.I,
    )
    if m:
        minutes = int(m.group(1))
        return {
            "available": True,
            "label": f"do {minutes} min",
            "wait_minutes": minutes,
            "bound": "upper",
            "scope": scope,
            "observed_at": observed_at,
            "observed_text": observed_text,
            "raw_text": text,
        }

    # "do 30 minuta"
    m = re.search(
        r"\bdo\s+(\d+)\s+min",
        normalized,
        flags=re.I,
    )
    if m:
        minutes = int(m.group(1))
        return {
            "available": True,
            "label": f"do {minutes} min",
            "wait_minutes": minutes,
            "bound": "upper",
            "scope": scope,
            "observed_at": observed_at,
            "observed_text": observed_text,
            "raw_text": text,
        }

    # "oko 45 minuta"
    m = re.search(
        r"\boko\s+(\d+)\s+min",
        normalized,
        flags=re.I,
    )
    if m:
        minutes = int(m.group(1))
        return {
            "available": True,
            "label": f"oko {minutes} min",
            "wait_minutes": minutes,
            "bound": "estimate",
            "scope": scope,
            "observed_at": observed_at,
            "observed_text": observed_text,
            "raw_text": text,
        }

    # "duža od 30 minuta" (ali ne "nisu duža")
    m = re.search(
        r"(?<!nisu\s)du[žz]a\s+od\s+(\d+)\s+min",
        normalized,
        flags=re.I,
    )
    if m:
        minutes = int(m.group(1))
        return {
            "available": True,
            "label": f"preko {minutes} min",
            "wait_minutes": minutes,
            "bound": "lower",
            "scope": scope,
            "observed_at": observed_at,
            "observed_text": observed_text,
            "raw_text": text,
        }

    # Sekcija postoji, ali nema brojčane procjene.
    return {
        "available": True,
        "label": "Nema brojčane procjene",
        "wait_minutes": None,
        "bound": None,
        "scope": scope,
        "observed_at": observed_at,
        "observed_text": observed_text,
        "raw_text": text,
    }


def fetch_bihamk() -> tuple[dict[str, dict], dict]:
    response = requests.get(
        BIHAMK_URL,
        headers=HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = clean(soup.get_text(" ", strip=True))

    report_text, report_at = bihamk_report_time(page_text)

    crossings: dict[str, dict] = {}
    found = 0

    for key, spec in CROSSINGS.items():
        section = bihamk_section_text(
            soup,
            spec["bihamk_names"],
        )

        item = parse_bihamk_wait(
            section,
            report_at,
            report_text,
        )

        crossings[key] = item

        if item["available"]:
            found += 1

    if found == 0:
        raise RuntimeError(
            "BIHAMK: nijedan poznati granični prijelaz nije pronađen. "
            "HTML se možda promijenio."
        )

    source = {
        "name": "BIHAMK",
        "url": BIHAMK_URL,
        "source_updated": report_text,
        "source_updated_at": report_at,
    }

    return crossings, source


def main() -> int:
    response = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        timeout=25,
    )
    response.raise_for_status()

    # requests ponekad pogrešno zaključi encoding; HAK je UTF-8.
    response.encoding = response.apparent_encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = clean(soup.get_text(" ", strip=True))

    source_updated, source_updated_at = source_timestamp(page_text)

    # BIHAMK je drugi, nezavisni izvor.
    # Ako BIHAMK privremeno padne, HAK feed i dalje ostaje upotrebljiv.
    bihamk_crossings = {}
    bihamk_source = {
        "name": "BIHAMK",
        "url": BIHAMK_URL,
        "source_updated": None,
        "source_updated_at": None,
        "error": None,
    }

    try:
        bihamk_crossings, bihamk_source = fetch_bihamk()
    except Exception as exc:
        bihamk_source["error"] = str(exc)
        print(f"UPOZORENJE BIHAMK: {exc}", file=sys.stderr)

    result = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        # "source" ostaje radi kompatibilnosti sa trenutnom Flutter verzijom.
        "source": {
            "name": "HAK / MUP RH",
            "url": SOURCE_URL,
            "source_updated": source_updated,
            "source_updated_at": source_updated_at,
        },
        "sources": {
            "hak": {
                "name": "HAK / MUP RH",
                "url": SOURCE_URL,
                "source_updated": source_updated,
                "source_updated_at": source_updated_at,
            },
            "bihamk": bihamk_source,
        },
        "crossings": {},
    }

    found = 0

    for key, spec in CROSSINGS.items():
        item = parse_crossing(soup, spec)

        item["bihamk"] = bihamk_crossings.get(
            key,
            {
                "available": False,
                "label": "Nema podataka",
                "wait_minutes": None,
                "bound": None,
                "scope": "unspecified",
                "observed_at": bihamk_source.get("source_updated_at"),
                "observed_text": bihamk_source.get("source_updated"),
                "raw_text": None,
            },
        )

        result["crossings"][key] = item

        if item["available"]:
            found += 1

    # Zaštita od toga da HAK potpuno promijeni HTML, a mi ipak
    # commitamo prazan/krivi JSON kao da je ispravan.
    if found == 0:
        raise RuntimeError(
            "Nije pronađen nijedan poznati BiH granični prijelaz "
            "u HAK tablici. HTML se možda promijenio."
        )

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"HAK/MUP podaci učitani. "
        f"Pronađeno prijelaza: {found}/{len(CROSSINGS)}"
    )

    for key, item in result["crossings"].items():
        bihamk = item.get("bihamk", {})
        print(
            f"- {key}: "
            f"HAK prema BiH={item['to_bih']['label']}; "
            f"HAK prema HR={item['to_croatia']['label']}; "
            f"BIHAMK={bihamk.get('label', 'Nema podataka')}"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GREŠKA: {exc}", file=sys.stderr)
        raise
