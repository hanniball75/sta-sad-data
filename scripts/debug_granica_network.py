import re
import time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

BASE_URLS = [
    "https://granica.rs/prelaz/hr-gornji-varos",
    "https://granica.rs/prelaz/hr-svilaj",
]

def compact(s):
    return re.sub(r"\s+", " ", s or "").strip()

def extract_measurement_block(text):
    lines = [compact(x) for x in text.splitlines() if compact(x)]
    interesting = []

    for i, line in enumerate(lines):
        low = line.lower()
        if (
            "procena čekanja" in low
            or "poslednje merenje" in low
            or "nema gužve" in low
            or "mala gužva" in low
            or "velika gužva" in low
        ):
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            for item in lines[start:end]:
                if item not in interesting:
                    interesting.append(item)

    return interesting[:30]

def dump_headers(resp):
    headers = resp.headers
    keys = [
        "date",
        "age",
        "cache-control",
        "cf-cache-status",
        "etag",
        "last-modified",
        "server",
        "vary",
    ]
    for key in keys:
        print(f"{key}: {headers.get(key, '<nema>')}")

def load(page, url, label):
    print("\n" + "=" * 100)
    print(label)
    print("LOCAL UTC:", datetime.now(timezone.utc).isoformat())
    print("URL:", url)

    resp = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=45000,
    )

    page.wait_for_timeout(5000)

    if resp:
        print("STATUS:", resp.status)
        print("--- RESPONSE HEADERS ---")
        dump_headers(resp)
    else:
        print("NO MAIN DOCUMENT RESPONSE")

    body_text = page.locator("body").inner_text()

    print("--- EXTRACTED LIVE TEXT ---")
    block = extract_measurement_block(body_text)
    if block:
        for line in block:
            print(line)
    else:
        print("Nije pronađen blok mjerenja.")

    print("--- PAGE TITLE ---")
    print(page.title())

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(
            locale="sr-RS",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
            extra_http_headers={
                "Cache-Control": "no-cache, no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

        page = context.new_page()

        for base in BASE_URLS:
            # 1) Normalna adresa
            load(page, base, "TEST 1 — NORMAL URL")

            # 2) Cache-busting query parametar
            stamp = int(time.time())
            bust = f"{base}?_sta_sad={stamp}"
            load(page, bust, "TEST 2 — CACHE-BUST URL")

            # 3) Drugi cache-bust da vidimo da li se odgovor mijenja
            time.sleep(2)
            stamp2 = int(time.time())
            bust2 = f"{base}?_sta_sad={stamp2}"
            load(page, bust2, "TEST 3 — SECOND CACHE-BUST URL")

        browser.close()

if __name__ == "__main__":
    main()
