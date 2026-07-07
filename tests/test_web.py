from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
import json
import threading
import time

from soundtouch_radio.config import load_station_config
from soundtouch_radio.diagnostics import DiagnosticRecorder
from soundtouch_radio.web import ControlPanelRuntime, create_control_panel_server


class FakeClient:
    def __init__(self, *, now_playing_states: list[dict] | None = None) -> None:
        self.played = []
        self.volume_target = 25
        self.keys = []
        self._now_playing_states = now_playing_states or [
            {
                "source": "UPNP",
                "content_location": "http://example.test/live.mp3",
                "play_status": "PLAY_STATE",
            }
        ]

    def play_station_dlna(self, station):
        self.played.append(station)
        return FakeResponse(status=200)

    def select_station(self, station):
        self.played.append(station)
        return FakeResponse(status=200)

    def now_playing(self):
        if len(self._now_playing_states) == 1:
            return self._now_playing_states[0]
        return self._now_playing_states.pop(0)

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

        recover = _post_json(f"{base_url}/api/recover", {})
        assert recover["reason"] == "playback_plausible"
        assert client.keys == ["MUTE"]
    finally:
        server.shutdown()
        server.server_close()


def test_runtime_recovery_stops_and_replays_implausible_playback(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    device, stations = load_station_config(config)
    client = FakeClient(
        now_playing_states=[
            {
                "source": "AUX",
                "content_location": "/local/aux",
                "play_status": "PLAY_STATE",
            },
            {
                "source": "AUX",
                "content_location": "/local/aux",
                "play_status": "PLAY_STATE",
            },
            {
                "source": "UPNP",
                "content_location": "http://example.test/live.mp3",
                "play_status": "PLAY_STATE",
            },
        ]
    )
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=client,
        settle=0,
    )

    play = runtime.play_slot(1)
    result = runtime.recover_if_implausible()
    diagnostics = runtime.snapshot()["bridge"]["diagnostics"]

    assert play["playback_check"]["reason"] == "source_stayed_aux"
    assert result["recovered"] is True
    assert result["reason"] == "playback_plausible_after_stop_replay"
    assert client.keys == ["STOP"]
    assert [station.slot for station in client.played] == [1, 1]
    assert diagnostics[-1]["kind"] == "recovery"


def test_runtime_recovery_skips_stale_play_attempt(tmp_path: Path) -> None:
    config = _write_config(tmp_path, station_count=2)
    device, stations = load_station_config(config)
    client = FakeClient(
        now_playing_states=[
            {
                "source": "AUX",
                "content_location": "/local/aux",
                "play_status": "PLAY_STATE",
            },
            {
                "source": "UPNP",
                "content_location": "http://example.test/two.mp3",
                "play_status": "PLAY_STATE",
            },
        ]
    )
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=client,
        settle=0,
    )

    first = runtime.play_slot(1)
    second = runtime.play_slot(2)
    client.keys.clear()
    client.played.clear()

    result = runtime.recover_if_implausible(
        station=stations[0],
        play_attempt_id=first["play_attempt_id"],
    )

    assert first["play_attempt_id"] != second["play_attempt_id"]
    assert result["recovered"] is False
    assert result["reason"] == "stale_recovery"
    assert result["actions"] == []
    assert client.keys == []
    assert client.played == []


def test_runtime_followup_skips_stale_play_attempt(tmp_path: Path) -> None:
    config = _write_config(tmp_path, station_count=2)
    device, stations = load_station_config(config)
    client = FakeClient(
        now_playing_states=[
            {
                "source": "UPNP",
                "content_location": "http://example.test/two.mp3",
                "play_status": "PLAY_STATE",
            },
        ]
    )
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=client,
        diagnostic_followup_delay=0.01,
        auto_recover=True,
    )

    runtime.record_bridge_result(
        {
            "triggered": True,
            "trigger_source": "websocket_now_selection",
            "station": stations[0].to_dict(),
            "status": 200,
            "after": {
                "source": "UPNP",
                "content_location": stations[0].location,
                "play_status": "PLAY_STATE",
            },
            "playback_check": {
                "plausible": True,
                "observed_source": "UPNP",
            },
        }
    )
    runtime.record_bridge_result(
        {
            "triggered": True,
            "trigger_source": "websocket_now_selection",
            "station": stations[1].to_dict(),
            "status": 200,
            "after": {
                "source": "UPNP",
                "content_location": stations[1].location,
                "play_status": "PLAY_STATE",
            },
            "playback_check": {
                "plausible": True,
                "observed_source": "UPNP",
            },
        }
    )

    stale_followup = _wait_for_diagnostic(runtime, "followup_check", stale=True)

    assert stale_followup["reason"] == "stale_followup"
    assert client.played == []
    assert client.keys == []
    assert runtime.snapshot()["bridge"]["current_play_attempt_id"] == 2


def test_runtime_recovery_does_not_replay_from_standby(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    device, stations = load_station_config(config)
    client = FakeClient(
        now_playing_states=[
            {
                "source": "STANDBY",
                "content_location": None,
                "play_status": None,
            },
        ]
    )
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=client,
        settle=0,
    )
    runtime.play_slot(1)
    client.played.clear()

    result = runtime.recover_if_implausible()

    assert result["recovered"] is False
    assert result["reason"] == "standby_not_recovered"
    assert result["actions"] == []
    assert client.keys == []
    assert client.played == []


def test_runtime_records_raw_websocket_messages(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    device, stations = load_station_config(config)
    diagnostic_log = tmp_path / "diagnostics" / "soundtouch.jsonl"
    runtime = ControlPanelRuntime(
        config_path=config,
        device=device,
        stations=stations,
        client=FakeClient(),
        diagnostic_recorder=DiagnosticRecorder(diagnostic_log),
    )

    runtime.record_bridge_status(
        {
            "event": "message",
            "connection_id": 2,
            "event_tags": ["nowSelectionUpdated"],
            "raw_message": "<updates><nowSelectionUpdated /></updates>",
        }
    )

    entries = [json.loads(line) for line in diagnostic_log.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["kind"] == "websocket_message"
    assert entries[0]["event_tags"] == ["nowSelectionUpdated"]
    assert entries[0]["raw_message"] == "<updates><nowSelectionUpdated /></updates>"
    assert entries[1]["kind"] == "listener_status"
    assert "raw_message" not in entries[1]["update"]
    assert runtime.snapshot()["app"]["diagnostic_log"]["path"] == str(diagnostic_log)


def _write_config(tmp_path: Path, *, station_count: int = 1) -> Path:
    config = tmp_path / "stations.toml"
    text = """
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
    if station_count >= 2:
        text += """
    [[station]]
    slot = 2
    name = "Station Two"
    location = "http://example.test/two.mp3"
    marker_source = "AUX"
    marker_source_account = "AUX"
    marker_location = "/local/aux/2"
    marker_name = "AUX IN 2"
    """
    config.write_text(text)
    return config


def _wait_for_diagnostic(
    runtime: ControlPanelRuntime,
    kind: str,
    **expected: object,
) -> dict:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        diagnostics = runtime.snapshot()["bridge"]["diagnostics"]
        for entry in diagnostics:
            if entry.get("kind") == kind and all(
                entry.get(key) == value for key, value in expected.items()
            ):
                return entry
        time.sleep(0.01)
    raise AssertionError(f"diagnostic {kind} with {expected} not found")


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
