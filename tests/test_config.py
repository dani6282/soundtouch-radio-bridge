from __future__ import annotations

from pathlib import Path

from dataclasses import replace

from soundtouch_radio.config import (
    load_station_config,
    parse_slots,
    save_station_config,
    station_by_slot,
)


def test_load_station_config(tmp_path: Path) -> None:
    config = tmp_path / "stations.toml"
    config.write_text(
        """
[device]
host = "192.0.2.1"

[[station]]
slot = 1
name = "Station One"
location = "http://example.test/stream.mp3"
marker_source = "AUX"
marker_source_account = "AUX"
marker_location = "/local/aux"
marker_name = "AUX IN"
"""
    )

    device, stations = load_station_config(config)

    assert device is not None
    assert device.host == "192.0.2.1"
    assert device.dlna_port == 8091
    assert stations[0].slot == 1
    assert stations[0].source == "UPNP"
    assert stations[0].source_account == "UPnPUserName"
    assert stations[0].marker_source == "AUX"
    assert stations[0].marker_source_account == "AUX"
    assert stations[0].marker_location == "/local/aux"
    assert stations[0].marker_name == "AUX IN"
    assert stations[0].type is None


def test_station_by_slot() -> None:
    _, stations = load_station_config(Path("stations.example.toml"))

    assert station_by_slot(stations, 1).name == "Example Radio One"


def test_parse_slots_defaults_to_configured() -> None:
    _, stations = load_station_config(Path("stations.example.toml"))

    assert parse_slots(None, stations) == [1, 2]
    assert parse_slots("2,1,2", stations) == [1, 2]


def test_save_station_config_round_trips_editable_fields(tmp_path: Path) -> None:
    source = tmp_path / "stations.toml"
    source.write_text(
        """
[device]
host = "192.0.2.1"
name = "Kitchen"

[[station]]
slot = 1
name = "Station One"
location = "http://example.test/one.mp3"
marker_source = "AUX"
marker_source_account = "AUX"
marker_location = "/local/aux"
marker_name = "AUX IN 1"
"""
    )
    device, stations = load_station_config(source)
    updated = [replace(stations[0], name="Station Two", location="http://example.test/two.mp3")]

    save_station_config(source, device, updated)
    reloaded_device, reloaded_stations = load_station_config(source)

    assert reloaded_device == device
    assert reloaded_stations[0].name == "Station Two"
    assert reloaded_stations[0].location == "http://example.test/two.mp3"
    assert reloaded_stations[0].marker_location == "/local/aux"
