from app import db


def test_startup_marks_interrupted_jobs_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sieve.db")
    db.init_db()
    pending_id = db.create_job("synthetic keyword")
    running_id = db.create_job("synthetic keyword")
    db.update_job_progress(running_id, {"page": 1, "found": 0}, [])

    db.init_db()

    assert db.get_job(pending_id)["status"] == "error"
    assert db.get_job(running_id)["status"] == "error"
    assert db.get_job(running_id)["error_message"] == (
        "Search interrupted by API shutdown."
    )


def test_retention_deletes_only_jobs_older_than_requested_days(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sieve.db")
    db.init_db()
    old_id = db.create_job("synthetic keyword")
    recent_id = db.create_job("synthetic keyword")
    with db._connection() as connection:
        connection.execute(
            "UPDATE search_jobs SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", old_id),
        )

    assert db.delete_jobs_older_than(30) == 1
    assert db.get_job(old_id) is None
    assert db.get_job(recent_id) is not None
