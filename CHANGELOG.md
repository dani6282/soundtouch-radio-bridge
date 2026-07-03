# Changelog

## 0.2.0a1 - 2026-07-03

- Add `soundtouch-radio serve` with a private-LAN web control panel.
- Add station name/URL editing with atomic TOML writes.
- Add volume, transport key, direct play, manual health-check, and bridge
  listener status APIs.
- Keep the web status refresh local to the app process; no idle Bose HTTP
  polling is introduced.

## 0.1.0a1 - 2026-07-02

- Alpha release.
- Restores Bose SoundTouch physical preset buttons using distinct local marker
  presets.
- Uses websocket events for button detection and DLNA/UPnP for playback.
- Keeps recovery polling off by default.
- Provides generic package documentation, sample station config, Docker example,
  and a Berlin station example.
