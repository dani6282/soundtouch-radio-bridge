from __future__ import annotations

import argparse
import datetime as dt
import sys
import threading

from websocket import WebSocketApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture raw Bose SoundTouch websocket events.")
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--seconds", type=float, default=90.0)
    args = parser.parse_args()

    url = f"ws://{args.host}:{args.port}/"

    def stamp() -> str:
        return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    def on_open(ws: WebSocketApp) -> None:
        print(f"{stamp()} websocket open {url}", flush=True)

    def on_message(ws: WebSocketApp, message: str | bytes) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        print(f"{stamp()} message {message}", flush=True)

    def on_error(ws: WebSocketApp, error: object) -> None:
        print(f"{stamp()} error {error}", file=sys.stderr, flush=True)

    def on_close(ws: WebSocketApp, code: int | None, reason: str | None) -> None:
        print(f"{stamp()} websocket close code={code} reason={reason}", flush=True)

    ws = WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        subprotocols=["gabbo"],
    )
    timer = threading.Timer(args.seconds, ws.close)
    timer.daemon = True
    timer.start()
    try:
        ws.run_forever(ping_interval=0)
    finally:
        timer.cancel()


if __name__ == "__main__":
    main()
