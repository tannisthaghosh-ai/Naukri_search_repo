"""Playwright CDP scraping for a user-authenticated Resdex session."""
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.sync_api import (
    Browser,
    Locator,
    Page,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from . import db
from .config import get_local_cdp_url

logger = logging.getLogger(__name__)

CDP_URL = get_local_cdp_url()
ADVANCED_SEARCH_URL = "https://resdex.naukri.com/v3?activeTab=advSrch"
NOT_LOGGED_IN = (
    "Not logged in to Resdex in the browser window. "
    "Log in there, then try again."
)

# Semantic locators captured from data/debug_form/inputs.json.
RESDEX_SELECTORS = {
    "role_labels": ("Enter keywords like skills, designation and company",),
    "role_placeholders": ("Enter keywords like skills, designation and company",),
    "min_experience_placeholder": "Min experience",
    "max_experience_placeholder": "Max experience",
    "location_labels": ("Add location",),
    "location_placeholders": ("Add location",),
    "submit_button": "#adv-search-btn",
    "candidate_links": 'a[href*="/v3/preview"][href*="uniqId="]',
    "next_name": re.compile(r"next", re.IGNORECASE),
}

EXTRACT_JS = """(linkSelector) => {
    const links = Array.from(document.querySelectorAll(linkSelector));
    return links.map((link) => {
        let card = link;
        let node = link;
        for (let depth = 0; depth < 10 && node; depth += 1) {
            const text = (node.innerText || node.textContent || "").trim();
            if (
                text.length >= 40 &&
                text.length <= 8000 &&
                /key skills/i.test(text) &&
                /(?:current|previous)/i.test(text)
            ) {
                card = node;
                break;
            }
            node = node.parentElement;
        }
        return {
            name: (link.innerText || link.textContent || "").trim(),
            profile_url: link.href,
            raw_text: (card.innerText || card.textContent || "").trim()
        };
    });
}"""


class ScraperError(RuntimeError):
    """A user-safe error that can be stored on a search job."""


def _is_login_url(url: str) -> bool:
    lowered = url.lower()
    return "login" in lowered or "signin" in lowered or "sign-in" in lowered


def _visible_first(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _find_textbox(page: Page, labels: tuple[str, ...], placeholders: tuple[str, ...]) -> Locator | None:
    for label in labels:
        candidate = _visible_first(
            page.get_by_role("textbox", name=re.compile(re.escape(label), re.IGNORECASE))
        )
        if candidate:
            return candidate
        candidate = _visible_first(page.get_by_label(label, exact=False))
        if candidate:
            return candidate

    for placeholder in placeholders:
        candidate = _visible_first(page.get_by_placeholder(placeholder, exact=False))
        if candidate:
            return candidate
    return None


def _accept_suggestion(page: Page, textbox: Locator) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        options = _visible_first(page.get_by_role("option"))
        if options:
            options.click()
            return

        listbox = _visible_first(page.get_by_role("listbox"))
        if listbox or textbox.get_attribute("aria-expanded") == "true":
            break
        time.sleep(0.1)

    textbox.press("Enter")
    try:
        page.wait_for_url("**/v3/search**", timeout=1500)
    except PlaywrightTimeoutError:
        pass


def _connect_browser(playwright) -> Browser:
    try:
        return playwright.chromium.connect_over_cdp(CDP_URL, timeout=10000)
    except PlaywrightError as exc:
        port = urlparse(CDP_URL).port or 9222
        raise ScraperError(
            f"Could not connect to Edge/Chrome on port {port}. "
            "Start it with start-edge.ps1 and log in."
        ) from exc


def _new_search_page(browser: Browser) -> Page:
    if not browser.contexts:
        raise ScraperError("Edge/Chrome CDP is connected but has no browser context.")
    return browser.contexts[0].new_page()


def _keyword_chip_present(page: Page, keyword: str) -> bool:
    buttons = page.get_by_role(
        "button",
        name=re.compile(re.escape(keyword), re.IGNORECASE),
    )
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if not button.is_visible():
            continue
        accessible_name = button.get_attribute("aria-label") or button.inner_text()
        if re.search(r"\b(?:remove|unmark)\b", accessible_name, re.IGNORECASE):
            return True
    return False


def _keyword_committed(page: Page, textbox: Locator, keyword: str) -> bool:
    value = textbox.input_value().strip()
    return keyword.casefold() in value.casefold() or _keyword_chip_present(
        page,
        keyword,
    )


def _search_button_enabled(button: Locator) -> bool:
    return (
        button.is_visible()
        and button.is_enabled()
        and button.get_attribute("aria-disabled") != "true"
    )


def _wait_for_search_button(page: Page, timeout: float = 8) -> Locator | None:
    button = page.locator(RESDEX_SELECTORS["submit_button"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _search_button_enabled(button):
            return button
        time.sleep(0.1)
    return None


def _disabled_search_error() -> ScraperError:
    return ScraperError(
        "Resdex did not enable the Search candidates button after keyword entry. "
        "Check the form and inspect_page.py."
    )


def _wait_for_role_field(page: Page) -> Locator:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _is_login_url(page.url):
            raise ScraperError(NOT_LOGGED_IN)
        field = _find_textbox(
            page,
            RESDEX_SELECTORS["role_labels"],
            RESDEX_SELECTORS["role_placeholders"],
        )
        if field:
            return field
        time.sleep(0.25)
    if _is_login_url(page.url):
        raise ScraperError(NOT_LOGGED_IN)
    raise ScraperError("Could not find the Resdex Keywords field.")


def _extract_candidates(page: Page) -> list[dict]:
    records = page.evaluate(EXTRACT_JS, RESDEX_SELECTORS["candidate_links"])
    return [
        parse_card(
            record.get("raw_text", ""),
            record.get("name", ""),
            record.get("profile_url", ""),
        )
        for record in records
    ]


def _uniq_id(profile_url: str) -> str:
    values = parse_qs(urlparse(profile_url).query)
    return values.get("uniqId", [profile_url])[0]


def _next_button(page: Page) -> Locator | None:
    button = _visible_first(page.get_by_role("button", name=RESDEX_SELECTORS["next_name"]))
    if button:
        return button
    return _visible_first(page.get_by_role("link", name=RESDEX_SELECTORS["next_name"]))


def _collect_results(
    job_id: str,
    page: Page,
    max_pages: int,
) -> list[dict]:
    try:
        page.wait_for_selector(RESDEX_SELECTORS["candidate_links"], timeout=15000)
    except PlaywrightTimeoutError as exc:
        if _is_login_url(page.url):
            raise ScraperError(NOT_LOGGED_IN) from exc
        raise ScraperError(
            "Resdex search results did not render candidate profiles. "
            "Check the page and selector discovery output."
        ) from exc

    results: list[dict] = []
    seen: set[str] = set()
    for page_number in range(1, max_pages + 1):
        for candidate in _extract_candidates(page):
            key = _uniq_id(candidate["profile_url"])
            if key and key not in seen:
                seen.add(key)
                candidate["page"] = page_number
                results.append(candidate)

        db.update_job_progress(
            job_id,
            {"page": page_number, "found": len(results)},
            results,
        )
        if page_number >= max_pages:
            break

        next_button = _next_button(page)
        if next_button is None or not next_button.is_enabled():
            break

        selector = RESDEX_SELECTORS["candidate_links"]
        previous_links = page.locator(selector).evaluate_all(
            "(anchors) => anchors.map((anchor) => anchor.href).join('\\n')"
        )
        next_button.click()
        time.sleep(3)
        try:
            page.wait_for_function(
                """(args) => {
                    const urls = Array.from(
                        document.querySelectorAll(args.selector)
                    ).map((anchor) => anchor.href);
                    return urls.length > 0 && urls.join("\\n") !== args.previous;
                }""",
                arg={"selector": selector, "previous": previous_links},
                timeout=15000,
            )
        except PlaywrightTimeoutError as exc:
            raise ScraperError("Resdex did not load the next results page.") from exc

    return results


def parse_card(raw_text: str, name: str, profile_url: str) -> dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    normalized_text = "\n".join(lines)
    flat_text = " ".join(normalized_text.split())
    section_labels = (
        r"Current|Previous|Fresher|Education|Pref\.?\s*locations?|"
        r"Key\s+skills|May\s+also\s+know"
    )
    flat_text = re.sub(
        rf"(?<=[A-Za-z0-9])(?=(?:{section_labels}))",
        " ",
        flat_text,
        flags=re.IGNORECASE,
    )
    flat_text = re.sub(
        r"(Current|Previous|Fresher|Key\s+skills|May\s+also\s+know|Education|"
        r"Pref\.?\s*locations?)(?=[A-Za-z0-9])",
        r"\1 ",
        flat_text,
        flags=re.IGNORECASE,
    )
    experience_match = re.search(
        r"(?<!\d)\d+(?:\.\d+)?\s*(?:y|yr|yrs|year|years)"
        r"(?:\s*\d+\s*(?:m|mo|mos|month|months))?\b",
        flat_text,
        re.IGNORECASE,
    )
    experience = None
    if experience_match:
        experience = experience_match.group(0).strip()
    elif re.search(r"\bfresher\b", flat_text, re.IGNORECASE):
        experience = "Fresher"

    company_pattern = (
        r"\b{marker}\s+.+?\s+at\s+(.+?)"
        r"(?=\b(?:Current|Previous|Education|Pref\.?\s*locations?|"
        r"Key\s+skills|May\s+also\s+know)\b|$)"
    )
    company_match = re.search(
        company_pattern.format(marker="Current"),
        flat_text,
        re.IGNORECASE,
    ) or re.search(
        company_pattern.format(marker="Previous"),
        flat_text,
        re.IGNORECASE,
    )

    skills_match = re.search(
        r"key\s+skills\s*:?\s*(.+?)(?=may\s+also\s+know|education|$)",
        flat_text,
        re.IGNORECASE | re.DOTALL,
    )
    skills_text = skills_match.group(1) if skills_match else ""
    skills = [
        item.strip(" \t,;|")
        for item in re.split(r"[,;|\n]", skills_text)
        if item.strip(" \t,;|")
    ]

    return {
        "name": name.strip() or None,
        "profile_url": urljoin("https://resdex.naukri.com", profile_url),
        "experience": experience,
        "current_company": company_match.group(1).strip() if company_match else None,
        "skills": skills,
        "raw_text": normalized_text,
    }


def run_search(
    job_id: str,
    keyword: str,
    max_pages: int = 10,
    location: str | None = None,
) -> None:
    try:
        with sync_playwright() as playwright:
            browser = _connect_browser(playwright)
            try:
                page = _new_search_page(browser)
            except ScraperError:
                raise

            try:
                try:
                    page.goto(
                        ADVANCED_SEARCH_URL,
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                except PlaywrightTimeoutError as exc:
                    if _is_login_url(page.url):
                        raise ScraperError(NOT_LOGGED_IN) from exc
                    raise ScraperError(
                        "Resdex advanced-search page did not load in time."
                    ) from exc
                if _is_login_url(page.url):
                    raise ScraperError(NOT_LOGGED_IN)

                keyword_field = _wait_for_role_field(page)
                keyword_field.click()
                keyword_field.press_sequentially(keyword, delay=60)
                _accept_suggestion(page, keyword_field)
                if (
                    "/v3/search" not in page.url.lower()
                    and not _keyword_committed(page, keyword_field, keyword)
                ):
                    keyword_field.press("Enter")
                    if (
                        "/v3/search" not in page.url.lower()
                        and not _keyword_committed(page, keyword_field, keyword)
                    ):
                        raise ScraperError(
                            "Resdex did not commit the Keywords value."
                        )

                if location and "/v3/search" not in page.url.lower():
                    location_field = _find_textbox(
                        page,
                        RESDEX_SELECTORS["location_labels"],
                        RESDEX_SELECTORS["location_placeholders"],
                    )
                    if location_field is None:
                        raise ScraperError(
                            "Could not find the Resdex location field."
                        )
                    location_field.click()
                    location_field.fill(location)
                    _accept_suggestion(page, location_field)

                if "/v3/search" not in page.url.lower():
                    submit = _wait_for_search_button(page, timeout=8)
                    if submit is None:
                        keyword_field.press("Enter")
                        if "/v3/search" not in page.url.lower():
                            submit = _wait_for_search_button(page, timeout=8)
                    if submit is None and "/v3/search" not in page.url.lower():
                        raise _disabled_search_error()
                    if submit is not None:
                        try:
                            submit.click(timeout=8000)
                        except PlaywrightTimeoutError as exc:
                            raise _disabled_search_error() from exc

                try:
                    page.wait_for_url("**/v3/search**", timeout=30000)
                except PlaywrightTimeoutError as exc:
                    if _is_login_url(page.url):
                        raise ScraperError(NOT_LOGGED_IN) from exc
                    raise ScraperError(
                        "Resdex search did not open the results page. "
                        "Check the search form selectors."
                    ) from exc

                results = _collect_results(job_id, page, max_pages)
                db.finish_job(job_id, results)
            finally:
                try:
                    page.close()
                except PlaywrightError:
                    logger.warning(
                        "Could not close the temporary Resdex search tab."
                    )
    except Exception as exc:
        logger.error(
            "Search job %s failed (%s).",
            job_id,
            type(exc).__name__,
        )
        if isinstance(exc, ScraperError):
            message = str(exc)
        elif isinstance(exc, PlaywrightTimeoutError):
            message = (
                "Resdex did not respond in time. Check the browser window and try again."
            )
        else:
            message = "Search failed due to an internal error."
        try:
            db.fail_job(job_id, message)
        except Exception as db_exc:
            logger.error(
                "Could not mark search job %s as failed (%s).",
                job_id,
                type(db_exc).__name__,
            )


def check_launcher() -> str | bool:
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=3000)
            pages = [page for context in browser.contexts for page in context.pages]
            if pages and _is_login_url(pages[0].url):
                return "waiting_login"
            return "connected"
    except PlaywrightError:
        logger.info("No CDP browser is reachable on the configured localhost port.")
        return False


def launch_launcher() -> None:
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise ScraperError("LOCALAPPDATA is not set; cannot locate Naukri Launcher.")
    launcher = (
        Path(local_app_data)
        / "Programs"
        / "Naukri Launcher"
        / "Naukri Launcher.exe"
    )
    if not launcher.is_file():
        raise ScraperError("Naukri Launcher executable was not found.")
    port = urlparse(CDP_URL).port or 9222
    subprocess.Popen([str(launcher), f"--remote-debugging-port={port}"])
