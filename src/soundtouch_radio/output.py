from __future__ import annotations

from typing import Any
import json
import sys


def emit(data: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    _emit_human(data)


def fail(message: str, *, as_json: bool, code: int = 1) -> None:
    if as_json:
        print(
            json.dumps({"ok": False, "error": message}, indent=2, sort_keys=True), file=sys.stderr
        )
    else:
        print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _emit_human(data: Any) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, indent=2, sort_keys=True)}")
            else:
                print(f"{key}: {value}")
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                print(" - " + ", ".join(f"{key}={value}" for key, value in item.items()))
            else:
                print(f" - {item}")
        return
    print(data)
