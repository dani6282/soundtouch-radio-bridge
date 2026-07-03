from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from soundtouch_radio.models import parse_now_playing_xml, parse_now_selection_xml  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture SoundTouch state changes.")
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    deadline = time.monotonic() + args.seconds
    previous: dict | None = None
    while time.monotonic() < deadline:
        state = {
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
            "now_selection": _read_selection(base_url),
            "now_playing": _read_playing(base_url),
        }
        comparable = {
            "now_selection": state["now_selection"],
            "now_playing": state["now_playing"],
        }
        if comparable != previous:
            print(json.dumps(state, sort_keys=True), flush=True)
            previous = comparable
        time.sleep(args.interval)


def _read_selection(base_url: str) -> dict:
    return parse_now_selection_xml(_read(base_url, "nowSelection"))


def _read_playing(base_url: str) -> dict:
    return parse_now_playing_xml(_read(base_url, "now_playing"))


def _read(base_url: str, path: str) -> str:
    try:
        with urlopen(f"{base_url}/{path}", timeout=2.0) as response:
            return response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        return f"<error><message>{exc}</message></error>"


if __name__ == "__main__":
    main()
