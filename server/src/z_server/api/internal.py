"""Internal routes for runners (job token) and z-server services (service token), bound to the contract.

The bootstrap token in Z_JOB_TOKEN works once, for GET /internal/jobs/{id}/spec; the spec returns a session
token valid until the job deadline. Every authenticated call extends the job lease (liveness).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import JobStatus

from z_server.api.framework import ContractOperation, contract_operations
from z_server.crypto import constant_time_equal, derive_key, token_hash
from z_server.db.models import Job, Mission
from z_server.errors import Problem
from z_server.missions.service import utcnow
from z_server.scheduler.queue import LEASE
from z_server.state import AppState

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
SPEC_OPERATION = "internalJobSpec"


# Operations that make slow calls (judge model, embeddings, the tool) before they write: they read on an
# unlocked snapshot and call `ctx.lock()` right before their first write, so a mission's other jobs, cancel
# and the gateway are not held up behind a model call.
DEFERRED_LOCK = frozenset(
    {"internalJobWorkspace", "internalJobWorkspaceManifest", "internalSearchMemory", "internalProposeAction"}
)


@dataclass
class JobCtx:
    request: Request
    db: AsyncSession
    state: AppState
    op: ContractOperation
    job: Job
    mission: Mission
    token_hash: str = ""
    locked: bool = False

    async def lock(self) -> None:
        """Lock order everywhere: the mission row, then the job row, then the audit lock. `events.append`,
        cancel and the gateway take the mission row first, so taking the job first would deadlock with them.
        Re-checks the job under the locks and extends its lease."""
        if self.locked:
            return
        column = "bootstrap_token_hash" if self.op.operation_id == SPEC_OPERATION else "session_token_hash"
        mission = await self.db.get(Mission, self.mission.id, with_for_update=True, populate_existing=True)
        job = await self.db.get(Job, self.job.id, with_for_update=True, populate_existing=True)
        if mission is None or job is None:
            raise Problem("not-found")
        if not constant_time_equal(getattr(job, column) or "", self.token_hash):  # rotated meanwhile
            raise Problem("unauthenticated", "invalid job token")
        if JobStatus(job.status).terminal:
            raise Problem("conflict", f"job is {job.status}")
        job.lease_expires_at = utcnow() + LEASE
        self.job, self.mission, self.locked = job, mission, True


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Problem("unauthenticated", "job token required")
    return auth[7:].strip()


async def _job_ctx(request: Request) -> AsyncIterator[JobCtx]:
    state: AppState = request.app.state.z
    route = request.scope.get("route")
    op = contract_operations()[getattr(route, "operation_id", "")]
    token = _bearer(request)
    path_id = request.path_params.get("id", "")
    async with state.sessions() as db, db.begin():
        column = Job.bootstrap_token_hash if op.operation_id == SPEC_OPERATION else Job.session_token_hash
        job = (await db.execute(select(Job).where(column == token_hash(token)))).scalar_one_or_none()
        if job is None or job.id != path_id:
            raise Problem("unauthenticated", "invalid job token")
        if JobStatus(job.status).terminal:
            raise Problem("conflict", f"job is {job.status}")
        if job.deadline_at is not None and job.deadline_at < utcnow():
            raise Problem("conflict", "job deadline passed")
        mission = await db.get(Mission, job.mission_id)
        if mission is None:
            raise Problem("not-found")
        ctx = JobCtx(request, db, state, op, job, mission, token_hash(token))
        if op.operation_id not in DEFERRED_LOCK:
            await ctx.lock()
        yield ctx
        await ctx.lock()  # a deferred operation that wrote nothing still extends the lease


JobCtxDep = Annotated[JobCtx, Depends(_job_ctx, scope="function")]


def service_token(state: AppState) -> str:
    """Token z-server components use between themselves (gateway -> api)."""
    return derive_key(state.settings.secret_key, "internal-service").hex()


async def require_service(request: Request) -> AppState:
    state: AppState = request.app.state.z
    if not constant_time_equal(_bearer(request), service_token(state)):
        raise Problem("unauthenticated", "service token required")
    return state


class InternalRouter(APIRouter):
    def operation(self, operation_id: str, **kwargs: Any) -> Callable[[F], F]:
        op = contract_operations()[operation_id]
        if op.permission not in {"job_token", "service"}:
            raise ValueError(f"{operation_id} is not an internal operation")

        def decorator(fn: F) -> F:
            self.add_api_route(
                op.path,
                fn,
                methods=[op.method],
                operation_id=operation_id,
                summary=op.summary,
                include_in_schema=False,
                **kwargs,
            )
            return fn

        return decorator
