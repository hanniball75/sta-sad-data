import json
import time
from playwright.sync_api import sync_playwright

URLS = [
    "https://granica.rs/prelaz/hr-gornji-varos",
    "https://granica.rs/prelaz/hr-svilaj",
]

def short(value, limit=1800):
    value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + "... [truncated]"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            locale="sr-RS",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
        )

        for url in URLS:
            print("\n" + "=" * 90)
            print("PAGE:", url)
            print("=" * 90)

            page = context.new_page()

            seen = set()

            def on_response(response):
                req = response.request
                rtype = req.resource_type
                resp_url = response.url

                # Fokus na stvari koje mogu nositi live podatke.
                interesting = (
                    rtype in {"xhr", "fetch", "document"}
                    or "api" in resp_url.lower()
                    or "json" in resp_url.lower()
                    or "measure" in resp_url.lower()
                    or "wait" in resp_url.lower()
                    or "border" in resp_url.lower()
                    or "prelaz" in resp_url.lower()
                )

                if not interesting:
                    return

                key = (rtype, resp_url)
                if key in seen:
                    return
                seen.add(key)

                try:
                    content_type = response.headers.get(
                        "content-type", ""
                    )
                except Exception:
                    content_type = ""

                print(
                    f"\nRESPONSE type={rtype} "
                    f"status={response.status}"
                )
                print("URL:", resp_url)
                print("CONTENT-TYPE:", content_type)

                if (
                    "application/json" in content_type.lower()
                    or rtype in {"xhr", "fetch"}
                ):
                    try:
                        body = response.text()
                        print("BODY:", short(body))
                    except Exception as exc:
                        print("BODY ERROR:", exc)

            page.on("response", on_response)

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=45000,
            )

            # Dovoljno dugo da frontend odradi inicijalni refresh.
            page.wait_for_timeout(15000)

            print("\n--- PERFORMANCE RESOURCES ---")
            resources = page.evaluate(
                """performance.getEntriesByType('resource')
                .map(x => ({
                  name: x.name,
                  initiatorType: x.initiatorType
                }))"""
            )

            for item in resources:
                name = item.get("name", "")
                initiator = item.get("initiatorType", "")
                lower = name.lower()

                if (
                    initiator in {"fetch", "xmlhttprequest"}
                    or "api" in lower
                    or "json" in lower
                    or "measure" in lower
                    or "wait" in lower
                    or "border" in lower
                ):
                    print(
                        f"{initiator}: {name}"
                    )

            print("\n--- CURRENT PAGE TEXT (selected hints) ---")
            body_text = page.locator("body").inner_text()

            for line in body_text.splitlines():
                l = line.strip()
                low = l.lower()
                if (
                    "meren" in low
                    or "čekanj" in low
                    or "gornji varoš" in low
                    or "svilaj" in low
                ):
                    print(short(l, 500))

            page.close()

        browser.close()

if __name__ == "__main__":
    main()
