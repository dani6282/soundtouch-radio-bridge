from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import os
import shutil
import sys
import time

from .bridge import DEFAULT_PLAYBACK_METHOD, BridgeState, bridge_once, run_websocket_bridge
from .config import DEFAULT_CONFIG_PATH, load_station_config, parse_slots, station_by_slot
from .models import (
    DeviceConfig,
    Station,
    station_to_content_item_xml,
    station_to_marker_content_item_xml,
    station_to_preset_xml,
)
from .output import emit, fail
from .soundtouch import (
    SoundTouchClient,
    SoundTouchError,
    dlna_set_av_transport_uri_body,
    validate_stream_url,
)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, SoundTouchError) as exc:
        fail(str(exc), as_json=args.json)
    if result is not None:
        emit(result, as_json=args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soundtouch-radio",
        description="Program Bose SoundTouch preset buttons with local web radio streams.",
    )
    parser.add_argument("--json", action="store_true", help="emit stable JSON")
    parser.add_argument("--stations", type=Path, default=DEFAULT_CONFIG_PATH, help="station TOML")
    parser.add_argument("--host", help="SoundTouch host/IP; overrides station config and env")
    parser.add_argument("--port", type=int, help="SoundTouch API port; default 8090")
    parser.add_argument("--dlna-port", type=int, help="SoundTouch DLNA port; default 8091")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="check local config and optional live reachability"
    )
    doctor.add_argument("--live", action="store_true", help="also query the Bose over the LAN")

    device = subparsers.add_parser("device", help="read SoundTouch device data")
    device_sub = device.add_subparsers(dest="device_command", required=True)
    device_sub.add_parser("info", help="show /info")
    device_sub.add_parser("now-playing", help="show /now_playing")
    device_sub.add_parser("now-selection", help="show /nowSelection")

    stations = subparsers.add_parser("stations", help="work with station config")
    stations_sub = stations.add_subparsers(dest="stations_command", required=True)
    stations_sub.add_parser("list", help="list configured stations")
    stations_sub.add_parser("validate", help="check station stream URLs")

    presets = subparsers.add_parser("presets", help="read or write Bose presets")
    presets_sub = presets.add_subparsers(dest="presets_command", required=True)
    presets_sub.add_parser("list", help="list current Bose presets")
    backup = presets_sub.add_parser("backup", help="save current Bose presets as XML and JSON")
    backup.add_argument("--out", type=Path, default=Path("backups"), help="backup output directory")
    program = presets_sub.add_parser("program", help="store configured stations into Bose presets")
    program.add_argument("--slots", help="comma-separated slots to program; default all configured")
    program.add_argument(
        "--out", type=Path, default=Path("backups"), help="backup output directory"
    )
    program.add_argument("--dry-run", action="store_true", help="show preset XML without writing")
    program.add_argument("--yes", action="store_true", help="confirm live preset write")

    play = subparsers.add_parser("play", help="play a configured station now")
    play.add_argument("slot", type=int, help="station slot")
    play.add_argument(
        "--method",
        choices=["dlna", "select"],
        default=DEFAULT_PLAYBACK_METHOD,
        help="playback method; dlna uses the Bose UPnP renderer",
    )
    play.add_argument(
        "--settle",
        type=float,
        default=10.0,
        help="seconds to wait before reading now-playing after DLNA playback",
    )
    play.add_argument("--dry-run", action="store_true", help="show request body without playing")
    play.add_argument("--yes", action="store_true", help="confirm live playback change")

    serve = subparsers.add_parser("serve", help="run the web control panel and bridge")
    serve.add_argument("--bind", default="127.0.0.1", help="web UI bind address")
    serve.add_argument("--web-port", type=int, default=8788, help="web UI port")
    serve.add_argument("--device-image", type=Path, help="optional speaker photo served by the UI")
    serve.add_argument("--no-bridge", action="store_true", help="serve UI without websocket bridge")
    serve.add_argument("--websocket-port", type=int, default=8080, help="SoundTouch websocket port")
    serve.add_argument(
        "--playback-method",
        choices=["select", "dlna"],
        default=DEFAULT_PLAYBACK_METHOD,
        help="method used after a preset marker is detected",
    )
    serve.add_argument(
        "--recovery-window",
        type=float,
        default=0.0,
        help="seconds to burst-poll after Bose activity/error websocket events",
    )
    serve.add_argument(
        "--recovery-poll-interval",
        type=float,
        default=0.1,
        help="seconds between polls during a recovery burst",
    )
    serve.add_argument(
        "--reconnect-interval",
        type=float,
        default=2.0,
        help="seconds to wait before reconnecting websocket",
    )
    serve.add_argument(
        "--cooldown", type=float, default=4.0, help="minimum seconds between retriggers"
    )
    serve.add_argument(
        "--settle",
        type=float,
        default=1.0,
        help="seconds to wait after playback trigger before reading status",
    )

    bridge = subparsers.add_parser("bridge", help="bridge preset markers to web radio playback")
    bridge_sub = bridge.add_subparsers(dest="bridge_command", required=True)
    bridge_run = bridge_sub.add_parser(
        "run", help="watch preset markers and trigger local web radio playback"
    )
    bridge_run.add_argument(
        "--mode",
        choices=["websocket", "poll"],
        default="websocket",
        help="trigger mode; websocket reacts fastest to physical buttons",
    )
    bridge_run.add_argument("--once", action="store_true", help="run one poll cycle and exit")
    bridge_run.add_argument(
        "--poll-interval", type=float, default=0.75, help="seconds between polls"
    )
    bridge_run.add_argument(
        "--recovery-window",
        type=float,
        default=0.0,
        help="seconds to burst-poll after Bose activity/error websocket events",
    )
    bridge_run.add_argument(
        "--recovery-poll-interval",
        type=float,
        default=0.1,
        help="seconds between polls during a recovery burst",
    )
    bridge_run.add_argument(
        "--websocket-port", type=int, default=8080, help="SoundTouch websocket port"
    )
    bridge_run.add_argument(
        "--playback-method",
        choices=["select", "dlna"],
        default=DEFAULT_PLAYBACK_METHOD,
        help="method used after a preset marker is detected",
    )
    bridge_run.add_argument(
        "--reconnect-interval",
        type=float,
        default=2.0,
        help="seconds to wait before reconnecting websocket",
    )
    bridge_run.add_argument(
        "--cooldown", type=float, default=4.0, help="minimum seconds between retriggers"
    )
    bridge_run.add_argument(
        "--settle",
        type=float,
        default=8.0,
        help="seconds to wait after playback trigger before reading status",
    )

    request = subparsers.add_parser("request", help="raw SoundTouch API request")
    request.add_argument("method", choices=["GET", "POST", "PUT", "DELETE"])
    request.add_argument("path", help="API path such as info or full URL")
    request.add_argument("--body-file", type=Path, help="XML body for POST/PUT")
    request.add_argument("--yes", action="store_true", help="confirm raw write request")

    return parser


def run(args: argparse.Namespace) -> Any:
    device_config, stations = _load_config_if_present(args.stations)

    if args.command == "doctor":
        return _doctor(args, device_config, stations)

    if args.command == "stations":
        if args.stations_command == "list":
            return {"stations": [station.to_dict() for station in stations]}
        if args.stations_command == "validate":
            return {"streams": [validate_stream_url(station.location) for station in stations]}

    client = _client(args, device_config)

    if args.command == "serve":
        if args.web_port <= 0:
            raise ValueError("--web-port must be greater than 0")
        if args.recovery_window < 0:
            raise ValueError("--recovery-window must be 0 or greater")
        if args.recovery_poll_interval <= 0:
            raise ValueError("--recovery-poll-interval must be greater than 0")
        from .web import ControlPanelRuntime, run_control_panel

        runtime = ControlPanelRuntime(
            config_path=args.stations,
            device=device_config,
            stations=stations,
            client=client,
            playback_method=args.playback_method,
            settle=args.settle,
        )
        run_control_panel(
            host=args.bind,
            port=args.web_port,
            runtime=runtime,
            image_path=args.device_image,
            start_bridge=not args.no_bridge,
            websocket_port=args.websocket_port,
            recovery_window=args.recovery_window,
            recovery_poll_interval=args.recovery_poll_interval,
            cooldown=args.cooldown,
            reconnect_interval=args.reconnect_interval,
        )
        return None

    if args.command == "device":
        if args.device_command == "info":
            return client.info()
        if args.device_command == "now-playing":
            return client.now_playing()
        if args.device_command == "now-selection":
            return client.now_selection()

    if args.command == "presets":
        if args.presets_command == "list":
            return {"presets": [preset.to_dict() for preset in client.presets()]}
        if args.presets_command == "backup":
            return {"backup": client.backup_presets(args.out)}
        if args.presets_command == "program":
            slots = parse_slots(args.slots, stations)
            selected = [station_by_slot(stations, slot) for slot in slots]
            if args.dry_run:
                return {
                    "dry_run": True,
                    "presets": [
                        {"slot": station.slot, "xml": station_to_preset_xml(station)}
                        for station in selected
                    ],
                }
            if not args.yes:
                raise ValueError("programming presets is a live write; rerun with --yes")
            backup = client.backup_presets(args.out)
            writes = []
            for station in selected:
                response = client.store_preset(station)
                writes.append(
                    {"slot": station.slot, "name": station.name, "status": response.status}
                )
            return {"backup": backup, "programmed": writes}

    if args.command == "bridge":
        if args.bridge_command == "run":
            state = BridgeState()
            if args.poll_interval <= 0:
                raise ValueError("--poll-interval must be greater than 0")
            if args.recovery_window < 0:
                raise ValueError("--recovery-window must be 0 or greater")
            if args.recovery_poll_interval <= 0:
                raise ValueError("--recovery-poll-interval must be greater than 0")
            if args.once:
                return bridge_once(
                    client,
                    stations,
                    state,
                    cooldown=args.cooldown,
                    settle=args.settle,
                    playback_method=args.playback_method,
                )
            if args.mode == "websocket":
                run_websocket_bridge(
                    client,
                    stations,
                    state,
                    websocket_port=args.websocket_port,
                    recovery_window=args.recovery_window,
                    recovery_poll_interval=args.recovery_poll_interval,
                    cooldown=args.cooldown,
                    settle=args.settle,
                    playback_method=args.playback_method,
                    reconnect_interval=args.reconnect_interval,
                    on_result=lambda result: emit(result, as_json=args.json),
                )
            while True:
                result = bridge_once(
                    client,
                    stations,
                    state,
                    cooldown=args.cooldown,
                    settle=args.settle,
                    playback_method=args.playback_method,
                )
                if result["triggered"]:
                    emit(result, as_json=args.json)
                time.sleep(args.poll_interval)

    if args.command == "play":
        station = station_by_slot(stations, args.slot)
        if args.dry_run:
            result = {"dry_run": True, "slot": station.slot, "method": args.method}
            if args.method == "dlna":
                result["soap"] = dlna_set_av_transport_uri_body(station.location)
                result["content_item_xml"] = station_to_content_item_xml(station)
            else:
                result["xml"] = station_to_content_item_xml(station)
            result["preset_marker_xml"] = station_to_marker_content_item_xml(station)
            return result
        if not args.yes:
            raise ValueError("play changes the speaker state; rerun with --yes")
        if args.method == "dlna":
            response = client.play_station_dlna(station)
            if args.settle > 0:
                time.sleep(args.settle)
        else:
            response = client.select_station(station)
        return {
            "played": station.to_dict(),
            "method": args.method,
            "status": response.status,
            "now_playing": client.now_playing(),
        }

    if args.command == "request":
        method = args.method.upper()
        if method != "GET" and not args.yes:
            raise ValueError("raw write request requires --yes")
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else None
        response = client.request(method, args.path, body)
        return response.to_dict()

    raise AssertionError(f"unhandled command {args.command}")


def _doctor(
    args: argparse.Namespace, device_config: DeviceConfig | None, stations: list[Station]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "python": sys.version.split()[0],
        "uv_available": shutil.which("uv") is not None,
        "config_path": str(args.stations),
        "config_exists": args.stations.exists(),
        "station_count": len(stations),
        "host_source": _host_source(args, device_config),
        "host": _resolve_host(args, device_config),
    }
    if args.live:
        client = _client(args, device_config)
        result["device"] = client.info()
        result["preset_count"] = len(client.presets())
    return result


def _client(args: argparse.Namespace, device_config: DeviceConfig | None) -> SoundTouchClient:
    host = _resolve_host(args, device_config)
    if not host:
        raise ValueError("SoundTouch host missing; set --host, SOUNDTOUCH_HOST, or [device].host")
    port = (
        args.port
        or (device_config.api_port if device_config else None)
        or int(os.getenv("SOUNDTOUCH_PORT", "8090"))
    )
    dlna_port = (
        args.dlna_port
        or (device_config.dlna_port if device_config else None)
        or int(os.getenv("SOUNDTOUCH_DLNA_PORT", "8091"))
    )
    return SoundTouchClient(host=host, port=int(port), dlna_port=int(dlna_port))


def _resolve_host(args: argparse.Namespace, device_config: DeviceConfig | None) -> str | None:
    return (
        args.host or os.getenv("SOUNDTOUCH_HOST") or (device_config.host if device_config else None)
    )


def _host_source(args: argparse.Namespace, device_config: DeviceConfig | None) -> str:
    if args.host:
        return "flag"
    if os.getenv("SOUNDTOUCH_HOST"):
        return "env"
    if device_config is not None and device_config.host:
        return "config"
    return "missing"


def _load_config_if_present(path: Path) -> tuple[DeviceConfig | None, list[Station]]:
    if not path.exists():
        return None, []
    return load_station_config(path)


if __name__ == "__main__":
    main()
