"""SQLite persistence for asynchronous search jobs."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator

from .config import resolve_project_path

DB_PATH = resolve_project_path(os.getenv("DB_PATH", "data/sieve.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_jobs (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    location TEXT,
    max_pages INTEGER NOT NULL DEFAULT 10,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'done', 'error')),
    progress_json TEXT,
    progress_page INTEGER NOT NULL DEFAULT 0,
    progress_found INTEGER NOT NULL DEFAULT 0,
    results_json TEXT,
    error_message TEXT,
    error TEXT,
    created_at TIMESTAMP NOT NULL
);
"""

_EXPECTED_COLUMNS = {
    "location": "TEXT",
    "max_pages": "INTEGER NOT NULL DEFAULT 10",
    "progress_json": "TEXT",
    "progress_page": "INTEGER NOT NULL DEFAULT 0",
    "progress_found": "INTEGER NOT NULL DEFAULT 0",
    "results_json": "TEXT",
    "error_message": "TEXT",
    "error": "TEXT",
}


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with _connection() as connection:
        connection.executescript(SCHEMA)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(search_jobs)")
        }
        for column, definition in _EXPECTED_COLUMNS.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE search_jobs ADD COLUMN {column} {definition}"
                )
        connection.execute(
            "UPDATE search_jobs SET status='error', "
            "error_message='Search interrupted by API shutdown.', "
            "error='Search interrupted by API shutdown.' "
            "WHERE status IN ('pending', 'running')"
        )


def _check_updated(cursor: sqlite3.Cursor, job_id: str) -> None:
    if cursor.rowcount == 0:
        raise ValueError(f"Search job {job_id} does not exist.")


def create_job(query: str, location: str | None = None, max_pages: int = 10) -> str:
    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connection() as connection:
        connection.execute(
            "INSERT INTO search_jobs(id, query, location, max_pages, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (job_id, query, location, max_pages, created_at),
        )
    return job_id


def get_active_job() -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, query, status FROM search_jobs "
            "WHERE status IN ('pending', 'running') ORDER BY created_at LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def update_job_progress(
    job_id: str,
    progress: dict[str, int],
    results: list[dict],
) -> None:
    page = max(0, int(progress.get("page", 0)))
    found = max(0, int(progress.get("found", len(results))))
    progress_json = json.dumps({"page": page, "found": found})
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE search_jobs SET status='running', progress_json=?, progress_page=?, "
            "progress_found=?, results_json=? WHERE id=?",
            (
                progress_json,
                page,
                found,
                json.dumps(results, ensure_ascii=False),
                job_id,
            ),
        )
        _check_updated(cursor, job_id)


def finish_job(job_id: str, results: list[dict]) -> None:
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE search_jobs SET status='done', results_json=?, error_message=NULL, "
            "error=NULL WHERE id=?",
            (json.dumps(results, ensure_ascii=False), job_id),
        )
        _check_updated(cursor, job_id)


def fail_job(job_id: str, error: str) -> None:
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE search_jobs SET status='error', error_message=?, error=? WHERE id=?",
            (error, error, job_id),
        )
        _check_updated(cursor, job_id)


def get_job(job_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM search_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None

    job = dict(row)
    results_json = job.pop("results_json")
    job["results"] = json.loads(results_json) if results_json else []
    progress_json = job.get("progress_json")
    if progress_json:
        job["progress"] = json.loads(progress_json)
    else:
        job["progress"] = {
            "page": job.get("progress_page", 0) or 0,
            "found": job.get("progress_found", 0) or 0,
        }
    job["error_message"] = job.get("error_message") or job.get("error")
    return job


def delete_jobs_older_than(days: int) -> int:
    if days < 1:
        raise ValueError("Retention days must be at least 1.")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    with _connection() as connection:
        cursor = connection.execute(
            "DELETE FROM search_jobs WHERE created_at < ?",
            (cutoff,),
        )
        return cursor.rowcount
