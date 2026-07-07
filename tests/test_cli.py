from __future__ import annotations

from soundtouch_radio.cli import run
from soundtouch_radio.cli import build_parser


def test_doctor_without_config_is_ok(tmp_path) -> None:
    parser = build_parser()
    args = parser.parse_args(["--stations", str(tmp_path / "missing.toml"), "--json", "doctor"])

    result = run(args)

    assert result["ok"] is True
    assert result["config_exists"] is False
    assert result["host_source"] == "missing"


def test_play_dry_run_defaults_to_dlna(tmp_path) -> None:
    config = tmp_path / "stations.toml"
    config.write_text(
        """
[device]
host = "192.0.2.1"

[[station]]
slot = 1
name = "Station One"
location = "http://example.test/live.mp3"
"""
    )
    parser = build_parser()
    args = parser.parse_args(["--stations", str(config), "--json", "play", "1", "--dry-run"])

    result = run(args)

    assert result["method"] == "dlna"
    assert "SetAVTransportURI" in result["soap"]
    assert 'source="UPNP"' in result["content_item_xml"]


def test_bridge_recovery_polling_defaults_off() -> None:
    parser = build_parser()
    args = parser.parse_args(["bridge", "run"])

    assert args.mode == "websocket"
    assert args.playback_method == "dlna"
    assert args.recovery_window == 0.0
    assert args.recovery_poll_interval == 0.1
    assert args.diagnostic_log is None


def test_bridge_accepts_diagnostic_log_path() -> None:
    parser = build_parser()
    args = parser.parse_args(["bridge", "run", "--diagnostic-log", "work/bridge.jsonl"])

    assert str(args.diagnostic_log) == "work/bridge.jsonl"


def test_serve_defaults_to_websocket_bridge_without_recovery_polling() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve"])

    assert args.bind == "127.0.0.1"
    assert args.web_port == 8788
    assert args.no_bridge is False
    assert args.playback_method == "dlna"
    assert args.recovery_window == 0.0
    assert args.recovery_poll_interval == 0.1
    assert args.diagnostic_followup_delay == 5.0
    assert args.auto_recover is False
    assert args.diagnostic_log is None


def test_serve_accepts_diagnostic_log_path() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve", "--diagnostic-log", "work/serve.jsonl"])

    assert str(args.diagnostic_log) == "work/serve.jsonl"
