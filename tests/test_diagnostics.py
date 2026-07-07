from __future__ import annotations

from pathlib import Path
import json

from soundtouch_radio.diagnostics import DiagnosticRecorder


def test_diagnostic_recorder_appends_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "soundtouch.jsonl"
    recorder = DiagnosticRecorder(path)

    recorder.record(
        "websocket_message",
        event_tags=["nowSelectionUpdated"],
        raw_message="<updates />",
    )

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["kind"] == "websocket_message"
    assert entries[0]["event_tags"] == ["nowSelectionUpdated"]
    assert entries[0]["raw_message"] == "<updates />"
    assert recorder.snapshot()["enabled"] is True
    assert recorder.snapshot()["path"] == str(path)
    assert recorder.snapshot()["last_error"] is None


def test_disabled_diagnostic_recorder_is_noop(tmp_path: Path) -> None:
    recorder = DiagnosticRecorder()

    recorder.record("bridge_result", result={"triggered": False})

    assert list(tmp_path.iterdir()) == []
    assert recorder.snapshot() == {"enabled": False, "path": None, "last_error": None}
