"""Small standard-library loader for the project's local .env file."""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_API_KEY_PLACEHOLDERS = {
    "",
    "replace-with-a-private-random-secret",
    "your_super_secret_key_here",
    "changeme",
}


def load_project_env(project_root: Path) -> None:
    env_file = project_root / ".env"
    if not env_file.is_file():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or not _KEY_PATTERN.fullmatch(key):
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


def require_api_key() -> str:
    api_key = os.getenv("API_KEY", "").strip()
    if api_key.casefold() in _API_KEY_PLACEHOLDERS:
        raise RuntimeError(
            "API_KEY is missing or still a placeholder. Set a private value "
            "in the project .env file before starting the API."
        )
    return api_key


def get_local_cdp_url() -> str:
    cdp_url = os.getenv("NAUKRI_CDP_URL", "http://127.0.0.1:9222").strip()
    try:
        parsed = urlparse(cdp_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(
            "NAUKRI_CDP_URL must be a valid localhost HTTP URL."
        ) from exc

    if (
        parsed.scheme != "http"
        or hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError(
            "NAUKRI_CDP_URL must use HTTP and point to localhost only."
        )
    if port is None or port == 0:
        raise RuntimeError("NAUKRI_CDP_URL must include a valid localhost HTTP port.")
    return cdp_url


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


load_project_env(PROJECT_ROOT)
