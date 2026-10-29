"""Checkpoint blob (`CheckpointIn.blob_b64`, `JobSpec.checkpoint_b64`).

A tar.gz with three members:

* `backend.tar.gz`: the agent harness state (OpenHands conversation persistence directory);
* `runner.json`: runner bookkeeping (steps, tokens, repairs, loop detector, handled control ids, model);
* `workspace.tar.gz`: files added or changed since the workspace was staged, plus the deleted ones, so a new
  pod (fresh `emptyDir`) restores the workspace the agent was working on.

Shared by the runner (writes and restores it) and z-server (take-over reads the workspace from it and folds
human edits into it before the job restarts from it).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import posixpath
import tarfile
from dataclasses import dataclass
from typing import Any

FORMAT_VERSION = 1
MAX_MEMBER = 512 * 1024**2


class CheckpointError(ValueError):
    pass


@dataclass
class Checkpoint:
    backend: bytes
    runner: dict[str, Any]
    workspace: bytes

    def pack(self) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            members = {
                "format.json": json.dumps({"version": FORMAT_VERSION}).encode(),
                "runner.json": json.dumps(self.runner, sort_keys=True).encode(),
                "backend.tar.gz": self.backend,
                "workspace.tar.gz": self.workspace,
            }
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def encode(self) -> str:
        return base64.b64encode(self.pack()).decode("ascii")

    @classmethod
    def unpack(cls, blob: bytes) -> Checkpoint:
        found: dict[str, bytes] = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                for member in tar:
                    if not member.isfile() or member.name not in (
                        "format.json",
                        "runner.json",
                        "backend.tar.gz",
                        "workspace.tar.gz",
                    ):
                        raise CheckpointError(f"unexpected checkpoint member {member.name!r}")
                    if member.size > MAX_MEMBER:
                        raise CheckpointError(f"checkpoint member too large: {member.name}")
                    fh = tar.extractfile(member)
                    if fh is None:
                        raise CheckpointError(f"unreadable checkpoint member {member.name!r}")
                    found[member.name] = fh.read()
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise CheckpointError(f"not a checkpoint: {exc}") from exc
        if set(found) != {"format.json", "runner.json", "backend.tar.gz", "workspace.tar.gz"}:
            raise CheckpointError("incomplete checkpoint")
        version = json.loads(found["format.json"]).get("version")
        if version != FORMAT_VERSION:
            raise CheckpointError(f"unsupported checkpoint version {version!r}")
        runner = json.loads(found["runner.json"])
        if not isinstance(runner, dict):
            raise CheckpointError("runner state is not an object")
        return cls(backend=found["backend.tar.gz"], runner=runner, workspace=found["workspace.tar.gz"])

    @classmethod
    def decode(cls, value: str) -> Checkpoint:
        try:
            blob = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CheckpointError(f"checkpoint is not base64: {exc}") from exc
        return cls.unpack(blob)


DELTA_META = ".z-delta.json"


def delta_files(workspace: bytes) -> tuple[dict[str, bytes], list[str]]:
    """Regular files of a workspace delta and its deleted paths (links and other members are skipped)."""
    files: dict[str, bytes] = {}
    deleted: list[str] = []
    if not workspace:
        return files, deleted
    try:
        with tarfile.open(fileobj=io.BytesIO(workspace), mode="r:gz") as tar:
            for member in tar:
                if member.name == DELTA_META:
                    fh = tar.extractfile(member)
                    deleted = list(json.loads(fh.read()).get("deleted", [])) if fh else []
                elif member.isfile() and member.size <= MAX_MEMBER:
                    fh = tar.extractfile(member)
                    if fh is not None:
                        files[posixpath.normpath(member.name)] = fh.read()
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise CheckpointError(f"unreadable workspace delta: {exc}") from exc
    return files, deleted


def merge_edits(workspace: bytes, edits: dict[str, bytes | None]) -> bytes:
    """A workspace delta with `edits` applied on top: bytes replace or add a file, None deletes it."""
    files, deleted = delta_files(workspace)
    gone = set(deleted)
    for path, data in edits.items():
        if data is None:
            files.pop(path, None)
            gone.add(path)
        else:
            files[path] = data
            gone.discard(path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(files):
            info = tarfile.TarInfo(path)
            info.size = len(files[path])
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(files[path]))
        meta = json.dumps({"deleted": sorted(gone)}).encode()
        info = tarfile.TarInfo(DELTA_META)
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    return buf.getvalue()
