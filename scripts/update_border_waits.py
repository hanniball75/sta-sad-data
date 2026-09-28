from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

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
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-gornji-varos",
            "to_croatia": "https://granica.rs/prelaz/ba-gradiska",
        },
    },
    "slavonski_brod_brod": {
        "name": "Slavonski Brod – Brod",
        "aliases": [
            "Slavonski Brod (Bosanski Brod)",
            "Slavonski Brod",
        ],
        "bihamk_names": ["GP Brod", "Brod"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-slavonski-brod",
            "to_croatia": "https://granica.rs/prelaz/ba-brod",
        },
    },
    "svilaj": {
        "name": "Svilaj",
        "aliases": ["Svilaj"],
        "bihamk_names": ["GP Svilaj", "Svilaj"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-svilaj",
            "to_croatia": "https://granica.rs/prelaz/ba-svilaj",
        },
    },
    "zupanja_orasje": {
        "name": "Županja – Orašje",
        "aliases": [
            "Županja (Orašje)",
            "Županja",
        ],
        "bihamk_names": ["GP Orašje", "Orašje"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-zupanja",
            "to_croatia": "https://granica.rs/prelaz/ba-orasje",
        },
    },
    "stara_gradiska_gradiska": {
        "name": "Stara Gradiška – Gradiška",
        "aliases": [
            "Stara Gradiška (Gradiška)",
            "Stara Gradiška",
        ],
        "bihamk_names": [],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-stara-gradiska",
            "to_croatia": "https://granica.rs/prelaz/ba-gradiska",
        },
    },
    "hrvatska_kostajnica_kostajnica": {
        "name": "Hrvatska Kostajnica – Kostajnica",
        "aliases": [
            "Hrvatska Kostajnica (Kostajnica)",
            "Hrvatska Kostajnica",
        ],
        "bihamk_names": ["GP Kostajnica", "Kostajnica"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-hrvatska-kostajnica",
            "to_croatia": "https://granica.rs/prelaz/ba-kostajnica",
        },
    },
    "slavonski_samac_samac": {
        "name": "Slavonski Šamac – Šamac",
        "aliases": [
            "Slavonski Šamac (Šamac)",
            "Slavonski Šamac",
        ],
        "bihamk_names": ["GP Šamac", "Šamac"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-slavonski-samac",
            "to_croatia": "https://granica.rs/prelaz/ba-samac",
        },
    },
    "gunja_brcko": {
        "name": "Gunja – Brčko",
        "aliases": [
            "Gunja (Brčko)",
            "Gunja",
        ],
        "bihamk_names": ["GP Brčko", "Brčko"],
        "granica_rs": {
            "to_bih": "https://granica.rs/prelaz/hr-gunja",
        },
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


def granica_rs_parse_measurement(
    page_text: str,
    expected_direction_text: str,
    url: str,
) -> dict:
    """
    Čita server-renderovani tekst Granica.rs stranice.
    Uzimamo samo PRVI smjerni blok na toj stranici, jer URL već
    predstavlja konkretnu stranu granice i smjer koji nas zanima.
    """

    text = clean(page_text)

    # Npr:
    # "Izlaz - ka BiH"
    # "Velika gužva"
    # "Procena čekanja: ~285-435 min"
    # "Poslednje merenje: 27.09.2026. 14:01"
    direction_match = re.search(
        re.escape(expected_direction_text),
        text,
        flags=re.I,
    )

    if not direction_match:
        return {
            "available": False,
            "label": "Nema podataka",
            "wait_minutes_min": None,
            "wait_minutes_max": None,
            "congestion": None,
            "observed_at": None,
            "observed_text": None,
            "page_url": url,
            "camera_url": None,
            "camera_alt": None,
            "error": "Smjerni blok nije pronađen",
        }

    segment = text[direction_match.end():]
    # ograniči na sljedeći smjerni blok ili alternative
    cut_positions = []

    for token in [
        "Ulaz - ka",
        "Izlaz - ka",
        "Nepoznat smer",
        "Alternativni prelazi",
    ]:
        pos = segment.lower().find(token.lower())
        if pos > 0:
            cut_positions.append(pos)

    if cut_positions:
        segment = segment[:min(cut_positions)]

    wait_match = re.search(
        r"Procena\s+čekanja:\s*"
        r"(do\s+\d+\s*min|~?\s*\d+\s*-\s*\d+\s*min|~?\s*\d+\s*min)",
        segment,
        flags=re.I,
    )

    observed_match = re.search(
        r"Poslednje\s+merenje:\s*"
        r"(\d{1,2}\.\d{1,2}\.\d{4}\.?\s+\d{1,2}:\d{2}(?::\d{2})?)",
        segment,
        flags=re.I,
    )

    # Gužva je obično tekst između smjera i "Procena čekanja".
    congestion = None
    if wait_match:
        before_wait = clean(segment[:wait_match.start()])
        # uzmi zadnju kratku frazu prije procjene
        candidates = [
            "Nema gužve",
            "Mala gužva",
            "Srednja gužva",
            "Velika gužva",
            "Nepoznato",
        ]
        for candidate in candidates:
            if candidate.lower() in before_wait.lower():
                congestion = candidate
                break

    if not wait_match:
        return {
            "available": False,
            "label": "Nema podataka",
            "wait_minutes_min": None,
            "wait_minutes_max": None,
            "congestion": congestion,
            "observed_at": None,
            "observed_text": None,
            "page_url": url,
            "camera_url": None,
            "camera_alt": None,
            "error": "Procjena čekanja nije pronađena",
        }

    raw_label = clean(wait_match.group(1))
    label_norm = normalize(raw_label)

    minimum = None
    maximum = None

    m = re.search(r"do\s+(\d+)\s*min", label_norm)
    if m:
        maximum = int(m.group(1))
        minimum = 0
        label = f"do {maximum} min"
    else:
        m = re.search(r"(\d+)\s*-\s*(\d+)\s*min", label_norm)
        if m:
            minimum = int(m.group(1))
            maximum = int(m.group(2))
            label = f"{minimum}-{maximum} min"
        else:
            m = re.search(r"(\d+)\s*min", label_norm)
            if m:
                minimum = int(m.group(1))
                maximum = minimum
                label = f"oko {minimum} min"
            else:
                label = raw_label

    observed_text = (
        clean(observed_match.group(1))
        if observed_match
        else None
    )
    observed_at = (
        parse_local_datetime(
            observed_text,
            ZoneInfo("Europe/Zagreb"),
        )
        if observed_text
        else None
    )

    return {
        "available": True,
        "label": label,
        "wait_minutes_min": minimum,
        "wait_minutes_max": maximum,
        "congestion": congestion,
        "observed_at": observed_at,
        "observed_text": observed_text,
        "page_url": url,
        "error": None,
    }


def _granica_relative_observed_at(
    text: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Granica.rs live prikaz često koristi relativno vrijeme:
    'pre 17 min', 'pre 2 h', 'pre 1 d'.
    Pretvaramo ga u približno ISO vrijeme.
    """
    normalized = normalize(text)
    now = datetime.now(ZoneInfo("Europe/Zagreb"))

    # npr. "pre 2 h 15 min"
    m = re.search(
        r"\bpre\s+(\d+)\s*h(?:\s+(\d+)\s*min)?\b",
        normalized,
    )
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2) or 0)
        dt = now - timedelta(
            hours=hours,
            minutes=minutes,
        )
        raw = f"pre {hours} h"
        if minutes:
            raw += f" {minutes} min"
        return (
            dt.replace(microsecond=0).isoformat(),
            raw,
        )

    m = re.search(
        r"\bpre\s+(\d+)\s*min\b",
        normalized,
    )
    if m:
        minutes = int(m.group(1))
        dt = now - timedelta(minutes=minutes)
        return (
            dt.replace(microsecond=0).isoformat(),
            f"pre {minutes} min",
        )

    m = re.search(
        r"\bpre\s+(\d+)\s*d\b",
        normalized,
    )
    if m:
        days = int(m.group(1))
        dt = now - timedelta(days=days)
        return (
            dt.replace(microsecond=0).isoformat(),
            f"pre {days} d",
        )

    return None, None


def granica_rs_parse_rendered(
    page_text: str,
    expected_direction_text: str,
    url: str,
) -> dict:
    """
    Čita LIVE karticu Granica.rs iz renderovanog DOM-a.

    Važno:
    - svaka URL stranica već predstavlja konkretan smjer;
    - ne smijemo uzeti broj iz FAQ / statističkog teksta niže na stranici;
    - novi dizajn često prikazuje običan format "10 min",
      a ne samo "do 10 min".
    """
    full_text = clean(page_text)

    # Granica.rs ispod live kartice ima FAQ / opis sa istorijskim primjerima
    # poput "oko 10 minuta". To nije trenutno mjerenje.
    # Zato parser ograničavamo samo na gornji LIVE dio stranice.
    normalized_full = normalize(full_text)
    live_text = full_text

    faq_markers = [
        "cesta pitanja",
        "česta pitanja",
        "koliko je guzva",
        "koliko je gužva",
    ]

    cut_positions = []
    for marker in faq_markers:
        pos = normalized_full.find(normalize(marker))
        if pos > 0:
            cut_positions.append(pos)

    if cut_positions:
        live_text = full_text[:min(cut_positions)]

    normalized = normalize(live_text)

    # Prvo probaj klasični format ako ga stranica još uvijek prikazuje.
    parsed = granica_rs_parse_measurement(
        live_text,
        expected_direction_text,
        url,
    )

    # Relativno vrijeme ("pre 4 min") tražimo ISKLJUČIVO u live dijelu.
    relative_at, relative_text = _granica_relative_observed_at(
        live_text,
    )

    if parsed.get("available"):
        if relative_at is not None:
            parsed["observed_at"] = relative_at
            parsed["observed_text"] = relative_text
        parsed["rendered_with_browser"] = True
        return parsed

    # Novi Granica.rs prikaz može izgledati:
    #
    # ČEKANJE · IZLAZ IZ HRVATSKE
    # 10
    # min
    # Mala gužva
    # 1 vozilo u koloni
    # poslednje merenje
    # pre 4 min
    #
    # clean() spaja novi red pa dobijamo "10 min".
    # Na novom Granica.rs prikazu "poslednje merenje pre 16 min"
    # može stajati prije same procjene čekanja. Zato NE uzimamo prvi
    # broj koji završava na "min", nego preskačemo sve kandidate koji
    # su dio izraza "pre X min".
    wait_match = None

    candidate_pattern = re.compile(
        r"\b("
        r"do\s+\d+\s*min(?:uta)?|"
        r"\d+\s*-\s*\d+\s*min(?:uta)?|"
        r"oko\s+\d+\s*min(?:uta)?|"
        r"\d+\s*min(?:uta)?"
        r")\b",
        flags=re.I,
    )

    for candidate_match in candidate_pattern.finditer(normalized):
        before = normalized[
            max(0, candidate_match.start() - 40):
            candidate_match.start()
        ]

        # "pre 16 min" = starost mjerenja, NIJE čekanje.
        if re.search(r"\bpre\s*$", before):
            continue

        # Dodatna zaštita ako je tekst oko timestamp-a drugačije složen.
        if (
            "poslednje merenje" in before[-35:]
            and re.search(r"\bpre\s+\d*\s*$", before[-20:])
        ):
            continue

        wait_match = candidate_match
        break

    if not wait_match:
        parsed["error"] = (
            "Live procjena čekanja nije pronađena u gornjem dijelu stranice"
        )
        return parsed

    raw_label = clean(wait_match.group(1))
    label_norm = normalize(raw_label)

    minimum = None
    maximum = None
    bound = "estimate"

    m = re.search(
        r"do\s+(\d+)\s*min",
        label_norm,
    )
    if m:
        minimum = 0
        maximum = int(m.group(1))
        label = f"do {maximum} min"
        bound = "upper"
    else:
        m = re.search(
            r"(\d+)\s*-\s*(\d+)\s*min",
            label_norm,
        )
        if m:
            minimum = int(m.group(1))
            maximum = int(m.group(2))
            label = f"{minimum}-{maximum} min"
            bound = "range"
        else:
            m = re.search(
                r"oko\s+(\d+)\s*min",
                label_norm,
            )
            if m:
                minimum = int(m.group(1))
                maximum = minimum
                label = f"oko {minimum} min"
            else:
                m = re.search(
                    r"(\d+)\s*min",
                    label_norm,
                )
                if m:
                    minimum = int(m.group(1))
                    maximum = minimum
                    # Ako Granica.rs kaže "10 min", zadrži upravo to.
                    label = f"{minimum} min"
                else:
                    label = raw_label

    congestion = None
    for candidate in [
        "Nema gužve",
        "Mala gužva",
        "Srednja gužva",
        "Velika gužva",
    ]:
        if normalize(candidate) in normalized:
            congestion = candidate
            break

    # Ako live prikaz nema relativno vrijeme, probaj apsolutni datum,
    # ali opet samo iz LIVE dijela.
    observed_at = relative_at
    observed_text = relative_text

    if observed_at is None:
        m = re.search(
            r"Poslednje\s+merenje:\s*"
            r"(\d{1,2}\.\d{1,2}\.\d{4}\.?\s+"
            r"\d{1,2}:\d{2}(?::\d{2})?)",
            live_text,
            flags=re.I,
        )
        if m:
            observed_text = clean(m.group(1))
            observed_at = parse_local_datetime(
                observed_text,
                ZoneInfo("Europe/Zagreb"),
            )

    return {
        "available": True,
        "label": label,
        "wait_minutes_min": minimum,
        "wait_minutes_max": maximum,
        "bound": bound,
        "congestion": congestion,
        "observed_at": observed_at,
        "observed_text": observed_text,
        "page_url": url,
        "rendered_with_browser": True,
        "error": None,
    }


class GranicaRsFetcher:
    """
    Granica.rs LIVE fetcher.

    Pouzdanost ima prednost:
    - jedan Chromium se koristi za SVE prelaze i oba smjera;
    - ista browser stranica se ponovo koristi za svaku navigaciju;
    - ako prvi kadar DOM-a izgleda star, kratko sačekamo i čitamo ponovo;
    - requests je samo rezervni fallback ako browser potpuno zakaže.

    Time dobijamo stvarni JS-renderovani live podatak bez pokretanja
    novog browser procesa za svaki prelaz.
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                **HEADERS,
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            }
        )

        self.cache: dict[tuple[str, str], dict] = {}

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        self.stats = {
            "browser_ok": 0,
            "browser_retry": 0,
            "http_fallback": 0,
            "cache_hits": 0,
            "failures": 0,
        }

    def _cache_bust_url(self, url: str) -> str:
        stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}_sta_sad={stamp}"

    def _is_fresh_measurement(
        self,
        parsed: dict,
        max_age_minutes: int = 35,
    ) -> bool:
        if not parsed.get("available"):
            return False

        observed_at = parsed.get("observed_at")

        # Ako Granica.rs daje procjenu ali ne daje vrijeme,
        # prihvati podatak; Flutter će ga kasnije označiti kako treba.
        if not observed_at:
            return True

        try:
            dt = datetime.fromisoformat(
                str(observed_at).replace("Z", "+00:00")
            )

            now = datetime.now(dt.tzinfo or timezone.utc)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            age = now - dt

            return age <= timedelta(
                minutes=max_age_minutes,
            )
        except Exception:
            return True

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return

        self._playwright = sync_playwright().start()

        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        self._context = self._browser.new_context(
            locale="sr-RS",
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

        # Jedna stranica se koristi za sve Granica.rs URL-ove.
        self._page = self._context.new_page()

    def _read_browser_dom(
        self,
        url: str,
        expected_direction_text: str,
    ) -> dict:
        body_text = self._page.locator(
            "body"
        ).inner_text()

        parsed = granica_rs_parse_rendered(
            body_text,
            expected_direction_text,
            url,
        )

        try:
            images = self._page.locator(
                "img"
            ).evaluate_all(
                """
                imgs => imgs.map(img => ({
                  src: img.currentSrc ||
                       img.src ||
                       img.dataset.src ||
                       '',
                  alt: img.alt || ''
                }))
                """
            )

            snapshot = next(
                (
                    item
                    for item in images
                    if "granicars-snapshots"
                    in item.get("src", "")
                ),
                None,
            )

            if snapshot:
                parsed["camera_url"] = snapshot.get("src")
                parsed["camera_alt"] = snapshot.get("alt")
            else:
                parsed["camera_url"] = None
                parsed["camera_alt"] = None
        except Exception:
            parsed["camera_url"] = None
            parsed["camera_alt"] = None

        parsed["transport"] = "browser"
        parsed["rendered_with_browser"] = True

        return parsed

    def _browser_fetch(
        self,
        url: str,
        expected_direction_text: str,
    ) -> dict:
        self._ensure_browser()

        self._page.goto(
            self._cache_bust_url(url),
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Većina Granica.rs stranica završi JS prikaz vrlo brzo.
        self._page.wait_for_timeout(1200)

        parsed = self._read_browser_dom(
            url,
            expected_direction_text,
        )

        if self._is_fresh_measurement(parsed):
            return parsed

        # Ako je prvi DOM još pokazao server-renderovanu/staru vrijednost,
        # sačekaj JS update pa pročitaj ISTU stranicu ponovo.
        self.stats["browser_retry"] += 1
        self._page.wait_for_timeout(2800)

        return self._read_browser_dom(
            url,
            expected_direction_text,
        )

    def _requests_fallback(
        self,
        url: str,
        expected_direction_text: str,
    ) -> dict:
        response = self.session.get(
            self._cache_bust_url(url),
            timeout=18,
        )
        response.raise_for_status()
        response.encoding = (
            response.apparent_encoding
            or "utf-8"
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        body_text = soup.get_text(
            "\n",
            strip=True,
        )

        parsed = granica_rs_parse_rendered(
            body_text,
            expected_direction_text,
            url,
        )

        parsed["camera_url"] = None
        parsed["camera_alt"] = None
        parsed["transport"] = "requests_fallback"

        return parsed

    def fetch(
        self,
        url: str,
        expected_direction_text: str,
    ) -> dict:
        key = (
            url,
            expected_direction_text,
        )

        cached = self.cache.get(key)

        if cached is not None:
            self.stats["cache_hits"] += 1
            return dict(cached)

        browser_error = None

        try:
            parsed = self._browser_fetch(
                url,
                expected_direction_text,
            )

            self.stats["browser_ok"] += 1
            self.cache[key] = dict(parsed)
            return parsed

        except Exception as exc:
            browser_error = str(exc)

        # Samo ako browser ne može otvoriti/obraditi stranicu,
        # koristi HTTP kao rezervni podatak.
        try:
            parsed = self._requests_fallback(
                url,
                expected_direction_text,
            )

            self.stats["http_fallback"] += 1

            if browser_error:
                parsed["browser_error"] = browser_error

            self.cache[key] = dict(parsed)
            return parsed

        except Exception as exc:
            self.stats["failures"] += 1

            return {
                "available": False,
                "label": "Nema podataka",
                "wait_minutes_min": None,
                "wait_minutes_max": None,
                "congestion": None,
                "observed_at": None,
                "observed_text": None,
                "page_url": url,
                "camera_url": None,
                "camera_alt": None,
                "transport": None,
                "error": (
                    f"browser={browser_error}; "
                    f"http={exc}"
                ),
            }

    def close(self) -> None:
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass

        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass

        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass

        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass

        try:
            self.session.close()
        except Exception:
            pass


def fetch_granica_rs_page(
    fetcher: GranicaRsFetcher,
    url: str,
    expected_direction_text: str,
) -> dict:
    return fetcher.fetch(
        url,
        expected_direction_text,
    )


def fetch_granica_rs_for_crossing(
    spec: dict,
    fetcher: GranicaRsFetcher,
) -> dict:
    urls = spec.get("granica_rs", {})

    result = {
        "source": "Granica.rs",
        "to_bih": None,
        "to_croatia": None,
        "error": None,
    }

    try:
        if urls.get("to_bih"):
            result["to_bih"] = fetch_granica_rs_page(
                fetcher,
                urls["to_bih"],
                "Izlaz - ka BiH",
            )
    except Exception as exc:
        result["to_bih"] = {
            "available": False,
            "label": "Nema podataka",
            "page_url": urls.get("to_bih"),
            "error": str(exc),
        }

    try:
        if urls.get("to_croatia"):
            result["to_croatia"] = fetch_granica_rs_page(
                fetcher,
                urls["to_croatia"],
                "Izlaz - ka Hrvatskoj",
            )
    except Exception as exc:
        result["to_croatia"] = {
            "available": False,
            "label": "Nema podataka",
            "page_url": urls.get("to_croatia"),
            "error": str(exc),
        }

    return result


def main() -> int:
    # HAK je samo jedan od više nezavisnih izvora.
    # Ako privremeno padne ili promijeni HTML, ne smijemo
    # srušiti BIHAMK + Granica.rs + kamere.
    soup = BeautifulSoup("", "html.parser")
    page_text = ""
    source_updated = None
    source_updated_at = None
    hak_error = None

    try:
        response = requests.get(
            SOURCE_URL,
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()

        # requests ponekad pogrešno zaključi encoding; HAK je UTF-8.
        response.encoding = response.apparent_encoding or "utf-8"

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )
        page_text = clean(
            soup.get_text(" ", strip=True)
        )

        source_updated, source_updated_at = (
            source_timestamp(page_text)
        )
    except Exception as exc:
        hak_error = str(exc)
        print(
            f"UPOZORENJE HAK: {exc}",
            file=sys.stderr,
        )

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
            "error": hak_error,
        },
        "sources": {
            "hak": {
                "name": "HAK / MUP RH",
                "url": SOURCE_URL,
                "source_updated": source_updated,
                "source_updated_at": source_updated_at,
                "error": hak_error,
            },
            "bihamk": bihamk_source,
            "granica_rs": {
                "name": "Granica.rs",
                "url": "https://granica.rs/",
                "note": "Procjene sa javnih kamera; shared Chromium live fetch v2",
            },
        },
        "crossings": {},
    }

    found = 0

    # Jedan zajednički Granica.rs fetcher za sve prelaze.
    granica_fetcher = GranicaRsFetcher()

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

        item["granica_rs"] = fetch_granica_rs_for_crossing(
            spec,
            granica_fetcher,
        )

        result["crossings"][key] = item

        if item["available"]:
            found += 1

    granica_fetcher.close()

    print(
        "Granica.rs fetch statistika: "
        f"browser={granica_fetcher.stats['browser_ok']}; "
        f"browser_retry={granica_fetcher.stats['browser_retry']}; "
        f"http_fallback={granica_fetcher.stats['http_fallback']}; "
        f"cache_hits={granica_fetcher.stats['cache_hits']}; "
        f"failures={granica_fetcher.stats['failures']}"
    )

    # HAK više nije "single point of failure".
    # Ako nije pronađen nijedan poznati prijelaz, samo označi
    # izvor kao privremeno nedostupan / promijenjen HTML.
    if found == 0:
        message = (
            "HAK trenutno nije vratio nijedan poznati BiH "
            "granični prijelaz. Nastavljam sa BIHAMK i Granica.rs."
        )

        if hak_error is None:
            hak_error = message
            result["source"]["error"] = hak_error
            result["sources"]["hak"]["error"] = hak_error

        print(
            f"UPOZORENJE HAK: {message}",
            file=sys.stderr,
        )

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if found > 0:
        print(
            f"HAK/MUP podaci učitani. "
            f"Pronađeno prijelaza: {found}/{len(CROSSINGS)}"
        )
    else:
        print(
            "HAK/MUP trenutno bez upotrebljivih prijelaza; "
            "BIHAMK i Granica.rs se ipak obrađuju."
        )

    for key, item in result["crossings"].items():
        bihamk = item.get("bihamk", {})
        granica = item.get("granica_rs", {})
        grs_bih_data = granica.get("to_bih") or {}
        grs_hr_data = granica.get("to_croatia") or {}

        grs_bih = grs_bih_data.get(
            "label",
            "Nema podataka",
        )
        grs_hr = grs_hr_data.get(
            "label",
            "Nema podataka",
        )

        grs_bih_time = grs_bih_data.get(
            "observed_text",
        ) or "bez vremena"

        grs_hr_time = grs_hr_data.get(
            "observed_text",
        ) or "bez vremena"

        print(
            f"- {key}: "
            f"HAK->BiH={item['to_bih']['label']}; "
            f"HAK->HR={item['to_croatia']['label']}; "
            f"BIHAMK={bihamk.get('label', 'Nema podataka')}; "
            f"Granica.rs->BiH={grs_bih} ({grs_bih_time}); "
            f"Granica.rs->HR={grs_hr} ({grs_hr_time})"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"GREŠKA: {exc}", file=sys.stderr)
        raise
