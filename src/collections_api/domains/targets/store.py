"""File store for the extracted targets document."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

LATEST = "latest.json"
_PERIOD = re.compile(r"^\dQ\d{2}$")


class TargetsStore:
    """Keeps ``latest.json`` and one ``targets_<period>.json`` snapshot per period.

    Writes go to a temporary file in the same directory and are moved into place with
    ``os.replace``, so a reader never sees a half-written document, even if the process
    dies mid-write or two uploads overlap.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    @property
    def directory(self) -> Path:
        return self._dir

    def latest(self) -> dict[str, Any] | None:
        path = self._dir / LATEST
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, document: dict[str, Any]) -> str:
        """Store ``document`` as the latest and, if new, as its period snapshot."""
        period = document["meta"]["period"]
        if not _PERIOD.match(period):
            raise ValueError(f"unexpected period label: {period!r}")
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        self._write_atomic(self._dir / LATEST, payload)
        snapshot = self._dir / f"targets_{period}.json"
        if not snapshot.exists():
            self._write_atomic(snapshot, payload)
        return period

    def scratch_path(self, suffix: str = ".pdf") -> Path:
        """A unique path inside the store for an upload being processed."""
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"_upload_{uuid.uuid4().hex}{suffix}"

    def _write_atomic(self, target: Path, payload: str) -> None:
        temporary = target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
