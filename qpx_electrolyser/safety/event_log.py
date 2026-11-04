"""Append-only, serial-number-free M16 safety event log."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
import re
from threading import Lock


SAFETY_EVENT_COLUMNS = (
    "timestamp_iso", "channel", "cause", "requested_action", "final_state",
)


class SafetyEventLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(
        self,
        *,
        channel: int,
        cause: str,
        requested_action: str,
        confirmed_off: bool,
    ) -> None:
        if channel not in (1, 2):
            raise ValueError("safety-event channel must be 1 or 2")
        row = {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "cause": self._clean(cause),
            "requested_action": self._clean(requested_action),
            "final_state": "OFF_CONFIRMED" if confirmed_off else "OFF_UNCONFIRMED",
        }
        with self._lock:
            new_file = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=SAFETY_EVENT_COLUMNS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()

    @staticmethod
    def _clean(value: str) -> str:
        cleaned = str(value).replace("\r", " ").replace("\n", " ").strip()
        return re.sub(
            r"(?i)(Aim-TTi\s*,\s*QPX600DP\s*,\s*)[^,]+",
            r"\1[REDACTED]",
            cleaned,
        )
