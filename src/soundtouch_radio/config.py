from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import tomllib

from .models import DeviceConfig, Station


DEFAULT_CONFIG_PATH = Path("stations.toml")


def load_station_config(path: Path) -> tuple[DeviceConfig | None, list[Station]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    device = _parse_device(data.get("device"))
    stations = [_parse_station(item) for item in data.get("station", [])]
    seen: set[int] = set()
    for station in stations:
        station.validate()
        if station.slot in seen:
            raise ValueError(f"duplicate station slot {station.slot}")
        seen.add(station.slot)
    return device, sorted(stations, key=lambda item: item.slot)


def save_station_config(path: Path, device: DeviceConfig | None, stations: list[Station]) -> None:
    _validate_stations(stations)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(station_config_to_toml(device, stations))
    tmp_path.replace(path)


def station_config_to_toml(device: DeviceConfig | None, stations: list[Station]) -> str:
    _validate_stations(stations)
    lines: list[str] = []
    if device is not None:
        lines.extend(
            [
                "[device]",
                f"host = {_toml_string(device.host)}",
                f"api_port = {device.api_port}",
                f"dlna_port = {device.dlna_port}",
            ]
        )
        if device.name is not None:
            lines.append(f"name = {_toml_string(device.name)}")
        lines.append("")

    for station in sorted(stations, key=lambda item: item.slot):
        lines.append("[[station]]")
        lines.append(f"slot = {station.slot}")
        _append_station_value(lines, "name", station.name)
        _append_station_value(lines, "source", station.source)
        _append_station_value(lines, "source_account", station.source_account)
        _append_station_value(lines, "location", station.location)
        _append_station_value(lines, "type", station.type)
        _append_station_value(lines, "marker_source", station.marker_source)
        _append_station_value(lines, "marker_source_account", station.marker_source_account)
        _append_station_value(lines, "marker_location", station.marker_location)
        _append_station_value(lines, "marker_name", station.marker_name)
        _append_station_value(lines, "marker_type", station.marker_type)
        _append_station_value(lines, "container_art", station.container_art)
        _append_station_value(lines, "homepage", station.homepage)
        _append_station_value(lines, "codec", station.codec)
        _append_station_value(lines, "notes", station.notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def station_by_slot(stations: list[Station], slot: int) -> Station:
    for station in stations:
        if station.slot == slot:
            return station
    raise ValueError(f"no station configured for slot {slot}")


def parse_slots(slots: str | None, configured: list[Station]) -> list[int]:
    if not slots:
        return [station.slot for station in configured]
    parsed = sorted({int(part.strip()) for part in slots.split(",") if part.strip()})
    for slot in parsed:
        if slot < 1 or slot > 6:
            raise ValueError(f"slot must be 1..6, got {slot}")
    return parsed


def _validate_stations(stations: list[Station]) -> None:
    seen: set[int] = set()
    for station in stations:
        station.validate()
        if station.slot in seen:
            raise ValueError(f"duplicate station slot {station.slot}")
        seen.add(station.slot)


def _append_station_value(lines: list[str], key: str, value: str | None) -> None:
    if value is None:
        return
    lines.append(f"{key} = {_toml_string(value)}")


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _parse_device(data: Any) -> DeviceConfig | None:
    if not data:
        return None
    return DeviceConfig(
        host=str(data["host"]),
        api_port=int(data.get("api_port", 8090)),
        dlna_port=int(data.get("dlna_port", 8091)),
        name=data.get("name"),
    )


def _parse_station(data: dict[str, Any]) -> Station:
    source = str(data.get("source", "UPNP"))
    marker_source = data.get("marker_source")
    return Station(
        slot=int(data["slot"]),
        name=str(data["name"]),
        source=source,
        type=str(data["type"]) if data.get("type") is not None else None,
        location=str(data["location"]),
        source_account=data.get("source_account", _default_source_account(source)),
        marker_source=str(marker_source) if marker_source is not None else None,
        marker_location=(
            str(data["marker_location"]) if data.get("marker_location") is not None else None
        ),
        marker_source_account=data.get(
            "marker_source_account",
            _default_source_account(str(marker_source)) if marker_source is not None else None,
        ),
        marker_type=str(data["marker_type"]) if data.get("marker_type") is not None else None,
        marker_name=str(data["marker_name"]) if data.get("marker_name") is not None else None,
        container_art=data.get("container_art"),
        homepage=data.get("homepage"),
        codec=data.get("codec"),
        notes=data.get("notes"),
    )


def _default_source_account(source: str) -> str | None:
    if source == "UPNP":
        return "UPnPUserName"
    if source == "AUX":
        return "AUX"
    return None
