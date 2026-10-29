"""Workspace staging for a job (docs/04 §1 step 2): mode `files` = uploaded mission inputs plus the artifacts
of the steps this step depends on. Every staged file goes through the judge; blocked files are withheld.

Layout in /workspace: uploaded inputs at `<name>`, earlier step artifacts at `steps/<step_id>/<name>`.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models import api_internal as m
from z_common.models.enums import JobStatus, JudgeVerdict, WorkspaceMode
from z_common.models.manifests import MissionPlan

from z_server import judge
from z_server.db.models import Artifact, Connector, Job, Mission
from z_server.errors import Problem
from z_server.gateway import git
from z_server.gateway import http as http_invoker
from z_server.gateway.auth import AuthError, material
from z_server.state import AppState

MAX_WORKSPACE_BYTES = 512 * 1024 * 1024
RUNNER_UID = 10001


@dataclass
class StagedFile:
    path: str
    data: bytes
    sha256: str
    verdict: judge.Verdict


@dataclass
class Staged:
    files: list[StagedFile] = field(default_factory=list)
    repo: str | None = None
    commit: str | None = None

    @property
    def delivered(self) -> list[StagedFile]:
        return [f for f in self.files if f.verdict.verdict != JudgeVerdict.blocked]

    @property
    def blocked(self) -> list[StagedFile]:
        return [f for f in self.files if f.verdict.verdict == JudgeVerdict.blocked]

    def manifest(self) -> m.WorkspaceManifest:
        return m.WorkspaceManifest(
            files=[
                m.WorkspaceFile(path=f.path, size=len(f.data), sha256=f.sha256, judge=f.verdict.verdict)
                for f in self.files
            ],
            flagged=[f.path for f in self.files if f.verdict.verdict != JudgeVerdict.clean],
            repo=self.repo,
            commit=self.commit,
        )

    def archive(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for f in self.delivered:
                info = tarfile.TarInfo(f.path)
                info.size, info.mode, info.uid, info.gid, info.mtime = len(f.data), 0o600, RUNNER_UID, RUNNER_UID, 0
                tar.addfile(info, io.BytesIO(f.data))
        return buffer.getvalue()


def _unique(path: str, taken: set[str]) -> str:
    candidate, n = path, 1
    while candidate in taken:
        stem, dot, ext = path.rpartition(".")
        candidate = f"{stem}-{n}.{ext}" if dot and stem else f"{path}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


MAX_REPO_FILES = 20_000
SKIP_DIRS = {".git"}


async def _clone(db: AsyncSession, state: AppState, job: Job) -> Staged:
    """git_clone: the repository is cloned server-side with the connector's read token; the runner receives a
    plain tree (no .git, no token). `repo` is `group/project`, optionally `connector:group/project@ref`."""
    raw = str((job.input or {}).get("repo") or "")
    if not raw:
        raise Problem("conflict", "this step has workspace git_clone but no repo")
    connector_name, _, rest = raw.rpartition(":") if ":" in raw and not raw.startswith("http") else ("gitlab", "", raw)
    repo, _, ref = rest.partition("@")
    connector = (
        await db.execute(select(Connector).where(Connector.name == (connector_name or "gitlab")))
    ).scalar_one_or_none()
    if connector is None or not connector.enabled or connector.review_status != "approved":
        raise Problem("conflict", f"connector {connector_name or 'gitlab'} is not approved and enabled for cloning")
    try:
        auth = await material(connector.manifest, "read", state.gateway.resolver)
        base = http_invoker.base_url(connector.base_url, connector.manifest)
        checkout = await git.clone(base, repo, ref or None, auth.secrets[0] if auth.secrets else None)
    except (AuthError, git.GitError, http_invoker.InvokeError) as exc:
        raise Problem("unavailable", f"could not clone {repo}: {exc}") from exc
    staged, total = Staged(repo=f"{connector.name}:{repo}", commit=checkout.commit), 0
    try:
        for path in sorted(checkout.path.rglob("*")):
            rel = path.relative_to(checkout.path)
            if rel.parts[0] in SKIP_DIRS or path.is_symlink() or not path.is_file():
                continue
            if len(staged.files) >= MAX_REPO_FILES:
                raise Problem("validation", f"repository has more than {MAX_REPO_FILES} files", status=413)
            data = path.read_bytes()
            total += len(data)
            if total > MAX_WORKSPACE_BYTES:
                raise Problem("validation", f"workspace larger than {MAX_WORKSPACE_BYTES} bytes", status=413)
            staged.files.append(
                StagedFile(rel.as_posix(), data, hashlib.sha256(data).hexdigest(), judge.scan_bytes(data))
            )
    finally:
        shutil.rmtree(checkout.path.parent, ignore_errors=True)
    return staged


async def stage(db: AsyncSession, mission: Mission, job: Job, state: AppState | None = None) -> Staged:
    step = next((s for s in MissionPlan.model_validate(mission.plan).steps if s.id == job.step_id), None)
    mode = step.workspace if step else WorkspaceMode.none
    if mode == WorkspaceMode.none:
        raise Problem("conflict", "this step has no workspace")
    if mode == WorkspaceMode.git_clone:
        if state is None or state.gateway is None:
            raise Problem("unavailable", "the clone helper needs the tool gateway")
        return await _clone(db, state, job)
    sources: list[tuple[str, Artifact]] = []
    inputs = (
        (
            await db.execute(
                select(Artifact)
                .where(Artifact.mission_id == mission.id, Artifact.job_id.is_(None))
                .order_by(Artifact.created_at)
            )
        )
        .scalars()
        .all()
    )
    sources += [(a.name, a) for a in inputs]
    depends = list(step.depends_on) if step else []
    if depends:
        rows = (
            await db.execute(
                select(Artifact, Job.step_id)
                .join(Job, Job.id == Artifact.job_id)
                .where(
                    Artifact.mission_id == mission.id, Job.step_id.in_(depends), Job.status == JobStatus.succeeded.value
                )
                .order_by(Job.step_id, Artifact.created_at)
            )
        ).all()
        sources += [(f"steps/{step_id}/{a.name}", a) for a, step_id in rows]
    staged, taken, total = Staged(), set[str](), 0
    for path, artifact in sources:
        data = Path(artifact.storage_path).read_bytes()
        total += len(data)
        if total > MAX_WORKSPACE_BYTES:
            raise Problem("validation", f"workspace larger than {MAX_WORKSPACE_BYTES} bytes", status=413)
        staged.files.append(
            StagedFile(_unique(path, taken), data, artifact.sha256, judge.scan_bytes(data, artifact.content_type))
        )
    return staged
