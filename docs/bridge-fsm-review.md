# Bridge Finite State Machine Review

Reviewed on 2026-07-07.

This review describes the current `soundtouch-radio` runtime as a finite state
machine. It focuses on whether physical preset button intent, playback, listener
health, and recovery are represented clearly enough to diagnose failures.

## Goal

The target household workflow is:

1. A user presses physical preset button `1` to `6`.
2. The Bose selects the harmless local marker preset stored in that slot.
3. The bridge receives a `nowSelectionUpdated` websocket message.
4. The bridge maps the preset slot to a configured station.
5. The bridge sends the station URL to the Bose DLNA renderer.
6. The Bose fetches and plays the stream.

The bridge must not restart playback when the user intentionally turns the
speaker off.

## Runtime States

The bridge process can be modeled as these states:

| State | Description | Main exit events |
| --- | --- | --- |
| `Starting` | Process has configuration and a SoundTouch client. | start bridge or serve UI |
| `InitialCheck` | Polls `/nowSelection` and `/now_playing` once before opening websocket. | marker found, no marker, API error |
| `Connecting` | Opens `ws://<speaker>:8080/` with subprotocol `gabbo`. | connected, connection error |
| `ConnectedListening` | Waits for websocket XML frames. | message, disconnect, error |
| `ClassifyingMessage` | Records raw XML, extracts event tags, tries to parse `nowSelectionUpdated`. | selection event, recovery signal, irrelevant event |
| `TriggeringPlayback` | Sends station URL via DLNA `SetAVTransportURI`. | accepted, rejected, API error |
| `CheckingPlayback` | Reads `/now_playing` after a settle delay. | plausible, implausible |
| `FollowupCheck` | Optional delayed status read for one play attempt. | plausible, recoverable failure, standby/off, stale attempt |
| `Recovery` | Optional STOP and replay for the same non-stale, non-standby play attempt. | recovered, still failed, skipped |
| `Reconnecting` | Sleeps briefly and starts another websocket connection attempt. | next `InitialCheck` |

## Inputs

The important external inputs are:

- raw websocket XML from the Bose;
- `/nowSelection`;
- `/now_playing`;
- DLNA `SetAVTransportURI` HTTP status;
- user-initiated web actions such as Play, Recover, volume, and key commands.

The persistent diagnostic log records these inputs and decisions when
`--diagnostic-log` is configured.

Every bridge/manual playback creates a monotonic `play_attempt_id`. Delayed
follow-up checks and recovery actions are bound to that id. If a newer playback
attempt exists by the time an older follow-up or recovery runs, the older path is
recorded as stale and performs no speaker actions.

## Transition Rules

### Websocket Message

`ConnectedListening -> ClassifyingMessage`

Every websocket frame is recorded as `websocket_message` with:

- `connection_id`;
- `event_tags`;
- `raw_message`.

The in-memory UI diagnostics keep only compact summaries so the status page does
not become a raw XML dump.

### Physical Preset Intent

`ClassifyingMessage -> TriggeringPlayback`

Only `nowSelectionUpdated` messages with a configured preset id are station-play
intent. Generic `userActivityUpdate` and `nowPlayingUpdated` are not enough to
start playback.

Current implementation note: the websocket path maps by preset id. It does not
also verify that the event's marker `ContentItem` matches the configured marker
fields. That keeps button handling simple, but it means externally rewriting a
Bose preset slot can make that slot trigger the configured station anyway.

### Playback Plausibility

`CheckingPlayback -> plausible | implausible`

Playback is plausible when `/now_playing` reports the expected source and stream
URL, and the play status is not paused or stopped.

Important implausible reasons:

- `source_stayed_aux`;
- `source_stayed_standby`;
- `wrong_source`;
- `wrong_location`;
- `transport_not_accepted`.

### Recovery

`FollowupCheck -> Recovery`

Automatic recovery is allowed only for non-standby failures. `STANDBY` is
treated as off, not as failed playback. Recovery from standby would resurrect the
speaker after a user intentionally turned it off.

Manual recovery also refuses to replay from standby unless the caller explicitly
sets `allow_power_toggle`.

Recovery is also attempt-bound. A delayed follow-up from slot 1 cannot recover
slot 1 after the user has already pressed slot 5; it records `stale_followup` or
`stale_recovery` instead.

## Findings

### Corrected: Listener Initial Check Could Kill The Loop

Before this review, the reconnect loop ran `bridge_once()` before opening each
websocket connection without catching SoundTouch API failures. If the speaker was
temporarily unreachable during a physical power cycle, that exception could
escape and stop the listener thread.

The loop now records `initial_check_error` and proceeds to the websocket
connection attempt/reconnect path.

### Corrected: Recovery Could Replay From Standby

Before this review, `STANDBY` was classified as implausible playback and the web
runtime recovery path could send STOP and replay the last station. That matches
the observed "turned back on after being turned off" failure mode.

Recovery now records `standby_not_recovered` and performs no actions unless
power-toggle recovery is explicitly allowed.

### Corrected: Stale Follow-Up Could Recover The Wrong Attempt

Before this review, a delayed follow-up captured one station, but recovery later
looked up the latest expected station. If the user pressed another preset during
the follow-up window, the old check could schedule recovery against newer global
state.

Follow-up and recovery now carry `play_attempt_id`; stale attempts are logged and
cannot send STOP, POWER, or replay commands.

### Remaining Risk: Half-Open Websocket Detection

`websocket-client` is still run with `ping_interval=0`. If a network path becomes
half-open without the TCP socket closing, the bridge might appear connected while
no events arrive.

The diagnostic log now distinguishes "connected but no button event recorded"
from "disconnected/reconnecting." If that pattern appears, the next low-risk
change is either websocket ping support or a scheduled listener-only reconnect.

### Remaining Risk: Slot-Only Marker Matching

The runtime assumes preset slot id is enough to identify user intent. That is
probably right for a household installation where this bridge owns the six
markers, but the raw diagnostic log should be checked after any surprising
trigger to see whether the marker `ContentItem` was changed externally.

## Diagnostic Events

When `--diagnostic-log <path>` is configured, the bridge appends JSONL records:

| Kind | Purpose |
| --- | --- |
| `websocket_message` | Raw Bose websocket XML plus parsed top-level tags. |
| `listener_status` | Start/connect/disconnect/error/message lifecycle. |
| `bridge_result` | Full bridge decision and playback check. |
| `manual_play` | Web UI direct play result. |
| `health_check` | Manual or recovery health probe results. |
| `followup_check` | Delayed post-playback plausibility check, including stale attempt skips. |
| `recovery` | Recovery decision, actions, and post-action state. |
| `key_command` | Manual key commands sent through the web UI. |

## Next Incident Checklist

For a failed button press, inspect the JSONL log in this order:

1. Was there a `websocket_message` at the button-press time?
2. Did it include `nowSelectionUpdated`?
3. Did a `bridge_result` follow it?
4. What `play_attempt_id` did it create?
5. If not triggered, what was the bridge `reason`?
6. If triggered, did DLNA return a 2xx status?
7. What did `playback_check.reason` report?
8. Did `/now_playing` show `STANDBY`, `AUX`, wrong `UPNP` URL, or the expected URL?
9. Did any follow-up or recovery record `stale_followup`, `stale_recovery`, or
   `standby_not_recovered`?

That sequence identifies whether the failure is listener delivery, event
parsing, preset mapping, renderer acceptance, renderer state, or stream playback.
