from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import json
import mimetypes
import os
import threading
import time

from .bridge import (
    DEFAULT_PLAYBACK_METHOD,
    BridgeState,
    PlaybackMethod,
    playback_check_for_station,
    playback_check_for_target,
    run_websocket_bridge,
)
from .config import save_station_config, station_by_slot
from .models import DeviceConfig, Station
from .soundtouch import SoundTouchClient


class ControlPanelRuntime:
    def __init__(
        self,
        *,
        config_path: Path,
        device: DeviceConfig | None,
        stations: list[Station],
        client: SoundTouchClient,
        playback_method: PlaybackMethod = DEFAULT_PLAYBACK_METHOD,
        settle: float = 1.0,
        diagnostic_followup_delay: float = 5.0,
        auto_recover: bool = False,
    ) -> None:
        self.config_path = config_path
        self.device = device
        self.stations = stations
        self.client = client
        self.playback_method = playback_method
        self.settle = settle
        self.diagnostic_followup_delay = diagnostic_followup_delay
        self.auto_recover = auto_recover
        self.bridge_state = BridgeState()
        self._lock = threading.RLock()
        self._recovery_lock = threading.Lock()
        self._recovery_running = False
        self._started_at = _now_iso()
        self._last_health_check: dict[str, Any] | None = None
        self._last_play: dict[str, Any] | None = None
        self._last_volume: dict[str, Any] | None = None
        self._last_now_playing: dict[str, Any] | None = None
        self._diagnostics: deque[dict[str, Any]] = deque(maxlen=30)
        self._bridge: dict[str, Any] = {
            "running": False,
            "connected": False,
            "started_at": None,
            "url": None,
            "connection_attempts": 0,
            "reconnects": 0,
            "last_connected_at": None,
            "last_disconnected_at": None,
            "last_event_at": None,
            "last_update_at": None,
            "last_error": None,
            "last_result": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            bridge = dict(self._bridge)
            bridge["diagnostics"] = list(self._diagnostics)
            return {
                "app": {
                    "started_at": self._started_at,
                    "config_path": str(self.config_path),
                    "config_writable": os.access(self.config_path, os.W_OK),
                    "playback_method": self.playback_method,
                    "auto_recover": self.auto_recover,
                    "diagnostic_followup_delay": self.diagnostic_followup_delay,
                },
                "device": _device_to_dict(self.device),
                "stations": [station.to_dict() for station in self.stations],
                "bridge": bridge,
                "last_health_check": self._last_health_check,
                "last_play": self._last_play,
                "volume": self._last_volume,
                "now_playing": self._last_now_playing,
            }

    def update_station(self, slot: int, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = station_by_slot(self.stations, slot)
            name = str(updates.get("name", current.name)).strip()
            location = str(updates.get("location", current.location)).strip()
            homepage = _optional_text(updates.get("homepage", current.homepage))
            if not location.startswith("http://"):
                raise ValueError("SoundTouch DLNA playback requires a plain http:// stream URL")
            updated = replace(current, name=name, location=location, homepage=homepage)
            updated.validate()
            next_stations = [
                updated if station.slot == updated.slot else station for station in self.stations
            ]
            save_station_config(self.config_path, self.device, next_stations)
            self.stations[:] = sorted(next_stations, key=lambda item: item.slot)
            return {"station": updated.to_dict(), "config_path": str(self.config_path)}

    def play_slot(self, slot: int) -> dict[str, Any]:
        with self._lock:
            station = station_by_slot(self.stations, slot)
        response = self._play_station(station)
        if self.settle > 0:
            time.sleep(self.settle)
        now_playing = self.client.now_playing()
        playback_check = playback_check_for_station(
            station,
            now_playing,
            status=response.status,
        )
        result = {
            "played": station.to_dict(),
            "method": self.playback_method,
            "status": response.status,
            "now_playing": now_playing,
            "playback_check": playback_check,
            "played_at": _now_iso(),
        }
        with self._lock:
            self._last_play = result
            self._last_now_playing = now_playing
            self._append_diagnostic_locked(
                {
                    "kind": "manual_play",
                    "station": _station_summary(station.to_dict()),
                    "status": response.status,
                    "now_playing": _now_playing_summary(now_playing),
                    "playback_check": playback_check,
                }
            )
        return result

    def health_check(self) -> dict[str, Any]:
        started = time.monotonic()
        data: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, check in {
            "info": self.client.info,
            "now_playing": self.client.now_playing,
            "now_selection": self.client.now_selection,
            "volume": self.client.volume,
        }.items():
            try:
                data[name] = check()
            except Exception as exc:
                errors[name] = str(exc)
        result = {
            "ok": not errors,
            "checked_at": _now_iso(),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "data": data,
            "errors": errors,
        }
        with self._lock:
            expected_station = self._expected_station_locked()
            if expected_station is not None and "now_playing" in data:
                result["playback_check"] = playback_check_for_station(
                    expected_station,
                    data["now_playing"],
                )
            self._last_health_check = result
            if "now_playing" in data:
                self._last_now_playing = data["now_playing"]
            if "volume" in data:
                self._last_volume = data["volume"]
            self._append_diagnostic_locked(
                {
                    "kind": "health_check",
                    "ok": result["ok"],
                    "playback_check": result.get("playback_check"),
                    "errors": errors,
                }
            )
        return result

    def get_volume(self) -> dict[str, Any]:
        volume = self.client.volume()
        with self._lock:
            self._last_volume = volume
        return {"volume": volume}

    def set_volume(self, target: int) -> dict[str, Any]:
        response = self.client.set_volume(target)
        volume = self.client.volume()
        result = {"status": response.status, "volume": volume}
        with self._lock:
            self._last_volume = volume
        return result

    def send_key(self, key: str) -> dict[str, Any]:
        result = self.client.send_key(key)
        result["sent_at"] = _now_iso()
        return result

    def recover_if_implausible(self, *, allow_power_toggle: bool = False) -> dict[str, Any]:
        if not self._recovery_lock.acquire(blocking=False):
            return {
                "ok": False,
                "recovered": False,
                "reason": "recovery_already_running",
                "checked_at": _now_iso(),
            }
        self._recovery_running = True
        try:
            with self._lock:
                station = self._expected_station_locked()
            if station is None:
                result = {
                    "ok": False,
                    "recovered": False,
                    "reason": "no_expected_station",
                    "checked_at": _now_iso(),
                    "actions": [],
                }
                self._record_recovery_result(result)
                return result

            health = self.health_check()
            now_playing = health.get("data", {}).get("now_playing")
            initial_check = playback_check_for_station(station, now_playing)
            actions: list[dict[str, Any]] = []
            if initial_check["plausible"]:
                result = {
                    "ok": True,
                    "recovered": False,
                    "reason": "playback_plausible",
                    "checked_at": _now_iso(),
                    "station": station.to_dict(),
                    "initial_check": initial_check,
                    "actions": actions,
                    "health_check": health,
                }
                self._record_recovery_result(result)
                return result

            stop = self.send_key("STOP")
            actions.append({"action": "key", "key": "STOP", "result": stop})
            time.sleep(1.0)

            replay = self._play_station(station)
            actions.append(
                {
                    "action": "replay_station",
                    "method": self.playback_method,
                    "status": replay.status,
                }
            )
            if self.settle > 0:
                time.sleep(self.settle)
            after_replay = self.client.now_playing()
            replay_check = playback_check_for_station(station, after_replay, status=replay.status)
            with self._lock:
                self._last_now_playing = after_replay

            if replay_check["plausible"] or not allow_power_toggle:
                result = {
                    "ok": replay_check["plausible"],
                    "recovered": replay_check["plausible"],
                    "reason": (
                        "playback_plausible_after_stop_replay"
                        if replay_check["plausible"]
                        else "power_toggle_not_allowed"
                    ),
                    "checked_at": _now_iso(),
                    "station": station.to_dict(),
                    "initial_check": initial_check,
                    "after_replay": after_replay,
                    "replay_check": replay_check,
                    "actions": actions,
                    "health_check": health,
                }
                self._record_recovery_result(result)
                return result

            power_sequence = (
                ["POWER"]
                if initial_check["observed_source"] == "STANDBY"
                else [
                    "POWER",
                    "POWER",
                ]
            )
            for index, key in enumerate(power_sequence):
                power = self.send_key(key)
                actions.append(
                    {
                        "action": "key",
                        "key": key,
                        "sequence_index": index + 1,
                        "result": power,
                    }
                )
                time.sleep(3.0)

            power_replay = self._play_station(station)
            actions.append(
                {
                    "action": "replay_station_after_power",
                    "method": self.playback_method,
                    "status": power_replay.status,
                }
            )
            if self.settle > 0:
                time.sleep(self.settle)
            after_power = self.client.now_playing()
            power_check = playback_check_for_station(
                station,
                after_power,
                status=power_replay.status,
            )
            result = {
                "ok": power_check["plausible"],
                "recovered": power_check["plausible"],
                "reason": (
                    "playback_plausible_after_power_replay"
                    if power_check["plausible"]
                    else power_check["reason"]
                ),
                "checked_at": _now_iso(),
                "station": station.to_dict(),
                "initial_check": initial_check,
                "after_replay": after_replay,
                "replay_check": replay_check,
                "after_power": after_power,
                "power_check": power_check,
                "actions": actions,
                "health_check": health,
            }
            with self._lock:
                self._last_now_playing = after_power
            self._record_recovery_result(result)
            return result
        finally:
            self._recovery_running = False
            self._recovery_lock.release()

    def start_bridge(
        self,
        *,
        websocket_port: int,
        recovery_window: float,
        recovery_poll_interval: float,
        cooldown: float,
        reconnect_interval: float,
    ) -> threading.Thread:
        with self._lock:
            self._bridge["running"] = True
            self._bridge["started_at"] = _now_iso()

        def run() -> None:
            try:
                run_websocket_bridge(
                    self.client,
                    self.stations,
                    self.bridge_state,
                    websocket_port=websocket_port,
                    recovery_window=recovery_window,
                    recovery_poll_interval=recovery_poll_interval,
                    cooldown=cooldown,
                    settle=self.settle,
                    playback_method=self.playback_method,
                    reconnect_interval=reconnect_interval,
                    on_result=self.record_bridge_result,
                    on_status=self.record_bridge_status,
                )
            except Exception as exc:
                with self._lock:
                    self._bridge["running"] = False
                    self._bridge["connected"] = False
                    self._bridge["last_error"] = str(exc)
                    self._bridge["last_update_at"] = _now_iso()

        thread = threading.Thread(target=run, name="soundtouch-radio-bridge", daemon=True)
        thread.start()
        return thread

    def record_bridge_result(self, result: dict[str, Any]) -> None:
        schedule_followup = bool(
            result.get("triggered") and result.get("station") and self.diagnostic_followup_delay > 0
        )
        schedule_recovery = bool(
            result.get("triggered")
            and self.auto_recover
            and result.get("playback_check")
            and not result["playback_check"].get("plausible")
        )
        with self._lock:
            self._bridge["last_result"] = result
            self._bridge["last_event_at"] = _now_iso()
            self._bridge["last_update_at"] = _now_iso()
            if result.get("after"):
                self._last_now_playing = result["after"]
            self._append_diagnostic_locked(_bridge_result_diagnostic(result))
        if schedule_followup:
            self._schedule_followup_check(result)
        if schedule_recovery:
            self._schedule_recovery()

    def record_bridge_status(self, update: dict[str, Any]) -> None:
        event = update.get("event")
        now = _now_iso()
        with self._lock:
            self._bridge["last_update_at"] = now
            if update.get("url"):
                self._bridge["url"] = update["url"]
            if event == "connecting":
                self._bridge["connection_attempts"] += 1
            elif event == "connected":
                if self._bridge["last_connected_at"]:
                    self._bridge["reconnects"] += 1
                self._bridge["connected"] = True
                self._bridge["last_connected_at"] = now
                self._bridge["last_error"] = None
            elif event == "disconnected":
                self._bridge["connected"] = False
                self._bridge["last_disconnected_at"] = now
                self._bridge["last_close"] = {
                    "code": update.get("code"),
                    "message": update.get("message"),
                }
            elif event == "message":
                self._bridge["last_event_at"] = now
            elif event == "error":
                self._bridge["last_error"] = update.get("error")
            self._append_diagnostic_locked(
                {
                    "kind": "listener_status",
                    "event": event,
                    "url": update.get("url"),
                    "code": update.get("code"),
                    "message": update.get("message"),
                    "error": update.get("error"),
                }
            )

    def _play_station(self, station: Station) -> Any:
        if self.playback_method == "dlna":
            return self.client.play_station_dlna(station)
        return self.client.select_station(station)

    def _expected_station_locked(self) -> Station | None:
        last_result = self._bridge.get("last_result") or {}
        station_data = last_result.get("station") if last_result.get("triggered") else None
        if station_data is None and self._last_play is not None:
            station_data = self._last_play.get("played")
        if not isinstance(station_data, dict):
            return None
        slot = station_data.get("slot")
        if slot is None:
            return None
        try:
            return station_by_slot(self.stations, int(slot))
        except (TypeError, ValueError):
            return None

    def _append_diagnostic_locked(self, entry: dict[str, Any]) -> None:
        self._diagnostics.append({"at": _now_iso(), **entry})

    def _schedule_followup_check(self, result: dict[str, Any]) -> None:
        station_data = result.get("station")
        if not isinstance(station_data, dict):
            return
        expected_location = station_data.get("location")
        expected_source = station_data.get("source") or "UPNP"
        if not expected_location:
            return

        def run() -> None:
            time.sleep(self.diagnostic_followup_delay)
            try:
                now_playing = self.client.now_playing()
                playback_check = playback_check_for_target(
                    expected_source=str(expected_source),
                    expected_location=str(expected_location),
                    now_playing=now_playing,
                )
                with self._lock:
                    self._last_now_playing = now_playing
                    self._bridge["last_followup_check"] = {
                        "checked_at": _now_iso(),
                        "delay_seconds": self.diagnostic_followup_delay,
                        "station": _station_summary(station_data),
                        "now_playing": _now_playing_summary(now_playing),
                        "playback_check": playback_check,
                    }
                    self._bridge["last_update_at"] = _now_iso()
                    self._append_diagnostic_locked(
                        {
                            "kind": "followup_check",
                            "delay_seconds": self.diagnostic_followup_delay,
                            "station": _station_summary(station_data),
                            "now_playing": _now_playing_summary(now_playing),
                            "playback_check": playback_check,
                        }
                    )
                if self.auto_recover and not playback_check["plausible"]:
                    self._schedule_recovery()
            except Exception as exc:
                with self._lock:
                    self._bridge["last_error"] = str(exc)
                    self._bridge["last_update_at"] = _now_iso()
                    self._append_diagnostic_locked({"kind": "followup_check", "error": str(exc)})

        threading.Thread(target=run, name="soundtouch-radio-followup", daemon=True).start()

    def _schedule_recovery(self) -> None:
        if self._recovery_running:
            return

        def run() -> None:
            self.recover_if_implausible(allow_power_toggle=False)

        threading.Thread(target=run, name="soundtouch-radio-recovery", daemon=True).start()

    def _record_recovery_result(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._bridge["last_recovery"] = result
            self._bridge["last_update_at"] = _now_iso()
            self._append_diagnostic_locked(
                {
                    "kind": "recovery",
                    "ok": result.get("ok"),
                    "recovered": result.get("recovered"),
                    "reason": result.get("reason"),
                    "station": _station_summary(result.get("station")),
                    "initial_check": result.get("initial_check"),
                    "replay_check": result.get("replay_check"),
                    "power_check": result.get("power_check"),
                }
            )


def run_control_panel(
    *,
    host: str,
    port: int,
    runtime: ControlPanelRuntime,
    image_path: Path | None,
    start_bridge: bool,
    websocket_port: int,
    recovery_window: float,
    recovery_poll_interval: float,
    cooldown: float,
    reconnect_interval: float,
) -> None:
    if start_bridge:
        runtime.start_bridge(
            websocket_port=websocket_port,
            recovery_window=recovery_window,
            recovery_poll_interval=recovery_poll_interval,
            cooldown=cooldown,
            reconnect_interval=reconnect_interval,
        )
    server = create_control_panel_server(host, port, runtime, image_path=image_path)
    print(f"soundtouch-radio web UI listening on http://{host}:{server.server_port}")
    server.serve_forever()


def create_control_panel_server(
    host: str,
    port: int,
    runtime: ControlPanelRuntime,
    *,
    image_path: Path | None = None,
) -> ThreadingHTTPServer:
    handler = _handler_factory(runtime, image_path)
    return ThreadingHTTPServer((host, port), handler)


def _handler_factory(
    runtime: ControlPanelRuntime, image_path: Path | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "soundtouch-radio"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/":
                    self._send_text(INDEX_HTML, content_type="text/html; charset=utf-8")
                elif path == "/app.css":
                    self._send_text(APP_CSS, content_type="text/css; charset=utf-8")
                elif path == "/app.js":
                    self._send_text(APP_JS, content_type="application/javascript; charset=utf-8")
                elif path == "/device-image":
                    self._send_image(image_path)
                elif path == "/api/status":
                    self._send_json(runtime.snapshot())
                elif path == "/api/volume":
                    self._send_json(runtime.get_volume())
                else:
                    self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/play":
                    self._send_json(runtime.play_slot(int(body["slot"])))
                elif path.startswith("/api/stations/"):
                    slot = int(path.rsplit("/", 1)[1])
                    self._send_json(runtime.update_station(slot, body))
                elif path == "/api/health-check":
                    self._send_json(runtime.health_check())
                elif path == "/api/recover":
                    self._send_json(
                        runtime.recover_if_implausible(
                            allow_power_toggle=bool(body.get("allow_power_toggle", False))
                        )
                    )
                elif path == "/api/volume":
                    self._send_json(runtime.set_volume(int(body["target"])))
                elif path == "/api/key":
                    self._send_json(runtime.send_key(str(body["key"])))
                else:
                    self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            except KeyError as exc:
                self._send_json(
                    {"ok": False, "error": f"missing field {exc.args[0]}"},
                    HTTPStatus.BAD_REQUEST,
                )
            except (TypeError, ValueError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            data = self.rfile.read(length)
            parsed = json.loads(data.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("JSON body must be an object")
            return parsed

        def _send_json(
            self,
            payload: dict[str, Any],
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, *, content_type: str) -> None:
            data = text.encode("utf-8")
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_image(self, path: Path | None) -> None:
            if path is None or not path.exists() or not path.is_file():
                self._send_json(
                    {"ok": False, "error": "image not configured"}, HTTPStatus.NOT_FOUND
                )
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _device_to_dict(device: DeviceConfig | None) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "host": device.host,
        "api_port": device.api_port,
        "dlna_port": device.dlna_port,
        "name": device.name,
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bridge_result_diagnostic(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "bridge_result",
        "triggered": result.get("triggered"),
        "reason": result.get("reason"),
        "trigger_source": result.get("trigger_source"),
        "station": _station_summary(result.get("station")),
        "status": result.get("status"),
        "before": _now_playing_summary(result.get("before") or result.get("now_playing")),
        "after": _now_playing_summary(result.get("after")),
        "playback_check": result.get("playback_check"),
    }


def _station_summary(station: Any) -> dict[str, Any] | None:
    if not isinstance(station, dict):
        return None
    return {
        "slot": station.get("slot"),
        "name": station.get("name"),
        "source": station.get("source"),
        "location": station.get("location"),
    }


def _now_playing_summary(now_playing: Any) -> dict[str, Any] | None:
    if not isinstance(now_playing, dict):
        return None
    return {
        "source": now_playing.get("source"),
        "content_source": now_playing.get("content_source"),
        "content_location": now_playing.get("content_location"),
        "station_name": now_playing.get("station_name"),
        "play_status": now_playing.get("play_status"),
    }


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SoundTouch Radio</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div class="photo-frame">
        <img id="deviceImage" src="/device-image" alt="SoundTouch speaker">
        <div id="deviceFallback" class="device-fallback" aria-hidden="true">
          <div class="speaker-body">
            <div class="speaker-display"></div>
            <div class="speaker-grille"></div>
            <div class="speaker-foot"></div>
          </div>
        </div>
      </div>
      <div class="headline">
        <p class="eyebrow">SoundTouch Radio</p>
        <h1 id="deviceName">SoundTouch</h1>
        <div class="status-row">
          <span id="listenerStatus" class="status-pill">Listener</span>
          <span id="lastEvent" class="status-pill muted">No event yet</span>
          <span id="configStatus" class="status-pill muted">Config</span>
        </div>
      </div>
    </header>

    <section class="control-strip" aria-label="Playback controls">
      <div class="volume-panel">
        <div class="panel-title">Volume <span id="volumeValue">--</span></div>
        <input id="volumeSlider" type="range" min="0" max="100" value="25">
      </div>
      <div class="transport-panel">
        <button data-key="VOLUME_DOWN" class="icon-button" type="button">-</button>
        <button data-key="VOLUME_UP" class="icon-button" type="button">+</button>
        <button data-key="MUTE" type="button">Mute</button>
        <button data-key="PLAY_PAUSE" type="button">Play/Pause</button>
        <button data-key="STOP" type="button">Stop</button>
      </div>
      <button id="healthButton" class="secondary" type="button">Check Now</button>
      <button id="recoverButton" class="secondary" type="button">Recover</button>
    </section>

    <section id="stations" class="stations" aria-label="Preset stations"></section>

    <section class="status-grid" aria-label="Status">
      <article>
        <h2>Now Playing</h2>
        <pre id="nowPlaying">No data</pre>
      </article>
      <article>
        <h2>Listener</h2>
        <pre id="bridgeDetails">No data</pre>
      </article>
      <article>
        <h2>Health</h2>
        <pre id="healthDetails">No check yet</pre>
      </article>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --ink: #17202a;
  --muted: #657083;
  --line: #d8dee8;
  --paper: #f6f7f9;
  --panel: #ffffff;
  --accent: #0f766e;
  --accent-strong: #0b5f59;
  --warm: #cc5a2a;
  --shadow: 0 18px 55px rgba(25, 35, 45, 0.16);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  background: linear-gradient(180deg, #edf1f5 0%, #f8f5ef 100%);
  color: var(--ink);
  font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button,
input {
  font: inherit;
}

button {
  border: 0;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  min-height: 44px;
  padding: 0 16px;
  font-weight: 700;
}

button:hover {
  background: var(--accent-strong);
}

button.secondary {
  background: #253244;
}

button.icon-button {
  aspect-ratio: 1;
  width: 44px;
  padding: 0;
  font-size: 22px;
}

input[type="text"],
input[type="url"] {
  width: 100%;
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fff;
  color: var(--ink);
}

pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: #293241;
  font-size: 13px;
}

.shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 24px 0 44px;
}

.hero {
  display: grid;
  grid-template-columns: minmax(280px, 460px) 1fr;
  gap: 28px;
  align-items: center;
  min-height: 280px;
}

.photo-frame {
  position: relative;
  overflow: hidden;
  min-height: 260px;
  border-radius: 8px;
  background: #15181d;
  box-shadow: var(--shadow);
}

.photo-frame img {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 260px;
  object-fit: cover;
}

.device-fallback {
  display: none;
  place-items: center;
  min-height: 260px;
  background:
    radial-gradient(circle at 25% 20%, rgba(255, 255, 255, 0.18), transparent 34%),
    linear-gradient(145deg, #11161c, #2d333b);
}

.speaker-body {
  position: relative;
  width: min(78%, 360px);
  aspect-ratio: 1.72;
  border-radius: 28px 28px 20px 20px;
  background: linear-gradient(180deg, #242a31, #0f1318);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 22px 48px rgba(0, 0, 0, 0.36);
}

.speaker-display {
  position: absolute;
  top: 18%;
  left: 26%;
  right: 26%;
  height: 18%;
  border-radius: 5px;
  background: linear-gradient(180deg, #92d4c9, #2f726d);
}

.speaker-grille {
  position: absolute;
  left: 13%;
  right: 13%;
  bottom: 19%;
  height: 24%;
  border-radius: 10px;
  background-image: radial-gradient(circle, rgba(255, 255, 255, 0.28) 1px, transparent 1px);
  background-size: 10px 10px;
  opacity: 0.55;
}

.speaker-foot {
  position: absolute;
  left: 18%;
  right: 18%;
  bottom: -7%;
  height: 12%;
  border-radius: 0 0 16px 16px;
  background: #0c0f13;
}

.headline h1 {
  margin: 0;
  font-size: clamp(36px, 6vw, 74px);
  line-height: 0.98;
  letter-spacing: 0;
}

.eyebrow {
  margin: 0 0 10px;
  color: var(--warm);
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0;
}

.status-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 22px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  border-radius: 999px;
  padding: 4px 12px;
  background: rgba(15, 118, 110, 0.12);
  color: var(--accent-strong);
  font-weight: 800;
}

.status-pill.offline,
.status-pill.error {
  background: rgba(204, 90, 42, 0.13);
  color: #9c3f1b;
}

.status-pill.muted {
  background: rgba(101, 112, 131, 0.13);
  color: var(--muted);
}

.control-strip {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto auto auto;
  gap: 14px;
  align-items: center;
  margin: 28px 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
}

.panel-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--muted);
  font-weight: 800;
}

.volume-panel input {
  width: 100%;
}

.transport-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.stations {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.station-card,
.status-grid article {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 10px 28px rgba(25, 35, 45, 0.07);
}

.station-card {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.station-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.slot {
  color: var(--warm);
  font-size: 28px;
  font-weight: 900;
}

.station-name {
  margin: 0;
  font-size: 18px;
}

.field-label {
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.station-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 28px;
}

.status-grid article {
  min-height: 180px;
  padding: 14px;
}

.status-grid h2 {
  margin: 0 0 10px;
  font-size: 16px;
}

@media (max-width: 900px) {
  .hero,
  .control-strip,
  .stations,
  .status-grid {
    grid-template-columns: 1fr;
  }

  .transport-panel {
    justify-content: stretch;
  }

  .transport-panel button {
    flex: 1 1 auto;
  }
}
"""


APP_JS = """
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function text(id, value) {
  document.getElementById(id).textContent = value;
}

function compactTime(value) {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderStatus(data) {
  const deviceName = data.device?.name || data.last_health_check?.data?.info?.name || "SoundTouch";
  text("deviceName", deviceName);

  const listener = document.getElementById("listenerStatus");
  listener.textContent = data.bridge.connected ? "Listener connected" : "Listener offline";
  listener.className = `status-pill ${data.bridge.connected ? "" : "offline"}`;

  text("lastEvent", `Last event ${compactTime(data.bridge.last_event_at)}`);
  text("configStatus", data.app.config_writable ? "Config writable" : "Config read-only");

  if (data.volume) {
    const actual = data.volume.actual ?? data.volume.target ?? "--";
    document.getElementById("volumeSlider").value = actual;
    text("volumeValue", actual);
  }

  if (!document.activeElement?.closest(".station-card")) {
    renderStations(data.stations);
  }

  text("nowPlaying", JSON.stringify(data.now_playing || data.last_play?.now_playing || {}, null, 2));
  text("bridgeDetails", JSON.stringify(data.bridge, null, 2));
  text("healthDetails", JSON.stringify(data.last_health_check || {}, null, 2));
}

function renderStations(stations) {
  const container = document.getElementById("stations");
  container.innerHTML = stations.map((station) => `
    <article class="station-card" data-slot="${station.slot}">
      <div class="station-head">
        <div>
          <div class="slot">${station.slot}</div>
          <h2 class="station-name">${escapeHtml(station.name)}</h2>
        </div>
      </div>
      <label class="field-label">
        Name
        <input name="name" type="text" value="${escapeAttr(station.name)}">
      </label>
      <label class="field-label">
        Stream URL
        <input name="location" type="url" value="${escapeAttr(station.location)}">
      </label>
      <label class="field-label">
        Homepage
        <input name="homepage" type="url" value="${escapeAttr(station.homepage || "")}">
      </label>
      <div class="station-actions">
        <button class="play" type="button">Play</button>
        <button class="save secondary" type="button">Save</button>
      </div>
    </article>
  `).join("");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

async function refresh() {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    text("bridgeDetails", error.message);
  }
}

document.getElementById("stations").addEventListener("click", async (event) => {
  const card = event.target.closest(".station-card");
  if (!card) return;
  const slot = Number(card.dataset.slot);
  try {
    if (event.target.classList.contains("play")) {
      await api("/api/play", {
        method: "POST",
        body: JSON.stringify({ slot }),
      });
    }
    if (event.target.classList.contains("save")) {
      await api(`/api/stations/${slot}`, {
        method: "POST",
        body: JSON.stringify({
          name: card.querySelector('[name="name"]').value,
          location: card.querySelector('[name="location"]').value,
          homepage: card.querySelector('[name="homepage"]').value,
        }),
      });
    }
    await refresh();
  } catch (error) {
    text("healthDetails", error.message);
  }
});

document.getElementById("volumeSlider").addEventListener("input", (event) => {
  text("volumeValue", event.target.value);
});

document.getElementById("volumeSlider").addEventListener("change", async (event) => {
  try {
    await api("/api/volume", {
      method: "POST",
      body: JSON.stringify({ target: Number(event.target.value) }),
    });
    await refresh();
  } catch (error) {
    text("healthDetails", error.message);
  }
});

document.querySelector(".transport-panel").addEventListener("click", async (event) => {
  const key = event.target.dataset.key;
  if (!key) return;
  try {
    await api("/api/key", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
    await refresh();
  } catch (error) {
    text("healthDetails", error.message);
  }
});

document.getElementById("healthButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/health-check", { method: "POST" });
    text("healthDetails", JSON.stringify(result, null, 2));
    await refresh();
  } catch (error) {
    text("healthDetails", error.message);
  }
});

document.getElementById("recoverButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/recover", { method: "POST" });
    text("healthDetails", JSON.stringify(result, null, 2));
    await refresh();
  } catch (error) {
    text("healthDetails", error.message);
  }
});

document.getElementById("deviceImage").addEventListener("error", () => {
  document.getElementById("deviceImage").style.display = "none";
  document.getElementById("deviceFallback").style.display = "grid";
});

refresh();
setInterval(refresh, 2500);
"""
