from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
import json
import threading

from soundtouch_radio.config import load_station_config
from soundtouch_radio.web import ControlPanelRuntime, create_control_panel_server


class FakeClient:
    def __init__(self) -> None:
        self.played = []
        self.volume_target = 25
        self.keys = []

    def play_station_dlna(self, station):
        self.played.append(station)
        return FakeResponse(status=200)

    def select_station(self, station):
        self.played.append(station)
        return FakeResponse(status=200)

    def now_playing(self):
        return {"source": "UPNP", "content_location": "http://example.test/live.mp3"}

    def now_selection(self):
        return {"preset_id": 1}

    def info(self):
        return {"name": "Kitchen", "type": "SoundTouch 20"}

    def volume(self):
        return {"target": self.volume_target, "actual": self.volume_target, "muted": False}

    def set_volume(self, target):
        self.volume_target = target
        return FakeResponse(status=200)

    def send_key(self, key):
        key = key.upper()
        self.keys.append(key)
        return {"key": key, "press_status": 200, "release_status": 200}


class FakeResponse:
    def __init__(self, *, status: int) -> None:
        self.status = status


def test_runtime_updates_station_config_without_losing_marker(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    device, stations = load_station_config(config)
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=FakeClient(),
    )

    result = runtime.update_station(
        1,
        {"name": "Updated", "location": "http://example.test/updated.mp3"},
    )
    _, reloaded = load_station_config(config)

    assert result["station"]["name"] == "Updated"
    assert reloaded[0].location == "http://example.test/updated.mp3"
    assert reloaded[0].marker_source == "AUX"
    assert reloaded[0].marker_location == "/local/aux"


def test_control_panel_api_serves_status_and_actions(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    device, stations = load_station_config(config)
    client = FakeClient()
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=client,
    )
    server = create_control_panel_server("127.0.0.1", 0, runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        status = _get_json(f"{base_url}/api/status")
        assert status["stations"][0]["name"] == "Station One"
        assert status["bridge"]["connected"] is False

        play = _post_json(f"{base_url}/api/play", {"slot": 1})
        assert play["played"]["slot"] == 1
        assert client.played[0].slot == 1

        volume = _post_json(f"{base_url}/api/volume", {"target": 31})
        assert volume["volume"]["actual"] == 31

        key = _post_json(f"{base_url}/api/key", {"key": "mute"})
        assert key["key"] == "MUTE"
        assert client.keys == ["MUTE"]
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "stations.toml"
    config.write_text(
        """
[device]
host = "192.0.2.1"
name = "Kitchen"

[[station]]
slot = 1
name = "Station One"
location = "http://example.test/live.mp3"
marker_source = "AUX"
marker_source_account = "AUX"
marker_location = "/local/aux"
marker_name = "AUX IN 1"
"""
    )
    return config


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
