from __future__ import annotations

from soundtouch_radio.bridge import (
    BridgeState,
    bridge_once,
    bridge_websocket_message,
    playback_check_for_station,
    playback_check_for_target,
    station_for_now_playing,
    station_for_now_selection,
)
from soundtouch_radio.models import Station
from soundtouch_radio.soundtouch import HttpResult


def test_station_for_now_playing_matches_upnp_location() -> None:
    station = Station(slot=1, name="Radio", location="http://example.test/live.mp3")

    result = station_for_now_playing(
        {
            "source": "UPNP",
            "content_location": "http://example.test/live.mp3",
        },
        [station],
    )

    assert result == station


def test_station_for_now_selection_matches_preset_id() -> None:
    station = Station(slot=2, name="Radio", location="http://example.test/live.mp3")

    result = station_for_now_selection({"preset_id": 2}, [station])

    assert result == station


def test_playback_check_accepts_target_stream_buffering() -> None:
    check = playback_check_for_target(
        expected_source="UPNP",
        expected_location="http://example.test/live.mp3",
        now_playing={
            "source": "UPNP",
            "content_location": "http://example.test/live.mp3",
            "play_status": "BUFFERING_STATE",
        },
        status=200,
    )

    assert check["plausible"] is True
    assert check["reason"] == "target_stream_selected"


def test_playback_check_flags_dlna_accepted_but_source_stayed_aux() -> None:
    station = Station(slot=1, name="Radio", location="http://example.test/live.mp3")

    check = playback_check_for_station(
        station,
        {
            "source": "AUX",
            "content_location": "/local/aux",
            "play_status": "PLAY_STATE",
        },
        status=200,
    )

    assert check["plausible"] is False
    assert check["transport_accepted"] is True
    assert check["reason"] == "source_stayed_aux"
    assert check["expected_location"] == "http://example.test/live.mp3"


def test_bridge_once_triggers_select_when_marker_is_not_playing() -> None:
    station = Station(slot=1, name="Radio", location="http://example.test/live.mp3")
    client = FakeClient(
        now_selection={
            "preset_id": 1,
            "content_location": "http://example.test/live.mp3",
        },
        before={
            "source": "INVALID_SOURCE",
            "content_location": None,
            "play_status": None,
        },
        after={
            "source": "UPNP",
            "content_location": "http://example.test/live.mp3",
            "play_status": "PLAY_STATE",
        },
    )

    result = bridge_once(client, [station], BridgeState(), cooldown=4.0, settle=0)

    assert result["triggered"] is True
    assert result["trigger_source"] == "now_selection"
    assert client.played == [station]
    assert client.playback_methods == ["dlna"]
    assert result["after"]["play_status"] == "PLAY_STATE"
    assert result["playback_check"]["plausible"] is True


def test_bridge_once_can_use_select_when_requested() -> None:
    station = Station(slot=1, name="Radio", location="http://example.test/live.mp3")
    client = FakeClient(
        now_selection={
            "preset_id": 1,
            "content_location": "http://example.test/live.mp3",
        },
        before={
            "source": "INVALID_SOURCE",
            "content_location": None,
            "play_status": None,
        },
        after={
            "source": "UPNP",
            "content_location": "http://example.test/live.mp3",
            "play_status": "PLAY_STATE",
        },
    )

    result = bridge_once(
        client,
        [station],
        BridgeState(),
        cooldown=4.0,
        settle=0,
        playback_method="select",
    )

    assert result["triggered"] is True
    assert client.playback_methods == ["select"]


def test_bridge_once_ignores_stale_selection_when_other_source_is_active() -> None:
    station = Station(slot=1, name="Radio", location="http://example.test/live.mp3")
    client = FakeClient(
        now_selection={
            "preset_id": 1,
            "content_location": "http://example.test/live.mp3",
        },
        before={
            "source": "BLUETOOTH",
            "content_location": None,
            "play_status": "PLAY_STATE",
        },
        after={
            "source": "BLUETOOTH",
            "content_location": None,
            "play_status": "PLAY_STATE",
        },
    )

    result = bridge_once(client, [station], BridgeState(), cooldown=4.0, settle=0)

    assert result["triggered"] is False
    assert result["reason"] == "stale_selection"
    assert client.played == []


def test_bridge_websocket_message_triggers_from_physical_button_event() -> None:
    station = Station(slot=2, name="RTL", location="http://example.test/rtl.mp3")
    client = FakeClient(
        now_selection={
            "preset_id": 0,
            "content_location": None,
        },
        before={
            "source": "INVALID_SOURCE",
            "content_location": None,
            "play_status": None,
        },
        after={
            "source": "UPNP",
            "content_location": "http://example.test/rtl.mp3",
            "play_status": "PLAY_STATE",
        },
    )

    result = bridge_websocket_message(
        client,
        [station],
        BridgeState(),
        """
<updates deviceID="abc">
  <nowSelectionUpdated>
    <preset id="2">
      <ContentItem source="UPNP" location="http://example.test/rtl.mp3"
          sourceAccount="UPnPUserName" isPresetable="true" />
    </preset>
  </nowSelectionUpdated>
</updates>
""",
        settle=0,
    )

    assert result is not None
    assert result["triggered"] is True
    assert result["trigger_source"] == "websocket_now_selection"
    assert client.played == [station]


def test_bridge_websocket_message_reasserts_playback_even_if_stream_looks_active() -> None:
    station = Station(slot=2, name="RTL", location="http://example.test/rtl.mp3")
    client = FakeClient(
        now_selection={
            "preset_id": 2,
            "content_location": "http://example.test/rtl.mp3",
        },
        before={
            "source": "UPNP",
            "content_location": "http://example.test/rtl.mp3",
            "play_status": "PLAY_STATE",
        },
        after={
            "source": "UPNP",
            "content_location": "http://example.test/rtl.mp3",
            "play_status": "PLAY_STATE",
        },
    )

    result = bridge_websocket_message(
        client,
        [station],
        BridgeState(),
        """
<updates deviceID="abc">
  <nowSelectionUpdated>
    <preset id="2">
      <ContentItem source="UPNP" location="http://example.test/rtl.mp3"
          sourceAccount="UPnPUserName" isPresetable="true" />
    </preset>
  </nowSelectionUpdated>
</updates>
""",
        settle=0,
    )

    assert result is not None
    assert result["triggered"] is True
    assert client.played == [station]


class FakeClient:
    def __init__(self, *, now_selection: dict, before: dict, after: dict) -> None:
        self._now_selection = now_selection
        self._states = [before, after]
        self.played: list[Station] = []
        self.playback_methods: list[str] = []

    def now_selection(self) -> dict:
        return self._now_selection

    def now_playing(self) -> dict:
        if len(self._states) == 1:
            return self._states[0]
        return self._states.pop(0)

    def play_station_dlna(self, station: Station) -> HttpResult:
        self.played.append(station)
        self.playback_methods.append("dlna")
        return HttpResult(status=200, headers={}, body="")

    def select_station(self, station: Station) -> HttpResult:
        self.played.append(station)
        self.playback_methods.append("select")
        return HttpResult(status=200, headers={}, body="")
