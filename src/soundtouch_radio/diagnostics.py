from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import threading


@dataclass
class DiagnosticRecorder:
    path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "path": str(self.path) if self.path is not None else None,
            "last_error": self.last_error,
        }

    def record(self, kind: str, **payload: Any) -> None:
        if self.path is None:
            return
        entry = {"at": _now_iso(), "kind": kind, **payload}
        try:
            line = json.dumps(entry, sort_keys=True, default=str)
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                self._last_error = None
        except (OSError, TypeError, ValueError) as exc:
            with self._lock:
                self._last_error = str(exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
