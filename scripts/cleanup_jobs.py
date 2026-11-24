"""Delete local search jobs older than a requested retention period."""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--older-than-days",
        type=int,
        required=True,
        help="Delete jobs created more than this many days ago (minimum 1).",
    )
    arguments = parser.parse_args()
    if arguments.older_than_days < 1:
        parser.error("--older-than-days must be at least 1.")

    deleted = db.delete_jobs_older_than(arguments.older_than_days)
    print(f"Deleted {deleted} old search job(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
