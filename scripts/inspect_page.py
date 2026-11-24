"""Dump the open Resdex page and its visible form controls for selector discovery."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app.config import get_local_cdp_url  # noqa: E402


def main() -> None:
    output_dir = PROJECT_ROOT / "data" / "debug"
    output_dir.mkdir(parents=True, exist_ok=True)
    page_path = output_dir / "page.html"
    screenshot_path = output_dir / "screenshot.png"
    inputs_path = output_dir / "inputs.json"
    cdp_url = get_local_cdp_url()

    print(
        "WARNING: page captures may contain personal candidate data. "
        "Files are written under the git-ignored data\\debug folder."
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=10000)
        pages = [
            page for context in browser.contexts for page in context.pages
            if "resdex.naukri.com" in page.url.lower()
        ]
        if not pages:
            raise RuntimeError(
                "No Resdex tab is open in the Chrome window attached to CDP."
            )

        page = pages[0]
        page_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(screenshot_path), full_page=True)
        controls = page.evaluate(
            """() => Array.from(document.querySelectorAll(
                "input, textarea, button, select"
            )).map((element) => {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                const visible = style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    rect.width > 0 && rect.height > 0;
                return {
                    tag: element.tagName.toLowerCase(),
                    type: element.getAttribute("type"),
                    placeholder: element.getAttribute("placeholder"),
                    aria_label: element.getAttribute("aria-label"),
                    name: element.getAttribute("name"),
                    id: element.id || null,
                    visible_text: (element.innerText || element.textContent || "")
                        .trim().slice(0, 200),
                    visible,
                    outer_html: element.outerHTML.slice(0, 500)
                };
            })"""
        )
        inputs_path.write_text(
            json.dumps(controls, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"Page HTML: {page_path}")
    print(f"Screenshot: {screenshot_path}")
    print(f"Controls JSON: {inputs_path}")


if __name__ == "__main__":
    main()
