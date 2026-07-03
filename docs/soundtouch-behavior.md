# SoundTouch Behavior Notes

These notes summarize behavior observed while developing `soundtouch-radio`.
They are intentionally generic; keep household-specific deployment notes in
your own operations repository.

## Useful Local APIs

- SoundTouch HTTP API: port `8090`
- SoundTouch websocket: port `8080`, subprotocol `gabbo`
- SoundTouch DLNA/UPnP renderer: port `8091`

The bridge uses websocket events for button intent and DLNA/UPnP for playback.

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

## Playback

The `/select` API can accept direct stream metadata without reliably producing
audible playback. DLNA `AVTransport#SetAVTransportURI` was the reliable
playback path in the tested setup.

Use plain `http://` MP3 streams first. HLS, app-only streams, expiring tokens,
and HTTPS-only endpoints may be device- or firmware-dependent.

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
