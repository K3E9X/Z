"""Artifact storage on the artifacts volume (docs/02 §3). Paths are derived from ids, never from names."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import Artifact
from z_server.ids import new_id

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ArtifactTooLarge(ValueError):
    pass


def safe_name(name: str) -> str:
    base = SAFE_NAME.sub("_", Path(name).name).strip("._") or "file"
    return base[:200]


@dataclass
class Stored:
    path: Path
    size: int
    sha256: str


def store_bytes(root: Path, mission_id: str, artifact_id: str, data: bytes) -> Stored:
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ArtifactTooLarge(f"artifact larger than {MAX_ARTIFACT_BYTES} bytes")
    folder = root / mission_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / artifact_id
    path.write_bytes(data)
    return Stored(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest())


async def create(
    db: AsyncSession,
    root: Path,
    *,
    mission_id: str,
    job_id: str | None,
    kind: str,
    name: str,
    data: bytes,
    data_class: str,
    content_type: str | None = None,
) -> Artifact:
    artifact_id = new_id("art")
    stored = store_bytes(root, mission_id, artifact_id, data)
    clean = safe_name(name)
    row = Artifact(
        id=artifact_id,
        mission_id=mission_id,
        job_id=job_id,
        kind=kind,
        name=clean,
        content_type=content_type or mimetypes.guess_type(clean)[0] or "application/octet-stream",
        size=stored.size,
        sha256=stored.sha256,
        data_class=data_class,
        storage_path=str(stored.path),
    )
    db.add(row)
    await db.flush()
    return row
