"""Artifact uploads (`POST /internal/jobs/{id}/artifacts`), redacted before they leave the runner."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import threading

from z_common.models.enums import ArtifactKind

from z_runner.client import ApiError, InternalClient
from z_runner.events import EventSink
from z_runner.redaction import Redactor

log = logging.getLogger("z_runner.artifacts")

TEXT_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".csv": "text/csv",
    ".patch": "text/x-diff",
    ".diff": "text/x-diff",
    ".mmd": "text/vnd.mermaid",
    ".svg": "image/svg+xml",
    ".log": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".html": "text/html",
}


class ArtifactRefused(Exception):
    """The artifact contains a known secret that cannot be redacted (binary content)."""


def content_type_for(name: str) -> str:
    for ext, ctype in TEXT_TYPES.items():
        if name.lower().endswith(ext):
            return ctype
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _is_text(content_type: str) -> bool:
    return content_type.startswith("text/") or content_type in (
        "application/json",
        "application/yaml",
        "image/svg+xml",
    )


class ArtifactStore:
    def __init__(self, client: InternalClient, sink: EventSink, redactor: Redactor) -> None:
        self._client = client
        self._sink = sink
        self._redactor = redactor
        self._lock = threading.Lock()
        self._uploaded: dict[tuple[str, str], str] = {}

    def upload(self, kind: ArtifactKind, name: str, content: bytes, content_type: str | None = None) -> str:
        """Upload (once per name + content) and emit the `artifact` event. Returns the artifact id."""
        ctype = content_type or content_type_for(name)
        if _is_text(ctype):
            try:
                content = self._redactor.text(content.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                ctype = "application/octet-stream"
        if not _is_text(ctype) and self._redactor.contains_known_secret(content):
            raise ArtifactRefused(f"{name} contains a secret and is binary; it was not uploaded")
        key = (name, hashlib.sha256(content).hexdigest())
        with self._lock:
            if key in self._uploaded:
                return self._uploaded[key]
        view = self._client.post_artifact(kind.value, name, content, ctype)
        with self._lock:
            self._uploaded[key] = view.id
        self._sink.emit("artifact", {"artifact_id": view.id, "kind": kind.value, "name": name})
        return view.id

    def try_upload(self, kind: ArtifactKind, name: str, content: bytes, content_type: str | None = None) -> str | None:
        try:
            return self.upload(kind, name, content, content_type)
        except (ApiError, ArtifactRefused) as exc:
            log.warning("artifact %s not uploaded: %s", name, exc)
            return None
