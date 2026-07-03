from __future__ import annotations

from soundtouch_radio.models import (
    Station,
    is_recovery_signal_xml,
    parse_info_xml,
    parse_now_selection_xml,
    parse_now_selection_update_xml,
    parse_presets_xml,
    parse_volume_xml,
    station_to_content_item_xml,
    station_to_marker_content_item_xml,
    station_to_preset_xml,
)
from soundtouch_radio.soundtouch import dlna_set_av_transport_uri_body


def test_station_to_content_item_xml() -> None:
    station = Station(
        slot=1,
        name="Test Radio",
        location="http://example.test/live.mp3",
        source_account="UPnPUserName",
    )

    xml = station_to_content_item_xml(station)

    assert 'source="UPNP"' in xml
    assert 'sourceAccount="UPnPUserName"' in xml
    assert "type=" not in xml
    assert 'location="http://example.test/live.mp3"' in xml
    assert 'isPresetable="true"' in xml
    assert "<itemName>Test Radio</itemName>" in xml


def test_station_to_preset_xml() -> None:
    station = Station(slot=2, name="Test Radio", location="http://example.test/live.mp3")

    xml = station_to_preset_xml(station, now=123)

    assert '<preset id="2" createdOn="123" updatedOn="123">' in xml
    assert "<ContentItem" in xml


def test_station_to_preset_xml_uses_marker_when_configured() -> None:
    station = Station(
        slot=1,
        name="Radio",
        location="http://example.test/live.mp3",
        source_account="UPnPUserName",
        marker_source="AUX",
        marker_source_account="AUX",
        marker_location="/local/aux",
        marker_name="AUX IN",
    )

    marker_xml = station_to_marker_content_item_xml(station)
    preset_xml = station_to_preset_xml(station, now=123)

    assert 'source="AUX"' in marker_xml
    assert 'sourceAccount="AUX"' in marker_xml
    assert 'location="/local/aux"' in marker_xml
    assert "<itemName>AUX IN</itemName>" in marker_xml
    assert 'source="AUX"' in preset_xml
    assert "http://example.test/live.mp3" not in preset_xml


def test_dlna_set_av_transport_uri_body_escapes_url() -> None:
    body = dlna_set_av_transport_uri_body("http://example.test/live.mp3?a=1&b=2")

    assert "SetAVTransportURI" in body
    assert "<CurrentURI>http://example.test/live.mp3?a=1&amp;b=2</CurrentURI>" in body


def test_parse_presets_xml() -> None:
    presets = parse_presets_xml(
        """
<presets>
  <preset id="1">
      <ContentItem source="UPNP" sourceAccount="UPnPUserName"
      location="http://example.test/live.mp3" isPresetable="true">
      <itemName>Test Radio</itemName>
      <containerArt>https://example.test/logo.png</containerArt>
    </ContentItem>
  </preset>
</presets>
"""
    )

    assert len(presets) == 1
    assert presets[0].slot == 1
    assert presets[0].name == "Test Radio"
    assert presets[0].source == "UPNP"
    assert presets[0].source_account == "UPnPUserName"
    assert presets[0].container_art == "https://example.test/logo.png"


def test_parse_info_xml() -> None:
    info = parse_info_xml(
        """
<info deviceID="abc">
  <name>Kitchen</name>
  <type>SoundTouch 20</type>
</info>
"""
    )

    assert info["device_id"] == "abc"
    assert info["name"] == "Kitchen"
    assert info["type"] == "SoundTouch 20"


def test_parse_now_selection_xml() -> None:
    selection = parse_now_selection_xml(
        """
<nowSelection id="2">
  <ContentItem source="UPNP" sourceAccount="UPnPUserName"
      location="http://example.test/live.mp3" isPresetable="true">
    <itemName>Test Radio</itemName>
  </ContentItem>
</nowSelection>
"""
    )

    assert selection == {
        "preset_id": 2,
        "content_location": "http://example.test/live.mp3",
        "content_source": "UPNP",
        "content_type": None,
        "source_account": "UPnPUserName",
        "item_name": "Test Radio",
    }


def test_parse_volume_xml() -> None:
    volume = parse_volume_xml(
        """
<volume deviceID="abc">
  <targetvolume>25</targetvolume>
  <actualvolume>24</actualvolume>
  <muteenabled>false</muteenabled>
</volume>
"""
    )

    assert volume == {"target": 25, "actual": 24, "muted": False}


def test_parse_now_selection_update_xml() -> None:
    selection = parse_now_selection_update_xml(
        """
<updates deviceID="abc">
  <nowSelectionUpdated>
    <preset id="2">
      <ContentItem source="UPNP" location="http://example.test/live.mp3"
          sourceAccount="UPnPUserName" isPresetable="true" />
    </preset>
  </nowSelectionUpdated>
</updates>
"""
    )

    assert selection == {
        "preset_id": 2,
        "content_location": "http://example.test/live.mp3",
        "content_source": "UPNP",
        "content_type": None,
        "source_account": "UPnPUserName",
        "item_name": None,
    }


def test_parse_now_selection_update_xml_ignores_other_messages() -> None:
    selection = parse_now_selection_update_xml(
        '<errorUpdate><error name="UNABLE_TO_PROCESS_NOT_LOGGED_IN" /></errorUpdate>'
    )

    assert selection is None


def test_is_recovery_signal_xml_matches_activity_and_error_updates() -> None:
    assert is_recovery_signal_xml('<userActivityUpdate deviceID="abc" />') is True
    assert (
        is_recovery_signal_xml(
            """
<updates>
  <errorUpdate deviceID="abc">
    <error name="UNABLE_TO_PROCESS_NOT_LOGGED_IN" />
  </errorUpdate>
</updates>
"""
        )
        is True
    )


def test_is_recovery_signal_xml_ignores_selection_updates() -> None:
    assert (
        is_recovery_signal_xml(
            """
<updates>
  <nowSelectionUpdated>
    <preset id="1" />
  </nowSelectionUpdated>
</updates>
"""
        )
        is False
    )
