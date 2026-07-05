# Changelog

## 0.2.0a2 - 2026-07-05

- Single-source the package version from installed metadata instead of a
  hard-coded value.
- Derive the outbound HTTP `User-Agent` from the package version.
- Add continuous integration (ruff lint, ruff format check, and pytest on
  Python 3.11 and 3.12) plus a tag-triggered release build.
- Ship a PEP 561 `py.typed` marker so downstream type checkers use the hints.
- Use explicit UTF-8 encoding for station-config and preset-backup file I/O.
- Tighten CLI typing to use `DeviceConfig` instead of `object`.

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
