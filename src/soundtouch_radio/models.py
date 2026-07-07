from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import time
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class DeviceConfig:
    host: str
    api_port: int = 8090
    dlna_port: int = 8091
    name: str | None = None


@dataclass(frozen=True)
class Station:
    slot: int
    name: str
    location: str
    source: str = "UPNP"
    type: str | None = None
    source_account: str | None = None
    marker_source: str | None = None
    marker_location: str | None = None
    marker_source_account: str | None = None
    marker_type: str | None = None
    marker_name: str | None = None
    container_art: str | None = None
    homepage: str | None = None
    codec: str | None = None
    notes: str | None = None

    def validate(self) -> None:
        if self.slot < 1 or self.slot > 6:
            raise ValueError(f"station slot must be 1..6, got {self.slot}")
        if not self.name.strip():
            raise ValueError(f"station {self.slot} is missing a name")
        if not self.location.strip():
            raise ValueError(f"station {self.slot} is missing a location URL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "name": self.name,
            "source": self.source,
            "type": self.type,
            "location": self.location,
            "source_account": self.source_account,
            "marker_source": self.marker_source,
            "marker_location": self.marker_location,
            "marker_source_account": self.marker_source_account,
            "marker_type": self.marker_type,
            "marker_name": self.marker_name,
            "container_art": self.container_art,
            "homepage": self.homepage,
            "codec": self.codec,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Preset:
    slot: int
    name: str | None
    source: str | None
    type: str | None
    location: str | None
    source_account: str | None = None
    container_art: str | None = None
    raw_xml: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "name": self.name,
            "source": self.source,
            "type": self.type,
            "location": self.location,
            "source_account": self.source_account,
            "container_art": self.container_art,
            "raw_xml": self.raw_xml,
        }


def station_to_content_item_xml(station: Station) -> str:
    return _content_item_xml(
        source=station.source,
        location=station.location,
        item_name=station.name,
        source_account=station.source_account,
        type_=station.type,
        container_art=station.container_art,
    )


def station_to_marker_content_item_xml(station: Station) -> str:
    return _content_item_xml(
        source=station.marker_source or station.source,
        location=station.marker_location or station.location,
        item_name=station.marker_name or station.name,
        source_account=(
            station.marker_source_account
            if station.marker_source_account is not None
            else station.source_account
        ),
        type_=station.marker_type if station.marker_type is not None else station.type,
        container_art=station.container_art,
    )


def _content_item_xml(
    *,
    source: str,
    location: str,
    item_name: str,
    source_account: str | None,
    type_: str | None,
    container_art: str | None,
) -> str:
    item = ET.Element(
        "ContentItem",
        {
            "source": source,
            "location": location,
            "isPresetable": "true",
        },
    )
    if source_account:
        item.set("sourceAccount", source_account)
    if type_:
        item.set("type", type_)
    name = ET.SubElement(item, "itemName")
    name.text = item_name
    if container_art:
        art = ET.SubElement(item, "containerArt")
        art.text = container_art
    return ET.tostring(item, encoding="unicode")


def station_to_preset_xml(station: Station, *, now: int | None = None) -> str:
    station.validate()
    timestamp = int(time.time()) if now is None else now
    preset = ET.Element(
        "preset",
        {
            "id": str(station.slot),
            "createdOn": str(timestamp),
            "updatedOn": str(timestamp),
        },
    )
    preset.append(ET.fromstring(station_to_marker_content_item_xml(station)))
    return ET.tostring(preset, encoding="unicode")


def parse_presets_xml(xml_text: str) -> list[Preset]:
    root = ET.fromstring(xml_text)
    presets: list[Preset] = []
    for preset_node in root.findall(".//preset"):
        content = preset_node.find("ContentItem")
        slot_text = preset_node.get("id")
        if not slot_text:
            continue
        slot = int(slot_text)
        if content is None:
            presets.append(
                Preset(
                    slot=slot,
                    name=None,
                    source=None,
                    type=None,
                    location=None,
                    raw_xml=ET.tostring(preset_node, encoding="unicode"),
                )
            )
            continue
        presets.append(
            Preset(
                slot=slot,
                name=_child_text(content, "itemName"),
                source=content.get("source"),
                type=content.get("type"),
                location=content.get("location"),
                source_account=content.get("sourceAccount"),
                container_art=_child_text(content, "containerArt"),
                raw_xml=ET.tostring(preset_node, encoding="unicode"),
            )
        )
    return sorted(presets, key=lambda item: item.slot)


def parse_info_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    return {
        "device_id": root.get("deviceID"),
        "name": _child_text(root, "name"),
        "type": _child_text(root, "type"),
        "marge_account_uuid": _child_text(root, "margeAccountUUID"),
        "module_type": _child_text(root, "moduleType"),
        "variant": _child_text(root, "variant"),
        "variant_mode": _child_text(root, "variantMode"),
        "country_code": _child_text(root, "countryCode"),
        "region_code": _child_text(root, "regionCode"),
    }


def parse_now_playing_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    content = root.find("ContentItem")
    return {
        "source": root.get("source"),
        "source_account": root.get("sourceAccount"),
        "content_location": content.get("location") if content is not None else None,
        "content_source": content.get("source") if content is not None else None,
        "content_type": content.get("type") if content is not None else None,
        "station_name": _child_text(root, "stationName"),
        "track": _child_text(root, "track"),
        "artist": _child_text(root, "artist"),
        "album": _child_text(root, "album"),
        "play_status": _child_text(root, "playStatus"),
        "description": _child_text(root, "description"),
    }


def parse_volume_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    return {
        "target": _child_int(root, "targetvolume"),
        "actual": _child_int(root, "actualvolume"),
        "muted": _child_bool(root, "muteenabled"),
    }


def parse_now_selection_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    return _selection_node_to_dict(root)


def parse_now_selection_update_xml(xml_text: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    preset = root.find(".//nowSelectionUpdated/preset")
    if preset is None:
        return None
    return _selection_node_to_dict(preset)


def is_recovery_signal_xml(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    return _has_tag(root, "userActivityUpdate") or _has_tag(root, "errorUpdate")


def event_tags_from_xml(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    if _local_name(root.tag) == "updates":
        return [_local_name(child.tag) for child in list(root)]
    return [_local_name(root.tag)]


def _has_tag(root: ET.Element, tag: str) -> bool:
    return root.tag == tag or root.find(f".//{tag}") is not None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _selection_node_to_dict(node: ET.Element) -> dict[str, Any]:
    content = node.find("ContentItem")
    preset_id = node.get("id")
    return {
        "preset_id": int(preset_id) if preset_id and preset_id.isdigit() else None,
        "content_location": content.get("location") if content is not None else None,
        "content_source": content.get("source") if content is not None else None,
        "content_type": content.get("type") if content is not None else None,
        "source_account": content.get("sourceAccount") if content is not None else None,
        "item_name": _child_text(content, "itemName") if content is not None else None,
    }


def _child_text(node: ET.Element, tag: str) -> str | None:
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _child_int(node: ET.Element, tag: str) -> int | None:
    text = _child_text(node, tag)
    if text is None:
        return None
    return int(text)


def _child_bool(node: ET.Element, tag: str) -> bool | None:
    text = _child_text(node, tag)
    if text is None:
        return None
    return text.lower() == "true"
