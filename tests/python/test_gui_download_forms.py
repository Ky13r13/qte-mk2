import pytest

from qte.gui.research_forms import (
    FormError, compile_download_configuration, form_identity, form_options, prepare_form,
)


def request():
    return {"symbols": ["SPY", "QQQ"], "timeframe": "1Hour",
            "start": "2024-01-02T14:00:00Z", "end": "2024-01-02T17:00:00Z",
            "feed": "iex", "action_free": True}


def test_download_form_preview_is_pure_and_credential_free(monkeypatch):
    monkeypatch.setattr("qte.adapters.alpaca_download.fetch_page",
                        lambda _: pytest.fail("preview attempted provider access"))
    preview = prepare_form("download_alpaca", request(), None)
    assert preview["effective_configuration"] == request()
    assert preview["kind"] == "download_alpaca"
    assert preview["preview_fingerprint"] == form_identity("download_alpaca", request(), None)["preview_fingerprint"]
    assert "credential" not in str(preview).lower()
    assert form_options()["download_alpaca"]["fields"]["feed"]["choices"] == ["iex", "sip"]


def test_download_form_errors_are_sanitized():
    for invalid in ({**request(), "url": "https://example.invalid"},
                    {**request(), "symbols": []}, {**request(), "feed": "secret-value"}):
        with pytest.raises(FormError) as caught:
            compile_download_configuration(invalid)
        assert caught.value.field == "configuration"
        assert "secret-value" not in caught.value.message
    with pytest.raises(FormError):
        prepare_form("download_alpaca", request(), b"not accepted")
