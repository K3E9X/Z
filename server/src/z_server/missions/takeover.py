"""Take-over (docs/04 §5 "Take over", docs/02 §2, ADR 0019): a person pauses a job and works in its workspace.

The workspace is rebuilt server-side from what Z already holds, never by reaching into the runner: the staged
inputs (the same staging the runner received, judge included) plus the files the agent added, changed or
deleted, taken from the job's last checkpoint. A read_write take-over records the person's edits in the
session; finishing folds them into the checkpoint and requeues the job, so a fresh runner restores the edited
workspace (pause and take-over use the checkpoint mechanism, docs/02). The runner that was parked is locked
out at once (its tokens are cleared) and the scheduler stops it before dispatching the new one. The agent is
told, as a guide message, which files a person changed.

One person at a time per job, for 30 minutes (renewed by every call); a take-over never consumes an attempt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import posixpath
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from z_common.checkpoint import DELTA_META, Checkpoint, CheckpointError, delta_files, merge_edits
from z_common.models.enums import JobStatus

from z_server.db.models import Job, JobMessage, Mission, Setting
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.missions import service, workspace
from z_server.state import AppState

TTL = timedelta(minutes=30)
PREFIX = "takeover:"
MAX_FILE = 1024 * 1024
MAX_EDITS = 8 * 1024 * 1024
MAX_LISTED = 5000
ACTIVE = {JobStatus.running.value, JobStatus.paused.value, JobStatus.needs_attention.value}


def key(job_id: str) -> str:
    return PREFIX + job_id


def clean_path(raw: str) -> str:
    """A workspace-relative path: no absolute path, no `..`, no NUL, never the delta metadata file."""
    if not raw or "\x00" in raw or raw.startswith(("/", "\\")) or (len(raw) > 1 and raw[1] == ":"):
        raise Problem("validation", "the path must be relative to the workspace")
    path = posixpath.normpath(raw.replace("\\", "/"))
    if path in {".", ""} or ".." in path.split("/") or path == DELTA_META or path.startswith(".z-"):
        raise Problem("validation", "the path must stay inside the workspace")
    return path


async def _session(db: AsyncSession, job: Job, user: str, *, lock: bool = False) -> tuple[Setting, dict[str, Any]]:
    row = await db.get(Setting, key(job.id), with_for_update=lock)
    if row is None:
        raise Problem("conflict", "this job is not being taken over: start a take-over first")
    value = dict(row.value)
    if datetime.fromisoformat(value["expires_at"]) < datetime.now(UTC):
        raise Problem("conflict", "the take-over expired: start it again")
    if value["user"] != user:
        raise Problem("forbidden", f"{value['user']} is taking this job over")
    return row, value


def _renew(row: Setting, value: dict[str, Any]) -> None:
    value["expires_at"] = (datetime.now(UTC) + TTL).isoformat()
    row.value = value


async def start(db: AsyncSession, mission: Mission, job: Job, user: str, mode: str) -> dict[str, Any]:
    if job.status not in ACTIVE:
        raise Problem("conflict", f"the job is {job.status}: only a running or paused job can be taken over")
    if mode == "read_write" and not job.checkpoint:
        raise Problem("conflict", "the job has not finished a step yet: take it over read-only, or wait")
    row = await db.get(Setting, key(job.id), with_for_update=True)
    now = datetime.now(UTC)
    if row is not None:
        current = dict(row.value)
        if current["user"] != user and datetime.fromisoformat(current["expires_at"]) > now:
            raise Problem("conflict", f"{current['user']} is already taking this job over")
        value = {**current, "user": user, "mode": mode} if current["user"] == user else None
    else:
        value = None
    value = value or {"user": user, "mode": mode, "edits": {}, "started_at": now.isoformat()}
    value["expires_at"] = (now + TTL).isoformat()
    if row is None:
        db.add(Setting(key=key(job.id), value=value))
    else:
        row.value = value
    if job.status == JobStatus.running.value:
        job.control_state, job.control_reason = "pause", f"taken over by {user}"
        await service.set_job_status(db, job, JobStatus.paused, f"taken over by {user}")
    return value


async def _staged(db: AsyncSession, state: AppState, mission: Mission, job: Job) -> dict[str, bytes]:
    """The staged inputs, cached per job for the session (staging may clone a repository)."""
    cache_key = f"takeover_staged:{job.id}"
    hit = state.cache.get(cache_key)
    if hit is not None and hit[0] > datetime.now(UTC):
        return dict(hit[1])
    staged = await workspace.stage(db, mission, job, state)
    files = {f.path: f.data for f in staged.delivered}
    state.cache[cache_key] = (datetime.now(UTC) + TTL, files)
    return files


def _agent_delta(job: Job) -> tuple[dict[str, bytes], list[str]]:
    if not job.checkpoint:
        return {}, []
    try:
        return delta_files(Checkpoint.unpack(job.checkpoint).workspace)
    except CheckpointError as exc:
        raise Problem("unavailable", f"the job checkpoint cannot be read: {exc}") from exc


def _edits(value: dict[str, Any]) -> dict[str, bytes | None]:
    return {path: (None if item is None else base64.b64decode(item["b64"])) for path, item in value["edits"].items()}


async def view(
    db: AsyncSession, state: AppState, mission: Mission, job: Job, user: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    row, value = await _session(db, job, user, lock=True)
    _renew(row, value)
    staged = await _staged(db, state, mission, job)
    agent, deleted = _agent_delta(job)
    edits = _edits(value)
    files: dict[str, dict[str, Any]] = {}
    for path, data in staged.items():
        files[path] = {"path": path, "size": len(data), "origin": "staged", "deleted": False}
    for path in deleted:
        if path in files:
            files[path] = {**files[path], "origin": "agent", "deleted": True}
    for path, data in agent.items():
        files[path] = {"path": path, "size": len(data), "origin": "agent", "deleted": False}
    for path, edit in edits.items():
        before = files.get(path, {"size": 0})
        files[path] = {
            "path": path,
            "size": before["size"] if edit is None else len(edit),
            "origin": "human",
            "deleted": edit is None,
        }
    ordered = sorted(files.values(), key=lambda f: f["path"])
    return value, {
        "job_id": job.id,
        "mode": value["mode"],
        "by": value["user"],
        "expires_at": value["expires_at"],
        "files": ordered[:MAX_LISTED],
        "truncated": len(ordered) > MAX_LISTED,
    }


async def read(
    db: AsyncSession, state: AppState, mission: Mission, job: Job, user: str, raw_path: str
) -> dict[str, Any]:
    path = clean_path(raw_path)
    row, value = await _session(db, job, user, lock=True)
    _renew(row, value)
    edits = _edits(value)
    agent, deleted = _agent_delta(job)
    if path in edits:
        data, origin = edits[path], "human"
    elif path in agent:
        data, origin = agent[path], "agent"
    elif path in deleted:
        data, origin = None, "agent"
    else:
        data, origin = (await _staged(db, state, mission, job)).get(path), "staged"
    if data is None:
        raise Problem("not-found", f"{path} is not in the workspace")
    if len(data) > MAX_FILE:
        raise Problem("validation", f"{path} is larger than 1 MiB: download the workspace from the Files tab")
    try:
        return {"path": path, "encoding": "utf-8", "content": data.decode("utf-8"), "size": len(data), "origin": origin}
    except UnicodeDecodeError:
        content = base64.b64encode(data).decode()
        return {"path": path, "encoding": "base64", "content": content, "size": len(data), "origin": origin}


async def write(db: AsyncSession, job: Job, user: str, raw_path: str, content: str | None, encoding: str) -> int:
    """Record one edit (None deletes). Returns the file size."""
    path = clean_path(raw_path)
    row, value = await _session(db, job, user, lock=True)
    if value["mode"] != "read_write":
        raise Problem("forbidden", "this take-over is read-only: start it again in read_write mode to edit")
    data: bytes | None = None
    if content is not None:
        try:
            data = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode("utf-8")
        except (binascii.Error, ValueError) as exc:
            raise Problem("validation", "the content is not valid base64") from exc
        if len(data) > MAX_FILE:
            raise Problem("validation", "a file edited in a take-over is at most 1 MiB")
    edits = dict(value["edits"])
    edits[path] = None if data is None else {"b64": base64.b64encode(data).decode(), "sha256": _sha(data)}
    total = sum(len(base64.b64decode(e["b64"])) for e in edits.values() if e is not None)
    if total > MAX_EDITS:
        raise Problem("validation", "the edits of one take-over are at most 8 MiB in total")
    value["edits"] = edits
    _renew(row, value)
    return 0 if data is None else len(data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def finish(
    db: AsyncSession, state: AppState, mission: Mission, job: Job, user: str, resume: bool
) -> dict[str, Any]:
    row, value = await _session(db, job, user, lock=True)
    edits = _edits(value)
    await db.delete(row)
    state.cache.pop(f"takeover_staged:{job.id}", None)
    restarted = False
    if edits:
        if not job.checkpoint:
            raise Problem("conflict", "the job has no checkpoint to fold the edits into")
        if JobStatus(job.status).terminal:
            raise Problem("conflict", f"the job ended ({job.status}) during the take-over: the edits were not applied")
        checkpoint = Checkpoint.unpack(job.checkpoint)
        checkpoint.workspace = merge_edits(checkpoint.workspace, edits)
        job.checkpoint = checkpoint.pack()
        changed = sorted(p for p, d in edits.items() if d is not None)
        removed = sorted(p for p, d in edits.items() if d is None)
        note = [f"[Take-over] {user} worked in your workspace while you were paused."]
        if changed:
            note.append("Changed: " + ", ".join(changed[:50]) + ".")
        if removed:
            note.append("Deleted: " + ", ".join(removed[:50]) + ".")
        note.append("Read those files again before you continue; keep their changes unless the task says otherwise.")
        db.add(JobMessage(id=new_id("jmsg"), job_id=job.id, text=" ".join(note), by=user))
        # restart from the edited checkpoint: the parked runner is locked out now and stopped by the scheduler
        job.session_token_hash = job.bootstrap_token_hash = None
        job.lease_owner = job.lease_expires_at = None
        job.resume_from_step = job.checkpoint_step
        job.available_at = service.utcnow()
        job.control_state, job.control_reason = ("run", None) if resume else ("pause", f"taken over by {user}")
        await service.set_job_status(db, job, JobStatus.queued, f"restart after the take-over by {user}")
        restarted = True
    elif resume and job.status == JobStatus.paused.value and job.control_reason == f"taken over by {user}":
        job.control_state, job.control_reason = "run", None
        await service.set_job_status(db, job, JobStatus.running, "take-over ended")
    if resume:
        await service.advance(db, state, mission)
    return {
        "job_id": job.id,
        "edited": sorted(edits),
        "restarted": restarted,
        "resumed": resume and (restarted or job.status == JobStatus.running.value),
    }
