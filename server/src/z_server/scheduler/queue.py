"""Claim, dispatch and supervise jobs (docs/02 §2 "Durable execution", ADR 0004).

- Claim: queued jobs whose `available_at` has passed, of active missions, under three limits: global
  (`executor.max_concurrency`), per mission (`executor.default_concurrency`) and per fan-out step
  (`fanout.concurrency`). `FOR UPDATE SKIP LOCKED` lets several schedulers run.
- Dispatch: a single-use bootstrap token is issued, the job goes `running` with a lease, then the executor
  starts the runner (outside the transaction).
- Lease: every runner call extends it. An expired lease means the runner died: the job is re-queued with
  attempt+1 and `resume_from_step` = its last checkpoint (backoff), or failed after `max_attempts`.
- Deadlines: a running job past `deadline_at` ends `timed_out`; a mission past its deadline too.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from z_common.models.enums import JobStatus, MissionStatus
from z_common.redaction import Redactor

from z_server.crypto import new_token, token_hash
from z_server.db.models import Job, Mission
from z_server.executors import Executor, ExecutorError
from z_server.missions import service
from z_server.state import AppState

log = logging.getLogger("z.scheduler")
LEASE = timedelta(seconds=120)
ACTIVE_MISSIONS = (MissionStatus.queued.value, MissionStatus.running.value, MissionStatus.waiting.value)
BACKOFF_S = (5, 30, 120)


@dataclass
class TickReport:
    dispatched: list[str] = field(default_factory=list)
    requeued: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reaped: list[str] = field(default_factory=list)


def runner_env(state: AppState, job: Job, bootstrap: str) -> dict[str, str]:
    # the contract's server is /api/v1: internal paths (/internal/jobs/...) are relative to it
    return {"Z_SERVER_URL": f"{state.settings.internal_url}/api/v1", "Z_JOB_ID": job.id, "Z_JOB_TOKEN": bootstrap}


def _fanout_limits(mission: Mission) -> dict[str, int]:
    limits = {}
    for step in (mission.plan or {}).get("steps", []):
        fanout = step.get("fanout")
        if isinstance(fanout, dict):
            limits[step["id"]] = int(fanout.get("concurrency", 8))
    return limits


async def claim(state: AppState, worker: str) -> list[tuple[str, dict[str, str]]]:
    config = state.config.get("executor", {})
    global_cap = int(config.get("max_concurrency", 32))
    mission_cap = int(config.get("default_concurrency", 8))
    out: list[tuple[str, dict[str, str]]] = []
    async with state.sessions() as db, db.begin():
        running = (
            await db.execute(select(Job.mission_id, Job.step_id).where(Job.status == JobStatus.running.value))
        ).all()
        per_mission = Counter(m for m, _ in running)
        per_step = Counter((m, s) for m, s in running)
        free = global_cap - len(running)
        if free <= 0:
            return out
        now = service.utcnow()
        candidates = (
            await db.execute(
                select(Job, Mission)
                .join(Mission, Mission.id == Job.mission_id)
                .where(
                    Job.status == JobStatus.queued.value, Job.available_at <= now, Mission.status.in_(ACTIVE_MISSIONS)
                )
                .order_by(Job.available_at, Job.created_at)
                .limit(max(free * 4, 16))
                .with_for_update(of=Job, skip_locked=True)
            )
        ).all()
        for job, mission in candidates:
            if free <= 0:
                break
            step_cap = _fanout_limits(mission).get(job.step_id, mission_cap)
            if per_mission[mission.id] >= mission_cap or per_step[(mission.id, job.step_id)] >= step_cap:
                continue
            bootstrap = new_token("zj")
            job.bootstrap_token_hash = token_hash(bootstrap)
            job.session_token_hash = None
            job.lease_owner, job.lease_expires_at = worker, now + LEASE
            job.started_at = job.started_at or now
            job.control_state = "run"
            await service.set_job_status(db, job, JobStatus.running)
            per_mission[mission.id] += 1
            per_step[(mission.id, job.step_id)] += 1
            free -= 1
            out.append((job.id, runner_env(state, job, bootstrap)))
            await service.advance(db, state, mission)
    return out


async def dispatch(state: AppState, executor: Executor, worker: str) -> list[str]:
    started = []
    for job_id, env in await claim(state, worker):
        try:
            ref = await executor.launch(job_id, env)
        except ExecutorError as exc:
            log.error("executor could not start job %s: %s", job_id, exc)
            await requeue_or_fail(state, job_id, f"executor: {exc}")
            continue
        async with state.sessions() as db, db.begin():
            job = await db.get(Job, job_id)
            if job is not None:
                job.executor_ref = ref
        started.append(job_id)
    return started


LOG_TAIL_CHARS = 4000


async def requeue_or_fail(state: AppState, job_id: str, reason: str, log_tail: str = "") -> str:
    """`log_tail` (the runner's last output, redacted here) is kept in the job error for diagnosis."""
    async with state.sessions() as db, db.begin():
        job = await db.get(Job, job_id, with_for_update=True)
        if job is None or JobStatus(job.status).terminal:
            return "skipped"
        mission = await db.get(Mission, job.mission_id)
        if log_tail:
            known = [service.mission_key(state, mission)] if mission is not None and mission.llm_key_enc else []
            tail = Redactor(known).text(log_tail)[-LOG_TAIL_CHARS:]
            job.error = {"class": "runner_lost", "message": f"{reason}\n--- last runner output ---\n{tail}"}
        if job.attempt >= job.max_attempts:
            job.error = job.error if log_tail else {"class": "runner_lost", "message": reason}
            await service.set_job_status(db, job, JobStatus.failed, reason)
            outcome = "failed"
        else:
            job.attempt += 1
            job.resume_from_step = job.checkpoint_step
            job.available_at = service.utcnow() + timedelta(seconds=BACKOFF_S[min(job.attempt - 2, len(BACKOFF_S) - 1)])
            job.lease_owner = job.lease_expires_at = job.executor_ref = None
            job.bootstrap_token_hash = job.session_token_hash = None
            await service.set_job_status(db, job, JobStatus.queued, f"retry {job.attempt}: {reason}")
            outcome = "requeued"
        if mission is not None:
            await service.advance(db, state, mission)
        return outcome


async def watchdog(state: AppState, executor: Executor) -> TickReport:
    report = TickReport()
    now = service.utcnow()
    async with state.sessions() as db:
        running = (await db.execute(select(Job).where(Job.status == JobStatus.running.value))).scalars().all()
        missions_late = (
            (
                await db.execute(
                    select(Mission.id).where(
                        Mission.status.in_(ACTIVE_MISSIONS), Mission.deadline_at.is_not(None), Mission.deadline_at < now
                    )
                )
            )
            .scalars()
            .all()
        )
    for job in running:
        if job.deadline_at is not None and job.deadline_at < now:
            await _time_out(state, executor, job.id)
            report.timed_out.append(job.id)
            continue
        alive = True
        if job.executor_ref:
            try:
                alive = await executor.is_running(job.executor_ref)
            except ExecutorError:
                alive = True  # unknown: rely on the lease
        lease_expired = job.lease_expires_at is not None and job.lease_expires_at < now
        if lease_expired or not alive:
            tail = ""
            if job.executor_ref:
                tail = await executor.logs(job.executor_ref)
                try:
                    await executor.stop(job.executor_ref)
                except ExecutorError:
                    log.warning("could not stop %s", job.executor_ref)
            outcome = await requeue_or_fail(
                state, job.id, "runner stopped without completing" if not alive else "lease expired", tail
            )
            (report.requeued if outcome == "requeued" else report.failed).append(job.id)
    report.reaped = await reap(state, executor)
    for mission_id in missions_late:
        async with state.sessions() as db, db.begin():
            mission = await db.get(Mission, mission_id, with_for_update=True)
            if mission is not None and not MissionStatus(mission.status).terminal:
                await service.finish(db, state, mission, MissionStatus.timed_out, "mission deadline passed")
    return report


async def reap(state: AppState, executor: Executor) -> list[str]:
    """Stop and remove the runner of every ended job (runners exit on their own; this cleans up the rest)."""
    async with state.sessions() as db:
        ended = (
            await db.execute(
                select(Job.id, Job.executor_ref).where(
                    Job.status.in_([s.value for s in JobStatus if s.terminal]), Job.executor_ref.is_not(None)
                )
            )
        ).all()
    reaped = []
    for job_id, ref in ended:
        if ref is None:
            continue
        try:
            await executor.stop(ref)
        except ExecutorError:
            log.warning("could not stop %s", ref)
            continue
        async with state.sessions() as db, db.begin():
            job = await db.get(Job, job_id, with_for_update=True)
            if job is not None and job.executor_ref == ref:
                job.executor_ref = None
        reaped.append(job_id)
    return reaped


async def _time_out(state: AppState, executor: Executor, job_id: str) -> None:
    async with state.sessions() as db, db.begin():
        job = await db.get(Job, job_id, with_for_update=True)
        if job is None or JobStatus(job.status).terminal:
            return
        ref = job.executor_ref
        job.error = {"class": "timeout", "message": "job deadline passed"}
        await service.set_job_status(db, job, JobStatus.timed_out, "deadline")
        mission = await db.get(Mission, job.mission_id)
        if mission is not None:
            await service.advance(db, state, mission)
    if ref:
        try:
            await executor.stop(ref)
        except ExecutorError:
            log.warning("could not stop %s", ref)


async def tick(state: AppState, executor: Executor, worker: str = "scheduler") -> dict[str, Any]:
    report = await watchdog(state, executor)
    report.dispatched = await dispatch(state, executor, worker)
    return report.__dict__
