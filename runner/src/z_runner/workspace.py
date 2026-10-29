"""Workspace: the jail every file tool goes through, safe archive extraction, manifest checks and the
workspace delta stored in checkpoints (docs/04 §1 step 2, §5 "Workspace jail").
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from z_common.models.api_internal import WorkspaceManifest
from z_common.models.enums import JudgeVerdict

# Runner-private files inside the workspace (skill resources). Never part of the job's output.
PRIVATE_DIR = ".z"
DELTA_META = ".z-delta.json"


class JailError(PermissionError):
    """A path resolves outside the workspace."""


class ArchiveError(ValueError):
    """A workspace archive is unsafe or over the limits."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_total_bytes: int = 2 * 1024**3
    max_member_bytes: int = 512 * 1024**2
    max_members: int = 100_000


class Workspace:
    """The job's workspace directory. All tool paths are resolved here."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)

    def resolve(self, path: str, *, for_write: bool = False) -> Path:
        """Resolve a tool-supplied path (relative to the workspace, or absolute inside it).

        Symlinks are followed: a link that points outside the workspace is refused, for reads and writes.
        """
        if not path or "\x00" in path:
            raise JailError("empty or invalid path")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        # resolve() follows every symlink that exists; non-existing tails are kept lexically (normalized).
        resolved = Path(os.path.realpath(candidate))
        if not resolved.is_relative_to(self.root):
            raise JailError(f"{path!r} is outside the workspace")
        if for_write and resolved == self.root:
            raise JailError("cannot write the workspace directory itself")
        return resolved

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    # ------------------------------------------------------------------ snapshots for checkpoint deltas
    def snapshot(self) -> dict[str, str]:
        """{relative path: sha256} of regular files (symlinks as `link:<target>`), private dir excluded."""
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            rel_dir = Path(dirpath).relative_to(self.root)
            if rel_dir.parts[:1] == (PRIVATE_DIR,):
                dirnames[:] = []
                continue
            for name in list(dirnames):
                full = Path(dirpath) / name
                if full.is_symlink():
                    out[(rel_dir / name).as_posix()] = "link:" + os.readlink(full)
                    dirnames.remove(name)
            for name in filenames:
                full = Path(dirpath) / name
                rel = (rel_dir / name).as_posix()
                if full.is_symlink():
                    out[rel] = "link:" + os.readlink(full)
                elif full.is_file():
                    out[rel] = sha256_file(full)
        return out

    def delta(self, baseline: dict[str, str]) -> tuple[bytes, dict[str, str]]:
        """tar.gz of files added or changed since `baseline` plus the list of deleted files."""
        current = self.snapshot()
        changed = sorted(p for p, digest in current.items() if baseline.get(p) != digest)
        deleted = sorted(p for p in baseline if p not in current)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel in changed:
                tar.add(self.root / rel, arcname=rel, recursive=False)
            meta = json.dumps({"deleted": deleted}).encode()
            info = tarfile.TarInfo(DELTA_META)
            info.size = len(meta)
            tar.addfile(info, io.BytesIO(meta))
        return buf.getvalue(), current

    def apply_delta(self, blob: bytes, limits: ArchiveLimits | None = None) -> None:
        """Apply a delta of `delta()` on the staged tree. Deletions act on the path itself (never on what a link
        points to), and only below a parent directory that resolves inside the workspace."""
        deleted: list[str] = []
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            try:
                meta = tar.extractfile(DELTA_META)
            except KeyError:
                meta = None
            if meta is not None:
                data = json.loads(meta.read())
                deleted = [str(p) for p in data.get("deleted", [])]
            # A file that replaced a staged link is written as itself, not through the link.
            replaced = [m.name for m in tar if m.name != DELTA_META and not m.isdir()]
        for rel in [*deleted, *replaced]:
            target = self._leaf(rel)
            if target.is_symlink() or target.is_file():
                target.unlink()
        extract_tar(io.BytesIO(blob), self.root, limits or ArchiveLimits(), skip={DELTA_META})

    def _leaf(self, rel: str) -> Path:
        """`rel` inside the workspace with its parent directory resolved (links followed) but not its last
        component, so that a link is handled as itself. The private dir and paths escaping the workspace are
        refused."""
        name = _safe_member_name(rel)
        if PurePosixPath(name).parts[0] == PRIVATE_DIR:
            raise ArchiveError(f"the workspace delta touches the private directory: {rel!r}")
        parent = Path(os.path.realpath(self.root / posixpath.dirname(name)))
        if not parent.is_relative_to(self.root):
            raise ArchiveError(f"the workspace delta reaches outside the workspace: {rel!r}")
        return parent / posixpath.basename(name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(name: str) -> str:
    if not name or "\x00" in name:
        raise ArchiveError("empty member name")
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        raise ArchiveError(f"absolute path in archive: {name!r}")
    normalized = posixpath.normpath(name.replace("\\", "/"))
    parts = PurePosixPath(normalized).parts
    if normalized in (".", "") or ".." in parts:
        raise ArchiveError(f"path traversal in archive: {name!r}")
    return normalized


def _check_link(member: tarfile.TarInfo, name: str) -> None:
    target = member.linkname
    if not target or "\x00" in target:
        raise ArchiveError(f"empty link target: {member.name!r}")
    if target.startswith(("/", "\\")):
        raise ArchiveError(f"link to an absolute path: {member.name!r} -> {target!r}")
    if member.issym():
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    else:  # hard links are relative to the archive root
        resolved = posixpath.normpath(target)
    if resolved == ".." or resolved.startswith("../"):
        raise ArchiveError(f"link escapes the workspace: {member.name!r} -> {target!r}")


def extract_tar(
    fileobj: io.BufferedIOBase | io.BytesIO,
    dest: Path,
    limits: ArchiveLimits,
    *,
    skip: set[str] | None = None,
) -> list[str]:
    """Extract a tar.gz into `dest`, refusing anything unsafe *before* writing it.

    Refused: absolute paths, `..`, symlinks and hard links pointing outside, device files and FIFOs,
    members over the size limits. Python's `data` extraction filter is applied as well (second line of defense).
    """
    dest = dest.resolve(strict=True)
    names: list[str] = []
    total = 0
    with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
        for count, member in enumerate(tar, start=1):
            if count > limits.max_members:
                raise ArchiveError(f"more than {limits.max_members} members")
            name = _safe_member_name(member.name)
            if skip and name in skip:
                continue
            if (
                member.isdev()
                or member.isfifo()
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
            ):
                raise ArchiveError(f"special file in archive: {member.name!r}")
            if member.issym() or member.islnk():
                _check_link(member, name)
            if member.size > limits.max_member_bytes:
                raise ArchiveError(f"member over {limits.max_member_bytes} bytes: {member.name!r}")
            total += member.size
            if total > limits.max_total_bytes:
                raise ArchiveError(f"archive expands to more than {limits.max_total_bytes} bytes")
            # Refuse writing through a symlink extracted earlier (e.g. `a -> sub`, then `a/../../x` is already
            # refused above, but `a -> sub` then `a/file` must stay inside too).
            parent = Path(os.path.realpath(dest / name)).parent
            if not parent.is_relative_to(dest):
                raise ArchiveError(f"member would be written outside the workspace: {member.name!r}")
            member.name = name
            if member.isfile() or member.isdir():
                member.mode = (member.mode or 0o644) & 0o755 | (0o700 if member.isdir() else 0o600)
            tar.extract(member, dest, filter="data")
            names.append(name)
    return names


@dataclass
class StagedInputs:
    files: list[str]
    flagged: list[str]
    blocked: list[str]


def check_manifest(workspace: Workspace, manifest: WorkspaceManifest) -> StagedInputs:
    """Verify the staged files against the manifest (size and sha256) and remove `blocked` ones.

    Raises ArchiveError when a listed file is missing or differs (the archive is not what the judge scanned).
    """
    blocked: list[str] = []
    for entry in manifest.files:
        target = workspace.resolve(entry.path)
        if entry.judge == JudgeVerdict.blocked:
            if target.is_file() and not target.is_symlink():
                target.unlink()
            blocked.append(entry.path)
            continue
        if not target.is_file() or target.is_symlink():
            raise ArchiveError(f"manifest file missing from the archive: {entry.path!r}")
        st = target.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_size != entry.size or sha256_file(target) != entry.sha256:
            raise ArchiveError(f"manifest mismatch for {entry.path!r}")
    flagged = sorted({p for p in manifest.flagged if p not in blocked})
    return StagedInputs(
        files=[f.path for f in manifest.files if f.path not in blocked], flagged=flagged, blocked=blocked
    )
