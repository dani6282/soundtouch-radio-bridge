from __future__ import annotations

import pytest

from soundtouch_radio.soundtouch import HttpResult, SoundTouchClient, SoundTouchError


def test_raise_for_soundtouch_error_reports_all_xml_errors() -> None:
    result = HttpResult(
        status=400,
        headers={},
        body="""
<errors>
  <error name="INVALID_SOURCE" value="1047" />
  <error name="INVALID_PARAMETER_VALUE" value="1048" />
</errors>
""",
    )

    with pytest.raises(SoundTouchError) as excinfo:
        SoundTouchClient._raise_for_soundtouch_error(result)

    assert "INVALID_SOURCE(1047)" in str(excinfo.value)
    assert "INVALID_PARAMETER_VALUE(1048)" in str(excinfo.value)


def test_set_volume_uses_soundtouch_volume_body(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    client = SoundTouchClient("192.0.2.1")

    def fake_request(method: str, path: str, body: str | None = None) -> HttpResult:
        requests.append((method, path, body))
        return HttpResult(status=200, headers={}, body="<status>/volume</status>")

    monkeypatch.setattr(client, "request", fake_request)

    response = client.set_volume(32)

    assert response.status == 200
    assert requests == [("POST", "volume", "<volume>32</volume>")]


def test_send_key_sends_press_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = []
    client = SoundTouchClient("192.0.2.1")

    def fake_request(method: str, path: str, body: str | None = None) -> HttpResult:
        requests.append((method, path, body))
        return HttpResult(status=200, headers={}, body="<status>/key</status>")

    monkeypatch.setattr(client, "request", fake_request)

    result = client.send_key("play_pause")

    assert result == {"key": "PLAY_PAUSE", "press_status": 200, "release_status": 200}
    assert requests == [
        ("POST", "key", '<key state="press" sender="Gabbo">PLAY_PAUSE</key>'),
        ("POST", "key", '<key state="release" sender="Gabbo">PLAY_PAUSE</key>'),
    ]
