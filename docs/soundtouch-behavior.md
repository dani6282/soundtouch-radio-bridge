# SoundTouch Behavior and API Notes

These notes summarize behavior observed while developing `soundtouch-radio` and
web/API details that matter for this bridge. They are intentionally generic;
keep household-specific deployment notes in your own operations repository.

## Sources Checked

Checked on 2026-07-07:

- Bose-hosted
  [SoundTouch Web API PDF](https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf),
  title `2026.4.1 SoundTouch Web API`, version 1.1, created 2026-04-06.
- Earlier Bose SoundTouch Web API version 1.0, dated 2026-01-07, mirrored as
  [`captivus/bose-soundtouch/docs/soundtouch-web-api.md`](https://github.com/captivus/bose-soundtouch/blob/master/docs/soundtouch-web-api.md)
  and backed by that repository's dated PDF.
- [`captivus/bose-soundtouch`](https://github.com/captivus/bose-soundtouch),
  a Python wrapper that implements the documented HTTP surface.
- [`thlucas1/bosesoundtouchapi`](https://github.com/thlucas1/bosesoundtouchapi),
  a Python wrapper with a broader websocket notification enum and comments about
  standby/power-related notifications.
- [`Adeptive/SoundTouch-NodeJS`](https://github.com/Adeptive/SoundTouch-NodeJS),
  an older Node wrapper that derives powered-on/off state from
  `nowPlayingUpdated` source changes.
- Home Assistant
  [`homeassistant/components/soundtouch`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/soundtouch),
  which is a local-polling integration backed by `libsoundtouch==0.8`.
- [`CharlesBlonde/libsoundtouch`](https://github.com/CharlesBlonde/libsoundtouch),
  the protocol library used by Home Assistant.

## Discovery

The Bose Web API 1.1 PDF adds discovery guidance.

SoundTouch speakers can be discovered via:

- SSDP over UDP port `1900`;
- mDNS/Bonjour/zeroconf service `_soundtouch._tcp.local`;
- AirPlay-capable speakers may also advertise `_raop._tcp.local`.

For SSDP, the PDF identifies these UPnP service types:

- `urn:schemas-upnp-org:device:MediaRenderer:1` for devices that can play
  audio, including SoundTouch speakers;
- `urn:schemas-upnp-org:device:MediaServer:1` for devices containing media,
  including the SoundTouch app and music servers.

The PDF notes that clients should track SSDP expiration from `Cache-Control:
max-age`; the UPnP minimum is 1800 seconds. Home Assistant uses zeroconf for
discovery with `_soundtouch._tcp.local`, matching the PDF.

## Useful Local APIs

- SoundTouch HTTP API: port `8090`
- SoundTouch websocket: port `8080`, subprotocol `gabbo`
- SoundTouch DLNA/UPnP renderer: port `8091`

The bridge uses websocket events for button intent and DLNA/UPnP for playback.

The Bose Web API document describes the primary HTTP command surface as `GET`
and `POST` requests on port `8090`, and websocket notifications on port `8080`
with websocket protocol `gabbo`.

## HTTP API Surface

Relevant documented endpoints:

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/key` | `POST` | Remote-control key event. Send a `press` XML body and then a `release` body for normal button clicks. |
| `/select` | `POST` | Selects a source such as AUX, Bluetooth, or product input. Product/source support varies; query `/sources`. |
| `/sources` | `GET` | Lists available sources and readiness. |
| `/nowPlaying` / `/now_playing` | `GET` | The Bose PDF names `/nowPlaying`; the older `libsoundtouch`, Home Assistant, Adeptive wrapper, and this bridge use `/now_playing` successfully on tested speakers. Both spellings should be treated as ecosystem variants until tested per device/firmware. |
| `/trackInfo` | `GET` | Similar shape to now-playing metadata. |
| `/volume` | `GET`/`POST` | Reads or writes target/actual volume and mute state. |
| `/presets` | `GET` | Reads preset slots and their `ContentItem`s. |
| `/info` | `GET` | Device id, name, type, software version, component info, network info. |
| `/capabilities` | `GET` | Feature discovery; wrappers use this before optional endpoints. |
| `/getZone`, `/setZone`, `/addZoneSlave`, `/removeZoneSlave` | mixed | Multi-room zone management. |

Relevant observed/wrapper endpoints not all present in the official endpoint
list:

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/nowSelection` | `GET` | Current selected preset/source marker. Used by this bridge and declared by `bosesoundtouchapi`. |
| `/storePreset` | `POST` | Writes a preset slot. Used by this bridge and declared by `bosesoundtouchapi`. |

Relevant `/key` values from the Bose Web API:

- transport: `PLAY`, `PAUSE`, `STOP`, `PREV_TRACK`, `NEXT_TRACK`,
  `PLAY_PAUSE`;
- power/volume/input: `POWER`, `MUTE`, `VOLUME_UP`, `VOLUME_DOWN`,
  `AUX_INPUT`;
- presets: `PRESET_1` through `PRESET_6`;
- service controls: `THUMBS_UP`, `THUMBS_DOWN`, `BOOKMARK`,
  `ADD_FAVORITE`, `REMOVE_FAVORITE`;
- shuffle/repeat: `SHUFFLE_OFF`, `SHUFFLE_ON`, `REPEAT_OFF`, `REPEAT_ONE`,
  `REPEAT_ALL`.

`POWER` is a standby/wake toggle, not a software reboot. The
`thlucas1/bosesoundtouchapi` key comments describe standby as preserving network
connections, which matches observed behavior: the HTTP API can remain available
while `/now_playing` reports `source="STANDBY"`.

## Preset Markers

Direct web-radio URLs stored as physical SoundTouch presets may update display
or API state but still fail audibly. On tested firmware, local AUX marker
presets were more reliable for detecting physical button intent.

Accepted markers must be distinct. If two slots use the exact same marker
content, the SoundTouch preset store may collapse them into one entry.

Known-good marker shapes include:

| Marker location | Marker name example |
| --- | --- |
| `/local/aux` | `AUX IN 1` |
| `AUX` | `AUX IN 2` |
| `/local/aux/3` | `AUX IN 3` |
| `/local/aux/4` | `AUX IN 4` |
| `AUX_5` | `AUX IN 5` |
| `AUX_6` | `AUX IN 6` |

## Websocket Notifications

The websocket server is reached at `ws://<speaker>:8080/` with subprotocol
`gabbo`. Payloads are XML. The official examples use either:

- `<updates deviceID="...">` with one or more child event nodes; or
- a single root node for non-`updates` messages, as implemented by
  `bosesoundtouchapi`.

Official Web API notification classes:

| Event tag | Meaning for this bridge |
| --- | --- |
| `presetsUpdated` | Preset list changed; refresh `/presets` if needed. |
| `recentsUpdated` | Recent-list changed; usually irrelevant. |
| `acctModeUpdated` | Cloud/account association changed. |
| `errorUpdate` / error notification | Error signal; useful for diagnostics/recovery. |
| `nowPlayingUpdated` | Playback/source state changed. Includes a nested `nowPlaying` payload in documented examples. |
| `volumeUpdated` | Volume/mute changed. |
| `bassUpdated` | Bass setting changed. |
| `zoneUpdated` | Multi-room zone changed. |
| `swUpdateStatusUpdated` | Software update status changed. |
| `siteSurveyResultsUpdated` | Wi-Fi site survey result changed. |
| `sourcesUpdated` | Source list changed. |
| `nowSelectionUpdated` | Selected source/preset changed. This is the physical-preset intent event used by the bridge. |
| `connectionStateUpdated` | Network connection state changed. |
| `infoUpdated` | Device info such as name changed. |

Additional event categories declared by `thlucas1/bosesoundtouchapi` and worth
recognizing in raw captures:

- `SoundTouchSdkInfo`
- `userActivityUpdate`
- `LowPowerStandbyUpdate`
- `criticalErrorUpdate`
- `errorNotification`
- `groupUpdated`
- `languageUpdated`
- `nameUpdated`
- `soundTouchConfigurationUpdated`
- `audiodspcontrols`
- `audioproducttonecontrols`
- `audioproductlevelcontrols`
- `productcechdmicontrol`
- websocket lifecycle categories in the client wrapper:
  `WebSocketOpen`, `WebSocketClose`, `WebSocketError`, `WebSocketPing`,
  `WebSocketPong`

Power-related observations from source review:

- There is no separate documented `powerButtonPressed` websocket event.
- `userActivityUpdate` is a generic signal for manual user activity, including
  physical remote/device button activity. It is not enough by itself to identify
  the button.
- `nowPlayingUpdated` can signal standby because the current source changes to
  `STANDBY`.
- `nowSelectionUpdated` can occur when source selection changes and, per
  `bosesoundtouchapi` comments, around power on/off.
- The older Node wrapper treats `nowPlayingUpdated.nowPlaying.source != STANDBY`
  as powered-on, and `source == STANDBY` as powered-off.

Bridge implication: only `nowSelectionUpdated` with a matching preset marker
should start station playback. Generic `userActivityUpdate` and
`nowPlayingUpdated` should be diagnostic/state signals, not station-play intent.

## Playback

The `/select` API can accept direct stream metadata without reliably producing
audible playback. DLNA `AVTransport#SetAVTransportURI` was the reliable
playback path in the tested setup.

Use plain `http://` MP3 streams first. HLS, app-only streams, expiring tokens,
and HTTPS-only endpoints may be device- or firmware-dependent.

Observed failure mode: the DLNA renderer can return HTTP 200 while the speaker
remains on `AUX` or `STANDBY` and `/now_playing` never moves to the target UPNP
URL. A physical speaker reboot cleared that state in testing. The web runtime
therefore records both immediate and delayed playback plausibility checks, and
its recovery action performs a health check, STOP, target stream replay, and a
second `/now_playing` check. The SoundTouch POWER key is a toggle rather than a
deterministic reboot command, so it should not be used automatically.

Recovery must be careful around standby. If a user turns the speaker off shortly
after playback starts, a delayed follow-up check can see `STANDBY`. That is
likely intentional shutdown, not a failed stream. Auto-recovery should not
blindly replay the last station from `STANDBY`; otherwise it can resurrect the
speaker after the user turned it off. The web runtime now treats standby as
`standby_not_recovered` and performs no automatic recovery actions. Follow-up
checks and automatic recovery are also tied to a `play_attempt_id`; stale
follow-ups from older button presses are recorded and skipped.

Safer recovery targets:

- DLNA returned HTTP 200 but `/now_playing` remains `AUX`.
- DLNA returned HTTP 200 but `/now_playing` remains on the wrong non-standby
  source.
- DLNA returned HTTP 200, source is `UPNP`, but `content_location` is the wrong
  stream URL.

Avoid automatic recovery when:

- `/now_playing` reports `STANDBY`, unless a future raw-event classifier can
  prove this was not an intentional power-off.
- the only signal is `userActivityUpdate`.
- the speaker is unreachable during a physical power cycle; the websocket loop
  should reconnect instead of treating the outage as a playback failure.

## Home Assistant Integration

The current Home Assistant SoundTouch integration is useful prior art, but it is
not the same architecture as this bridge.

Home Assistant facts from `homeassistant/components/soundtouch`:

- manifest dependency: `libsoundtouch==0.8`;
- integration type: device;
- IoT class: `local_polling`;
- discovery: zeroconf `_soundtouch._tcp.local`;
- setup creates a `SoundTouchDevice` via `libsoundtouch.soundtouch_device(host)`;
- entity `update()` polls `status()`, `volume()`, and `zone_status()`;
- `STANDBY` maps to media-player off;
- `PLAY_STATE` and `BUFFERING_STATE` map to playing;
- `PAUSE_STATE` maps to paused;
- `STOP_STATE` maps to off;
- `INVALID_SOURCE` maps to unknown/none;
- source selection only exposes AUX and Bluetooth;
- `play_media()` accepts plain `http://` URLs and rejects/ignores HTTPS for URL
  playback;
- direct URL playback delegates to `libsoundtouch.play_url()`, which uses DLNA
  `AVTransport#SetAVTransportURI` on port `8091`;
- preset playback in Home Assistant calls `/select` with the preset's stored
  `ContentItem`, not a physical-button marker bridge;
- `turn_on()` and `turn_off()` delegate to `libsoundtouch.power_on()` and
  `power_off()`, which read status first and send `POWER` only if the current
  source is respectively `STANDBY` or not `STANDBY`;
- multi-room services are `play_everywhere`, `create_zone`, `add_zone_slave`,
  and `remove_zone_slave`.

`libsoundtouch` also contains websocket support, but Home Assistant's current
entity path does not use it for state updates. The library's websocket parser
handles only `volumeUpdated`, `nowPlayingUpdated`, `presetsUpdated`,
`zoneUpdated`, and `infoUpdated`. This is narrower than the Bose PDF and the
newer `bosesoundtouchapi` category enum.

Operational lessons from Home Assistant:

- Treat `STANDBY` as off, not as failed playback.
- Guard `POWER` with a status read when possible; it is still a toggle, but HA's
  wrapper avoids toggling blindly.
- DLNA direct URL playback and `http://`-only stream handling are established
  prior art.
- HA does not solve physical preset-button bridging; it plays stored presets or
  direct URLs through service calls.

## Polling

Default bridge operation should be websocket-only:

```sh
soundtouch-radio --stations stations.toml bridge run \
  --mode websocket \
  --playback-method dlna \
  --recovery-window 0
```

Recovery polling exists for diagnostics and lossy event environments. Leave it
off unless you have measured missed events.

## Diagnostics

For persistent captures during normal bridge operation, run with a JSONL
diagnostic log:

```sh
soundtouch-radio --stations stations.toml serve \
  --diagnostic-log diagnostics/soundtouch-radio.jsonl
```

The JSONL log records raw websocket XML, parsed event tags, connection ids,
listener lifecycle events, bridge decisions, health checks, follow-up checks,
and recovery actions. Keep it on persistent private storage; it may include
device identifiers, station URLs, and raw track/source metadata.

To identify new websocket behavior, capture raw XML before changing parser logic:

```sh
uv run --with websocket-client python scripts/capture_events.py <speaker-ip> --seconds 90
```

Useful manual probes:

```sh
curl -fsS http://<speaker-ip>:8090/info
curl -fsS http://<speaker-ip>:8090/now_playing
curl -fsS http://<speaker-ip>:8090/nowSelection
curl -fsS http://<speaker-ip>:8090/presets
```

When investigating power/off behavior, capture these together:

- raw websocket XML;
- `/now_playing` before and after the button press;
- `/nowSelection` before and after the button press;
- whether the action was physical Power, remote Power, app standby, or wall
  power cycle.
