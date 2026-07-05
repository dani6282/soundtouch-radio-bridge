from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape
import json
import time
import xml.etree.ElementTree as ET

from . import __version__
from .models import (
    Preset,
    Station,
    parse_info_xml,
    parse_now_selection_xml,
    parse_now_playing_xml,
    parse_presets_xml,
    parse_volume_xml,
    station_to_content_item_xml,
    station_to_preset_xml,
)

USER_AGENT = f"soundtouch-radio/{__version__}"


class SoundTouchError(RuntimeError):
    """Raised for SoundTouch API failures."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "headers": self.headers, "body": self.body}


class SoundTouchClient:
    def __init__(
        self, host: str, port: int = 8090, dlna_port: int = 8091, timeout: float = 8.0
    ) -> None:
        self.host = host
        self.port = port
        self.dlna_port = dlna_port
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def info(self) -> dict[str, Any]:
        return parse_info_xml(self.request("GET", "info").body)

    def now_playing(self) -> dict[str, Any]:
        return parse_now_playing_xml(self.request("GET", "now_playing").body)

    def now_selection(self) -> dict[str, Any]:
        return parse_now_selection_xml(self.request("GET", "nowSelection").body)

    def volume(self) -> dict[str, Any]:
        return parse_volume_xml(self.request("GET", "volume").body)

    def set_volume(self, level: int) -> HttpResult:
        if level < 0 or level > 100:
            raise ValueError(f"volume must be 0..100, got {level}")
        return self.request("POST", "volume", f"<volume>{level}</volume>")

    def send_key(self, key: str) -> dict[str, Any]:
        key = key.strip().upper()
        if key not in {
            "MUTE",
            "PLAY_PAUSE",
            "STOP",
            "VOLUME_UP",
            "VOLUME_DOWN",
            "POWER",
        }:
            raise ValueError(f"unsupported key {key}")
        press = self.request("POST", "key", _key_body(key, state="press"))
        release = self.request("POST", "key", _key_body(key, state="release"))
        return {"key": key, "press_status": press.status, "release_status": release.status}

    def presets_xml(self) -> str:
        return self.request("GET", "presets").body

    def presets(self) -> list[Preset]:
        return parse_presets_xml(self.presets_xml())

    def backup_presets(self, out_dir: Path) -> dict[str, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        xml_path = out_dir / f"soundtouch-presets-{self.host}-{timestamp}.xml"
        json_path = out_dir / f"soundtouch-presets-{self.host}-{timestamp}.json"
        xml_text = self.presets_xml()
        xml_path.write_text(xml_text, encoding="utf-8")
        presets = [preset.to_dict() for preset in parse_presets_xml(xml_text)]
        json_path.write_text(
            json.dumps({"host": self.host, "presets": presets}, indent=2), encoding="utf-8"
        )
        return {"xml": str(xml_path), "json": str(json_path)}

    def select_station(self, station: Station) -> HttpResult:
        return self.request("POST", "select", station_to_content_item_xml(station))

    def play_station_dlna(self, station: Station) -> HttpResult:
        return self.play_url_dlna(station.location)

    def play_url_dlna(self, url: str) -> HttpResult:
        if not url.startswith("http://"):
            raise SoundTouchError("DLNA playback requires a plain http:// stream URL")
        body = dlna_set_av_transport_uri_body(url)
        request = Request(
            f"http://{self.host}:{self.dlna_port}/AVTransport/Control",
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Content-Type": 'text/xml; charset="utf-8"',
                "HOST": f"{self.host}:{self.dlna_port}",
                "SOAPACTION": "urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return HttpResult(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read().decode("utf-8", errors="replace"),
                )
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SoundTouchError(f"DLNA playback returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise SoundTouchError(f"failed to connect to SoundTouch DLNA API: {exc}") from exc

    def store_preset(self, station: Station) -> HttpResult:
        return self.request("POST", "storePreset", station_to_preset_xml(station))

    def request(self, method: str, path: str, body: str | None = None) -> HttpResult:
        url = path if path.startswith(("http://", "https://")) else urljoin(self.base_url, path)
        data = body.encode("utf-8") if body is not None else None
        request = Request(
            url,
            data=data,
            method=method.upper(),
            headers={
                "Content-Type": "text/xml",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                result = HttpResult(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response_body,
                )
        except HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            result = HttpResult(
                status=exc.code,
                headers=dict(exc.headers.items()),
                body=response_body,
            )
        except URLError as exc:
            raise SoundTouchError(f"failed to connect to SoundTouch API at {url}: {exc}") from exc

        self._raise_for_soundtouch_error(result)
        return result

    @staticmethod
    def _raise_for_soundtouch_error(result: HttpResult) -> None:
        if 200 <= result.status < 300:
            if result.body.strip():
                _raise_xml_errors(result.body)
            return
        if result.body.strip():
            _raise_xml_errors(result.body)
        raise SoundTouchError(f"SoundTouch API returned HTTP {result.status}")


def validate_stream_url(url: str, timeout: float = 8.0) -> dict[str, Any]:
    request = Request(
        url,
        method="GET",
        headers={"Range": "bytes=0-1023", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            chunk = response.read(1024)
            return {
                "url": url,
                "ok": True,
                "status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "bytes_read": len(chunk),
                "final_url": response.url,
            }
    except HTTPError as exc:
        return {
            "url": url,
            "ok": False,
            "status": exc.code,
            "content_type": exc.headers.get("Content-Type"),
            "error": exc.reason,
        }
    except URLError as exc:
        return {"url": url, "ok": False, "error": str(exc.reason)}


def _raise_xml_errors(xml_text: str) -> None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return
    error_nodes = []
    if root.tag == "errors":
        error_nodes.extend(root.findall("error"))
    error_nodes.extend(root.findall(".//error"))
    if not error_nodes:
        return
    details = []
    for error in error_nodes:
        name = error.get("name") or "UNKNOWN_ERROR"
        value = error.get("value") or (error.text or "").strip()
        details.append(f"{name}({value})")
    raise SoundTouchError("SoundTouch API error: " + ", ".join(details))


def dlna_set_av_transport_uri_body(url: str) -> str:
    escaped_url = escape(url)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">',
            "  <s:Body>",
            '    <u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">',
            "      <InstanceID>0</InstanceID>",
            "      <CurrentURIMetaData></CurrentURIMetaData>",
            f"      <CurrentURI>{escaped_url}</CurrentURI>",
            "    </u:SetAVTransportURI>",
            "  </s:Body>",
            "</s:Envelope>",
        ]
    )


def _key_body(key: str, *, state: str) -> str:
    return f'<key state="{state}" sender="Gabbo">{escape(key)}</key>'
