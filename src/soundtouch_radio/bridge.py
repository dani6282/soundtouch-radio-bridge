from __future__ import annotations

from dataclasses import dataclass
import threading
from time import monotonic, sleep
from typing import Any, Callable, Literal

from .models import Station, is_recovery_signal_xml, parse_now_selection_update_xml
from .soundtouch import SoundTouchClient, SoundTouchError

PlaybackMethod = Literal["dlna", "select"]
DEFAULT_PLAYBACK_METHOD: PlaybackMethod = "dlna"


@dataclass
class BridgeState:
    last_triggered_location: str | None = None
    last_triggered_at: float = 0.0
    last_selection_key: tuple[int | None, str | None] | None = None


def station_for_now_playing(now_playing: dict[str, Any], stations: list[Station]) -> Station | None:
    if now_playing.get("source") != "UPNP":
        return None
    location = now_playing.get("content_location")
    if not location:
        return None
    for station in stations:
        if station.location == location:
            return station
    return None


def station_for_now_selection(
    now_selection: dict[str, Any], stations: list[Station]
) -> Station | None:
    preset_id = now_selection.get("preset_id")
    if preset_id is None:
        return None
    for station in stations:
        if station.slot == preset_id:
            return station
    return None


def playback_check_for_target(
    *,
    expected_source: str,
    expected_location: str,
    now_playing: dict[str, Any] | None,
    status: int | None = None,
) -> dict[str, Any]:
    observed = now_playing or {}
    observed_source = observed.get("source")
    observed_location = observed.get("content_location")
    observed_play_status = observed.get("play_status")
    transport_accepted = None
    if status is not None:
        transport_accepted = 200 <= status < 300
        if not transport_accepted:
            return {
                "plausible": False,
                "reason": "transport_not_accepted",
                "transport_accepted": transport_accepted,
                "status": status,
                "expected_source": expected_source,
                "expected_location": expected_location,
                "observed_source": observed_source,
                "observed_location": observed_location,
                "observed_play_status": observed_play_status,
            }

    reason = "target_stream_selected"
    plausible = (
        observed_source == expected_source
        and observed_location == expected_location
        and observed_play_status not in {"PAUSE_STATE", "STOP_STATE"}
    )
    if not plausible:
        if not observed:
            reason = "missing_now_playing"
        elif observed_source == "AUX":
            reason = "source_stayed_aux"
        elif observed_source == "STANDBY":
            reason = "source_stayed_standby"
        elif observed_source == "INVALID_SOURCE":
            reason = "invalid_source"
        elif observed_source != expected_source:
            reason = "wrong_source"
        elif observed_location != expected_location:
            reason = "wrong_location"
        else:
            reason = "target_stream_not_playing"

    return {
        "plausible": plausible,
        "reason": reason,
        "transport_accepted": transport_accepted,
        "status": status,
        "expected_source": expected_source,
        "expected_location": expected_location,
        "observed_source": observed_source,
        "observed_location": observed_location,
        "observed_play_status": observed_play_status,
    }


def playback_check_for_station(
    station: Station, now_playing: dict[str, Any] | None, *, status: int | None = None
) -> dict[str, Any]:
    return playback_check_for_target(
        expected_source=station.source,
        expected_location=station.location,
        now_playing=now_playing,
        status=status,
    )


def selection_key(now_selection: dict[str, Any]) -> tuple[int | None, str | None]:
    return (now_selection.get("preset_id"), now_selection.get("content_location"))


def selection_looks_active(
    now_selection: dict[str, Any],
    now_playing: dict[str, Any],
    station: Station,
    state: BridgeState,
) -> bool:
    key = selection_key(now_selection)
    if state.last_selection_key is not None and key != state.last_selection_key:
        return True
    if now_playing.get("source") == "INVALID_SOURCE":
        return True
    return (
        now_playing.get("source") == "UPNP"
        and now_playing.get("content_location") == station.location
        and now_playing.get("play_status") != "PLAY_STATE"
    )


def bridge_once(
    client: SoundTouchClient,
    stations: list[Station],
    state: BridgeState,
    *,
    cooldown: float,
    settle: float,
    playback_method: PlaybackMethod = DEFAULT_PLAYBACK_METHOD,
) -> dict[str, Any]:
    now_selection = client.now_selection()
    now_playing = client.now_playing()
    station = station_for_now_selection(now_selection, stations)
    trigger_source = "now_selection"
    if station is None:
        station = station_for_now_playing(now_playing, stations)
        trigger_source = "now_playing"
    if station is None:
        return {
            "triggered": False,
            "reason": "no_configured_marker",
            "now_selection": now_selection,
            "now_playing": now_playing,
        }
    return bridge_station_marker(
        client,
        station,
        state,
        now_selection=now_selection,
        now_playing=now_playing,
        cooldown=cooldown,
        settle=settle,
        trigger_source=trigger_source,
        require_active_selection=trigger_source == "now_selection",
        use_cooldown=True,
        skip_already_playing=True,
        playback_method=playback_method,
    )


def bridge_station_marker(
    client: SoundTouchClient,
    station: Station,
    state: BridgeState,
    *,
    now_selection: dict[str, Any],
    now_playing: dict[str, Any],
    cooldown: float,
    settle: float,
    trigger_source: str,
    require_active_selection: bool,
    use_cooldown: bool,
    skip_already_playing: bool,
    playback_method: PlaybackMethod,
) -> dict[str, Any]:
    same_stream_is_playing = (
        now_playing.get("content_location") == station.location
        and now_playing.get("play_status") == "PLAY_STATE"
    )
    if skip_already_playing and same_stream_is_playing:
        state.last_triggered_location = station.location
        state.last_triggered_at = monotonic()
        state.last_selection_key = selection_key(now_selection)
        return {
            "triggered": False,
            "reason": "already_playing",
            "trigger_source": trigger_source,
            "station": station.to_dict(),
            "now_selection": now_selection,
            "now_playing": now_playing,
        }
    if require_active_selection and not selection_looks_active(
        now_selection,
        now_playing,
        station,
        state,
    ):
        return {
            "triggered": False,
            "reason": "stale_selection",
            "trigger_source": trigger_source,
            "station": station.to_dict(),
            "now_selection": now_selection,
            "now_playing": now_playing,
        }

    now = monotonic()
    recently_triggered = (
        state.last_triggered_location == station.location
        and now - state.last_triggered_at < cooldown
    )
    if use_cooldown and recently_triggered:
        return {
            "triggered": False,
            "reason": "cooldown",
            "trigger_source": trigger_source,
            "station": station.to_dict(),
            "now_selection": now_selection,
            "now_playing": now_playing,
        }

    response = play_station(client, station, playback_method)
    state.last_triggered_location = station.location
    state.last_triggered_at = monotonic()
    state.last_selection_key = selection_key(now_selection)
    if settle > 0:
        sleep(settle)
    after = client.now_playing()
    return {
        "triggered": True,
        "trigger_source": trigger_source,
        "station": station.to_dict(),
        "status": response.status,
        "now_selection": now_selection,
        "before": now_playing,
        "after": after,
        "playback_check": playback_check_for_station(station, after, status=response.status),
    }


def play_station(
    client: SoundTouchClient, station: Station, playback_method: PlaybackMethod
) -> Any:
    if playback_method == "select":
        return client.select_station(station)
    if playback_method == "dlna":
        return client.play_station_dlna(station)
    raise ValueError(f"unsupported playback method {playback_method}")


def bridge_websocket_message(
    client: SoundTouchClient,
    stations: list[Station],
    state: BridgeState,
    message: str | bytes,
    *,
    settle: float,
    playback_method: PlaybackMethod = DEFAULT_PLAYBACK_METHOD,
) -> dict[str, Any] | None:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    now_selection = parse_now_selection_update_xml(message)
    if now_selection is None:
        return None
    now_playing = client.now_playing()
    station = station_for_now_selection(now_selection, stations)
    if station is None:
        return {
            "triggered": False,
            "reason": "no_configured_marker",
            "trigger_source": "websocket_now_selection",
            "now_selection": now_selection,
            "now_playing": now_playing,
        }
    return bridge_station_marker(
        client,
        station,
        state,
        now_selection=now_selection,
        now_playing=now_playing,
        cooldown=0,
        settle=settle,
        trigger_source="websocket_now_selection",
        require_active_selection=False,
        use_cooldown=False,
        skip_already_playing=False,
        playback_method=playback_method,
    )


def run_websocket_bridge(
    client: SoundTouchClient,
    stations: list[Station],
    state: BridgeState,
    *,
    websocket_port: int,
    recovery_window: float,
    recovery_poll_interval: float,
    cooldown: float,
    settle: float,
    playback_method: PlaybackMethod,
    reconnect_interval: float,
    on_result: Callable[[dict[str, Any]], None],
    on_status: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    try:
        from websocket import WebSocketApp
    except ImportError as exc:
        raise SoundTouchError("websocket bridge requires the websocket-client package") from exc

    url = f"ws://{client.host}:{websocket_port}/"
    trigger_lock = threading.Lock()
    recovery_lock = threading.Lock()
    recovery_until = 0.0
    recovery_thread_running = False

    def notify_status(update: dict[str, Any]) -> None:
        if on_status is None:
            return
        try:
            on_status(update)
        except Exception:
            pass

    def on_message(_ws: WebSocketApp, message: str | bytes) -> None:
        message_text = (
            message.decode("utf-8", errors="replace") if isinstance(message, bytes) else message
        )
        notify_status({"event": "message"})
        with trigger_lock:
            result = bridge_websocket_message(
                client,
                stations,
                state,
                message_text,
                settle=settle,
                playback_method=playback_method,
            )
        if result is not None and result["triggered"]:
            on_result(result)
        if result is None and is_recovery_signal_xml(message_text):
            start_recovery_burst()

    def start_recovery_burst() -> None:
        nonlocal recovery_thread_running, recovery_until
        if recovery_window <= 0:
            return
        with recovery_lock:
            recovery_until = max(recovery_until, monotonic() + recovery_window)
            if recovery_thread_running:
                return
            recovery_thread_running = True
        threading.Thread(target=recovery_loop, daemon=True).start()

    def recovery_loop() -> None:
        nonlocal recovery_thread_running, recovery_until
        try:
            while True:
                with recovery_lock:
                    remaining = recovery_until - monotonic()
                if remaining <= 0:
                    return
                with trigger_lock:
                    result = bridge_once(
                        client,
                        stations,
                        state,
                        cooldown=cooldown,
                        settle=settle,
                        playback_method=playback_method,
                    )
                if result["triggered"]:
                    on_result(result)
                    with recovery_lock:
                        recovery_until = 0.0
                    return
                sleep(min(recovery_poll_interval, remaining))
        finally:
            with recovery_lock:
                recovery_thread_running = False

    def reconnecting_websocket_loop() -> None:
        notify_status({"event": "starting", "url": url})
        while True:
            with trigger_lock:
                initial_result = bridge_once(
                    client,
                    stations,
                    state,
                    cooldown=0,
                    settle=settle,
                    playback_method=playback_method,
                )
            if initial_result["triggered"]:
                on_result(initial_result)

            def on_open(_ws: WebSocketApp) -> None:
                notify_status({"event": "connected", "url": url})

            def on_error(_ws: WebSocketApp, error: Any) -> None:
                notify_status({"event": "error", "error": str(error)})

            def on_close(
                _ws: WebSocketApp,
                close_status_code: int | None,
                close_msg: str | None,
            ) -> None:
                notify_status(
                    {
                        "event": "disconnected",
                        "code": close_status_code,
                        "message": close_msg,
                    }
                )

            notify_status({"event": "connecting", "url": url})
            ws = WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                subprotocols=["gabbo"],
            )
            ws.run_forever(ping_interval=0)
            sleep(reconnect_interval)

    reconnecting_websocket_loop()
