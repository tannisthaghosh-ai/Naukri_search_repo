import pytest

from app.config import get_local_cdp_url, require_api_key


def test_api_key_must_be_present_and_not_a_placeholder(monkeypatch):
    for value in (None, "", "replace-with-a-private-random-secret"):
        if value is None:
            monkeypatch.delenv("API_KEY", raising=False)
        else:
            monkeypatch.setenv("API_KEY", value)
        with pytest.raises(RuntimeError, match="API_KEY is missing or still a placeholder"):
            require_api_key()

    monkeypatch.setenv("API_KEY", "a-private-test-key")
    assert require_api_key() == "a-private-test-key"


def test_cdp_url_must_be_localhost(monkeypatch):
    monkeypatch.setenv("NAUKRI_CDP_URL", "http://remote.example:9222")
    with pytest.raises(RuntimeError, match="localhost only"):
        get_local_cdp_url()

    monkeypatch.setenv("NAUKRI_CDP_URL", "http://127.0.0.1:9222")
    assert get_local_cdp_url() == "http://127.0.0.1:9222"

    monkeypatch.setenv("NAUKRI_CDP_URL", "http://127.0.0.1")
    with pytest.raises(RuntimeError, match="valid localhost HTTP port"):
        get_local_cdp_url()
