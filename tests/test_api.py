import atexit
import os
import tempfile
import threading
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

_TEST_STORAGE = tempfile.TemporaryDirectory(prefix="sieve-api-tests-")
atexit.register(_TEST_STORAGE.cleanup)
os.environ["API_KEY"] = "sieve-test-key"
os.environ["DB_PATH"] = str(Path(_TEST_STORAGE.name) / "startup.db")

from app import db, main, scraper  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sieve.db")
    db.init_db()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def test_search_requires_api_key(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").status_code == 401
    assert client.get(
        "/api/health",
        headers={"X-API-KEY": "sieve-test-key"},
    ).json() == {"status": "ok"}
    response = client.post("/api/search", json={"role": "python developer"})
    assert response.status_code == 401

    response = client.post(
        "/api/search",
        json={"role": "python developer"},
        headers={"X-API-KEY": "wrong-key"},
    )
    assert response.status_code == 401


def test_search_validates_keyword_location_and_page_limit(client):
    headers = {"X-API-KEY": "sieve-test-key"}
    for payload in (
        {"keyword": "x" * 201},
        {"keyword": "python", "location": "x" * 101},
        {"keyword": "python", "max_pages": 101},
    ):
        response = client.post("/api/search", json=payload, headers=headers)
        assert response.status_code == 422


def test_search_job_lifecycle_and_query_alias(client, monkeypatch):
    started = threading.Event()
    allow_progress = threading.Event()
    progress_written = threading.Event()
    allow_finish = threading.Event()
    finished = threading.Event()
    received = {}
    candidate = {
        "name": "Test Candidate",
        "profile_url": "https://resdex.naukri.com/v3/preview?uniqId=test-1",
        "experience": "6 yrs 2 mos",
        "current_company": "Example Corp",
        "skills": ["Python", "FastAPI"],
        "raw_text": "Candidate text",
    }

    def fake_search(job_id, keyword, max_pages=10, location=None):
        received.update(
            job_id=job_id,
            keyword=keyword,
            max_pages=max_pages,
            location=location,
        )
        started.set()
        if not allow_progress.wait(5):
            return
        db.update_job_progress(job_id, {"page": 1, "found": 1}, [candidate])
        progress_written.set()
        if not allow_finish.wait(5):
            return
        db.finish_job(job_id, [candidate])
        finished.set()

    monkeypatch.setattr(main.scraper, "run_search", fake_search)
    headers = {"X-API-KEY": "sieve-test-key"}
    try:
        response = client.post(
            "/api/search",
            json={"query": "python developer", "max_pages": 2, "location": "Kolkata"},
            headers=headers,
        )
        assert response.status_code == 202
        started_job = response.json()
        assert started_job["status"] == "pending"
        assert started_job["job_id"]
        job_id = started_job["job_id"]
        assert started.wait(2)
        assert received == {
            "job_id": job_id,
            "keyword": "python developer",
            "max_pages": 2,
            "location": "Kolkata",
        }

        assert client.post(
            "/api/search",
            json={"role": "another search"},
            headers=headers,
        ).status_code == 409
        assert client.get(f"/api/search/{job_id}", headers=headers).json()["status"] == "pending"

        allow_progress.set()
        assert progress_written.wait(2)
        running = client.get(f"/api/search/{job_id}", headers=headers).json()
        assert running["status"] == "running"
        assert running["progress"] == {"page": 1, "found": 1}

        allow_finish.set()
        assert finished.wait(2)
        done = client.get(f"/api/search/{job_id}", headers=headers).json()
        assert done["status"] == "done"
        assert done["count"] == 1
        assert done["results"] == [candidate]
    finally:
        allow_progress.set()
        allow_finish.set()
        finished.wait(2)


def test_parse_card_extracts_candidate_fields():
    parsed = scraper.parse_card(
        "Example Name4y 8m₹ 12 LacsBengaluru"
        "CurrentSenior Python Developer at Example Corp"
        "PreviousPython Developer at Previous Corp"
        "EducationB.Tech 2021"
        "Key skillsPython | FastAPI | AWS"
        "May also knowDocker",
        "Example Name",
        "https://resdex.naukri.com/v3/preview?uniqId=test-2",
    )

    assert parsed["name"] == "Example Name"
    assert parsed["experience"] == "4y 8m"
    assert parsed["current_company"] == "Example Corp"
    assert parsed["skills"] == ["Python", "FastAPI", "AWS"]

    fresher = scraper.parse_card(
        "New CandidateFresherPuneKey skillsPython | Flask",
        "New Candidate",
        "/v3/preview?uniqId=fresher",
    )
    assert fresher["experience"] == "Fresher"
    assert fresher["profile_url"].startswith("https://resdex.naukri.com/")


def test_search_failure_is_returned_as_json(client, monkeypatch):
    failed = threading.Event()

    def fail_search(job_id, **kwargs):
        if kwargs.get("keyword") != "python developer":
            raise RuntimeError("The search keyword was not passed to the worker.")
        db.fail_job(
            job_id,
            "Could not connect to Edge/Chrome on port 9222. "
            "Start it with start-edge.ps1 and log in.",
        )
        failed.set()

    monkeypatch.setattr(main.scraper, "run_search", fail_search)
    response = client.post(
        "/api/search",
        json={"role": "python developer"},
        headers={"X-API-KEY": "sieve-test-key"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert failed.wait(2)

    result = client.get(
        f"/api/search/{job_id}",
        headers={"X-API-KEY": "sieve-test-key"},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "error"
    assert result.json()["error"] == (
        "Could not connect to Edge/Chrome on port 9222. "
        "Start it with start-edge.ps1 and log in."
    )


def test_unreachable_cdp_fails_job_without_escaping(monkeypatch):
    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    def fail_connect(_playwright):
        raise scraper.ScraperError(
            "Could not connect to Edge/Chrome on port 1. "
            "Start it with start-edge.ps1 and log in."
        )

    monkeypatch.setattr(scraper, "sync_playwright", FakePlaywrightContext)
    monkeypatch.setattr(scraper, "_connect_browser", fail_connect)
    job_id = db.create_job("python developer", max_pages=1)

    scraper.run_search(job_id, "python developer", max_pages=1)

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "error"
    assert job["error_message"] == (
        "Could not connect to Edge/Chrome on port 1. "
        "Start it with start-edge.ps1 and log in."
    )


def test_internal_scraper_failure_does_not_store_or_log_exception_text(
    monkeypatch,
    caplog,
):
    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    def fail_connect(_playwright):
        raise RuntimeError("Synthetic Candidate Name leaked from a browser error")

    monkeypatch.setattr(scraper, "sync_playwright", FakePlaywrightContext)
    monkeypatch.setattr(scraper, "_connect_browser", fail_connect)
    job_id = db.create_job("python developer", max_pages=1)

    scraper.run_search(job_id, "python developer", max_pages=1)

    job = db.get_job(job_id)
    assert job is not None
    assert job["error_message"] == "Search failed due to an internal error."
    assert "Synthetic Candidate Name" not in caplog.text


def test_disabled_search_form_stores_clean_error(monkeypatch):
    class FakeField:
        def __init__(self):
            self.actions = []

        def click(self):
            self.actions.append("click")

        def press_sequentially(self, text, delay):
            self.actions.append(("type", text, delay))

        def press(self, key):
            self.actions.append(("press", key))

    class FakePage:
        url = scraper.ADVANCED_SEARCH_URL

        def __init__(self):
            self.closed = False

        def goto(self, *_args, **_kwargs):
            _ = (_args, _kwargs)
            return None

        def close(self):
            self.closed = True

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, _exc_type, _exc_value, _traceback):
            _ = (_exc_type, _exc_value, _traceback)
            return False

    field = FakeField()
    page = FakePage()
    job_id = db.create_job("python developer", max_pages=1)
    monkeypatch.setattr(scraper, "sync_playwright", FakePlaywrightContext)

    def fake_connect(playwright):
        assert playwright is not None
        return object()

    def fake_new_page(browser):
        assert browser is not None
        return page

    def fake_keyword_field(current_page):
        assert current_page is page
        return field

    monkeypatch.setattr(scraper, "_connect_browser", fake_connect)
    monkeypatch.setattr(scraper, "_new_search_page", fake_new_page)
    monkeypatch.setattr(scraper, "_wait_for_role_field", fake_keyword_field)

    def accept_suggestion(current_page, current_field):
        assert current_page is page
        assert current_field is field

    monkeypatch.setattr(
        scraper,
        "_accept_suggestion",
        accept_suggestion,
    )

    def keyword_committed(current_page, current_field, keyword):
        assert current_page is page
        assert current_field is field
        return keyword == "python developer"

    monkeypatch.setattr(scraper, "_keyword_committed", keyword_committed)

    def disabled_search_button(current_page, timeout=8):
        assert current_page is page
        assert timeout == 8
        return None

    monkeypatch.setattr(
        scraper,
        "_wait_for_search_button",
        disabled_search_button,
    )

    scraper.run_search(job_id, "python developer", max_pages=1)

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "error"
    assert job["error_message"] == (
        "Resdex did not enable the Search candidates button after keyword entry. "
        "Check the form and inspect_page.py."
    )
    assert ("type", "python developer", 60) in field.actions
    assert page.closed


def test_xlsx_export_auth_and_job_state_rules(client):
    headers = {"X-API-KEY": "sieve-test-key"}
    pending_id = db.create_job("python developer")
    endpoint = f"/api/search/{pending_id}/export.xlsx"

    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=headers).status_code == 409

    candidate = {
        "name": "Export Candidate",
        "profile_url": "https://resdex.naukri.com/v3/preview?uniqId=export-1",
        "experience": "3y 2m",
        "current_company": "Example Corp",
        "skills": ["Python"],
        "page": 1,
    }
    db.finish_job(pending_id, [candidate])
    exported = client.get(endpoint, headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(exported.content))
    assert workbook["Candidates"]["A2"].hyperlink.target == candidate["profile_url"]

    empty_error_id = db.create_job("python developer")
    db.fail_job(empty_error_id, "Search failed.")
    assert client.get(
        f"/api/search/{empty_error_id}/export.xlsx",
        headers=headers,
    ).status_code == 409

    partial_id = db.create_job("python developer")
    db.update_job_progress(partial_id, {"page": 1, "found": 1}, [candidate])
    db.fail_job(partial_id, "Later page failed.")
    assert client.get(
        f"/api/search/{partial_id}/export.xlsx",
        headers=headers,
    ).status_code == 200
