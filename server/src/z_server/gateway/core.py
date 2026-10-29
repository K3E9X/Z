"""The gateway call pipeline (docs/05 §2) for agent reads, and the shared tool invocation used by the executor.

For every agent call: the operation must be in the job's allowlist (recomputed per call, so revoking exposure
takes effect at once) and of class read; arguments are validated; rate limits per connector and per mission
apply; the read credential is injected; the tool is called with a timeout (idempotent reads retried); the
response is shaped (ADF flattened, noise dropped), redacted, judge-scanned, truncated into an artifact when
large; a C2+ connector raises the mission class before the result is returned; every call is audited.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from sqlalchemy import select
from z_common.models.enums import DataClass, JobStatus, JudgeVerdict, OperationClass
from z_common.models.manifests import AgentProfile
from z_common.redaction import Redactor

from z_server import artifacts, audit, events, judge, judge_model
from z_server.crypto import token_hash
from z_server.db.models import Connector, Job, Mission, Operation
from z_server.gateway import adf, browser, cli, mcp_client, oauth, queries
from z_server.gateway import http as http_invoker
from z_server.gateway.auth import AuthError, AuthMaterial, Purpose, material
from z_server.gateway.limits import CallStats, SlidingWindow, replica_share
from z_server.gateway.secrets import SecretResolver
from z_server.llm.litellm import LlmKeyError
from z_server.missions import service, specs
from z_server.missions.service import utcnow
from z_server.scheduler.queue import LEASE
from z_server.state import AppState

log = logging.getLogger("z.gateway")
MAX_RESULT_CHARS = 24_000
NOISE_KEYS = {"avatarUrls", "iconUrl", "expand", "_links", "avatar_url", "web_url_avatar"}
DEFAULT_TIMEOUT_S = 30.0


class GatewayRefused(Exception):
    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class Invocation:
    ok: bool
    status: int | None
    body: Any
    latency_ms: int
    identity: str | None
    secrets: list[str] = field(default_factory=list)
    error: str | None = None
    files: list[tuple[str, bytes]] = field(default_factory=list)  # browser screenshots, stored as artifacts


@dataclass
class Outcome:
    ok: bool
    text: str
    judge: JudgeVerdict = JudgeVerdict.clean
    artifact_id: str | None = None
    truncated: bool = False
    structured: Any = None


def shape(value: Any) -> Any:
    """Flatten rich text and drop presentation noise before results reach a model."""
    value = adf.flatten(value)

    def drop(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: drop(x) for k, x in v.items() if k not in NOISE_KEYS}
        if isinstance(v, list):
            return [drop(x) for x in v]
        return v

    return drop(value)


def with_defaults(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    for name, prop in ((schema or {}).get("properties") or {}).items():
        if name not in out and isinstance(prop, dict) and "default" in prop:
            out[name] = prop["default"]
    return out


@dataclass
class Gateway:
    state: AppState
    resolver: SecretResolver
    windows: SlidingWindow = field(default_factory=SlidingWindow)
    stats: CallStats = field(default_factory=CallStats)

    @classmethod
    def create(cls, state: AppState) -> Gateway:
        return cls(state, SecretResolver(state.http))

    # ---------------------------------------------------------------- shared invocation

    async def auth(self, connector: Connector, purpose: Purpose) -> AuthMaterial:
        """Credential headers for one call: the acting user's delegated grant (user-scope steps and actions),
        an app-only OAuth token, or a static secret."""
        user = oauth.ACTING_USER.get()
        try:
            if user is not None:
                token, identity = await oauth.user_token(self.state, self.resolver, connector, purpose, user)
                return AuthMaterial({"Authorization": f"Bearer {token}"}, identity, [token])
            if oauth.flow(connector.manifest, purpose, "service") is not None:
                token = await oauth.service_token(self.state.http, self.resolver, connector.manifest, purpose)
                app = oauth.flow(connector.manifest, purpose, "service") or {}
                return AuthMaterial({"Authorization": f"Bearer {token}"}, f"app:{app.get('app')}", [token])
        except oauth.OAuthError as exc:
            raise AuthError(str(exc)) from exc
        return await material(connector.manifest, purpose, self.resolver)

    async def invoke(
        self,
        connector: Connector,
        op: Operation,
        args: dict[str, Any],
        purpose: Purpose,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Invocation:
        """Call one operation with the credential of `purpose`. Never raises for tool-side failures."""
        manifest = connector.manifest
        try:
            auth = await self.auth(connector, purpose)
        except AuthError as exc:
            return Invocation(False, None, None, 0, None, error=str(exc))
        started = time.monotonic()
        try:
            if connector.kind in {"openapi", "rest"}:
                base = http_invoker.base_url(connector.base_url, manifest)
                prepared = http_invoker.prepare(base, op.source_ref, op.input_schema, args)
                result = await http_invoker.send(self.state.http, prepared, auth.headers, timeout_s=timeout_s)
                ok, error = http_invoker.outcome(manifest, result.status, result.body)
                return Invocation(ok, result.status, result.body, result.latency_ms, auth.identity, auth.secrets, error)
            if connector.kind == "mcp":
                res = await mcp_client.call(
                    manifest, connector.base_url, op.local_id, args, auth.headers, self.resolver, timeout_s
                )
                latency = int((time.monotonic() - started) * 1000)
                payload = mcp_client.result_payload(res)
                return Invocation(
                    not res.isError,
                    None,
                    payload,
                    latency,
                    auth.identity,
                    auth.secrets,
                    "tool returned an error" if res.isError else None,
                )
            if connector.kind == "graphql":
                ok, status, body, error = await queries.graphql(self.state.http, connector, op, args, auth, timeout_s)
                latency = int((time.monotonic() - started) * 1000)
                return Invocation(ok, status, body, latency, auth.identity, auth.secrets, error)
            if connector.kind == "database":
                ok, body, error, latency = await queries.database(connector, op, args, auth, purpose)
                return Invocation(ok, None, body, latency, auth.identity, auth.secrets, error)
            if connector.kind == "cli":
                tool = await cli.run(self.state, connector, op, args, auth)
                latency = int((time.monotonic() - started) * 1000)
                return Invocation(tool.ok, None, tool.body, latency, auth.identity, auth.secrets, tool.error)
            if connector.kind == "browser":
                run = await browser.run(self.state, connector, op, args, auth, timeout_s)
                return Invocation(
                    run.ok, None, run.body, run.latency_ms, auth.identity, auth.secrets, run.error, run.files
                )
            return Invocation(
                False,
                None,
                None,
                0,
                auth.identity,
                auth.secrets,
                f"connector kind {connector.kind} is not served by this gateway build",
            )
        except http_invoker.InvokeError as exc:
            return Invocation(
                False, exc.status, None, int((time.monotonic() - started) * 1000), auth.identity, auth.secrets, str(exc)
            )
        except (queries.QueryRefused, cli.CliRefused) as exc:
            return Invocation(False, None, None, 0, auth.identity, auth.secrets, str(exc))

    async def send(
        self,
        connector: Connector,
        method: str,
        path: str,
        purpose: Purpose,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Invocation:
        """A request built by a connector adapter (executor writes, idempotency lookups) on an HTTP connector."""
        try:
            auth = await self.auth(connector, purpose)
        except AuthError as exc:
            return Invocation(False, None, None, 0, None, error=str(exc))
        try:
            base = http_invoker.base_url(connector.base_url, connector.manifest)
            prepared = http_invoker.PreparedRequest(method.upper(), base + path, params or {}, json_body, {})
            result = await http_invoker.send(self.state.http, prepared, auth.headers, timeout_s=timeout_s)
        except http_invoker.InvokeError as exc:
            return Invocation(False, exc.status, None, 0, auth.identity, auth.secrets, str(exc))
        ok, error = http_invoker.outcome(connector.manifest, result.status, result.body)
        return Invocation(ok, result.status, result.body, result.latency_ms, auth.identity, auth.secrets, error)

    # ---------------------------------------------------------------- agent side

    async def job_for(self, token: str) -> tuple[Job, Mission]:
        async with self.state.sessions() as db, db.begin():
            job = (
                await db.execute(select(Job).where(Job.session_token_hash == token_hash(token)))
            ).scalar_one_or_none()
            if job is None or JobStatus(job.status).terminal:
                raise GatewayRefused("unauthenticated", "invalid or expired job token")
            mission = await db.get(Mission, job.mission_id)
            if mission is None:
                raise GatewayRefused("unauthenticated", "mission not found")
            job.lease_expires_at = utcnow() + LEASE
            return job, mission

    async def tools(self, token: str) -> list[Any]:
        job, mission = await self.job_for(token)
        async with self.state.sessions() as db:
            profile = AgentProfile.model_validate(await service.agent_profile(db, job))
            return await specs.gateway_tools(db, profile, mission)

    async def _deny(self, job: Job, mission: Mission, what: str, rule: str, detail: str) -> None:
        async with self.state.sessions() as db, db.begin():
            await events.append(
                db,
                mission.id,
                "denied",
                {"what": what, "rule": rule, "detail": detail},
                job_id=job.id,
                agent=job.agent_name,
                step_id=job.step_id,
            )
            await audit.record(
                db,
                audit.Actor(f"agent:{job.agent_name}"),
                "gateway.denied",
                "operation",
                what,
                result="denied",
                team=mission.team_id,
                after={"rule": rule, "detail": detail, "mission": mission.id, "job": job.id},
            )

    async def call(self, token: str, name: str, arguments: dict[str, Any] | None) -> Outcome:
        job, mission = await self.job_for(token)
        args = dict(arguments or {})
        async with self.state.sessions() as db:
            profile = AgentProfile.model_validate(await service.agent_profile(db, job))
            allowed = {t.name: t for t in await specs.gateway_tools(db, profile, mission)}
            tool = allowed.get(name)
            op = await db.get(Operation, tool.operation) if tool else None
            connector = await db.get(Connector, op.connector_id) if op else None
        if tool is None or op is None or connector is None:
            await self._deny(job, mission, name, "not_in_allowlist", f"{name} is not an operation this job may call")
            return Outcome(False, f"Refused: {name} is not in this job's allowlist.")
        if op.op_class != OperationClass.read.value:
            await self._deny(job, mission, op.id, "write_requires_proposal", "writes go through propose_action")
            return Outcome(False, f"Refused: {op.id} is a {op.op_class} operation; use propose_action.")
        args = with_defaults(op.input_schema, args)
        try:
            jsonschema.validate(args, op.input_schema or {"type": "object"})
        except jsonschema.ValidationError as exc:
            return Outcome(False, f"Invalid arguments for {op.id}: {exc.message[:300]}")
        limits = connector.manifest.get("rate_limit") or {}
        for key, limit in (
            (f"c:{connector.name}", limits.get("per_minute")),
            (f"m:{mission.id}:{connector.name}", limits.get("per_mission_per_minute")),
        ):
            if limit:
                wait_s = self.windows.allow(key, replica_share(int(limit)))
                if wait_s:
                    await self._deny(job, mission, op.id, "rate_limit", f"{key.split(':')[0]} limit {limit}/min")
                    return Outcome(False, f"Rate limited: retry {op.id} in {wait_s:.0f} s.")
        step: dict[str, Any] = next(
            (st for st in (mission.plan or {}).get("steps", []) if st.get("id") == job.step_id), {}
        )
        user_scope = step.get("credentials") == "user"
        acting = oauth.ACTING_USER.set(mission.created_by if user_scope else None)
        try:
            inv = await self.invoke(connector, op, args, "read")
        finally:
            oauth.ACTING_USER.reset(acting)
        self.stats.record(connector.name, inv.latency_ms, inv.ok)
        known = [*inv.secrets, token]
        if mission.llm_key_enc:
            known.append(service.mission_key(self.state, mission))
        redactor = Redactor(known)
        if inv.files and isinstance(inv.body, dict):
            inv.body["screenshots"] = await self._store_files(job, mission, connector, inv.files)
        body = redactor.value(shape(inv.body))
        if connector.kind == "browser" and isinstance(body, dict):
            await self._browser_event(job, mission, op, body)
        text = (
            body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        if not inv.ok:
            text = (
                f"{op.id} failed ({inv.error}). {text[:1500]}"
                if text and text != "null"
                else f"{op.id} failed ({inv.error})."
            )
        verdict = await judge_model.review(
            self.state, text, judge.scan(text), owner=(mission.created_by, mission.team_id or "")
        )
        return await self._finish(job, mission, connector, op, inv, args, text, body, verdict)

    async def _browser_event(self, job: Job, mission: Mission, op: Operation, body: dict[str, Any]) -> None:
        """The live timeline shows where the flow ended and its first screenshot (events schema `browser`)."""
        shots = body.get("screenshots") or []
        async with self.state.sessions() as db, db.begin():
            await events.append(
                db,
                mission.id,
                "browser",
                {
                    "url": str(body.get("url") or ""),
                    "action": op.id,
                    "screenshot_ref": shots[0]["screenshot_ref"] if shots else None,
                },
                job_id=job.id,
                agent=job.agent_name,
                step_id=job.step_id,
            )

    async def _store_files(
        self, job: Job, mission: Mission, connector: Connector, files: list[tuple[str, bytes]]
    ) -> list[dict[str, str]]:
        """Screenshots become artifacts of the tool's data class (password fields are masked by the worker)."""
        refs = []
        async with self.state.sessions() as db, db.begin():
            row_mission = await db.get(Mission, mission.id)
            current = row_mission.data_class if row_mission is not None else mission.data_class
            for name, data in files:
                row = await artifacts.create(
                    db,
                    self.state.settings.artifacts_dir,
                    mission_id=mission.id,
                    job_id=job.id,
                    kind="screenshot",
                    name=name,
                    data=data,
                    data_class=max(current, connector.data_class),
                    content_type="image/png",
                )
                await events.append(
                    db,
                    mission.id,
                    "artifact",
                    {"artifact_id": row.id, "kind": "screenshot", "name": row.name},
                    job_id=job.id,
                    agent=job.agent_name,
                    step_id=job.step_id,
                )
                refs.append({"name": row.name, "screenshot_ref": row.id})
        return refs

    async def _finish(
        self,
        job: Job,
        mission: Mission,
        connector: Connector,
        op: Operation,
        inv: Invocation,
        args: dict[str, Any],
        text: str,
        body: Any,
        verdict: judge.Verdict,
    ) -> Outcome:
        outcome = Outcome(inv.ok, text, verdict.verdict, structured=body if inv.ok and isinstance(body, dict) else None)
        async with self.state.sessions() as db, db.begin():
            mission_row = await db.get(Mission, mission.id, with_for_update=True)
            job_row = await db.get(Job, job.id, with_for_update=True)
            assert mission_row is not None and job_row is not None
            if inv.ok and DataClass(connector.data_class).rank > DataClass(mission_row.data_class).rank:
                try:
                    await service.raise_data_class(
                        db,
                        self.state,
                        mission_row,
                        DataClass(connector.data_class),
                        "tool_result",
                        f"{op.id} ({connector.name} is {connector.data_class})",
                        job_row,
                    )
                except LlmKeyError:
                    log.error("could not restrict the key of mission %s; withholding %s", mission.id, op.id)
                    return Outcome(
                        False,
                        f"The result of {op.id} was withheld: the platform could not restrict the model key for its data class.",
                    )
            if verdict.verdict != JudgeVerdict.clean:
                await events.append(
                    db,
                    mission.id,
                    "judge",
                    {
                        "subject": "tool_result",
                        "ref": op.id,
                        "verdict": verdict.verdict.value,
                        "categories": verdict.categories,
                        "reason": verdict.reason,
                    },
                    job_id=job.id,
                    agent=job.agent_name,
                    step_id=job.step_id,
                )
            if verdict.verdict == JudgeVerdict.blocked:
                job_row.control_state, job_row.control_reason = "pause", f"the judge withheld a result of {op.id}"
                await service.set_job_status(db, job_row, JobStatus.needs_attention, "judge_blocked")
                await service.advance(db, self.state, mission_row)
                outcome = Outcome(
                    False,
                    f"The result of {op.id} was withheld by the platform (possible prompt injection: {', '.join(verdict.categories)}). A human will review it.",
                    JudgeVerdict.blocked,
                )
            elif len(text) > MAX_RESULT_CHARS:
                row = await artifacts.create(
                    db,
                    self.state.settings.artifacts_dir,
                    mission_id=mission.id,
                    job_id=job.id,
                    kind="data",
                    name=f"{op.id}.json",
                    data=text.encode(),
                    data_class=max(mission_row.data_class, connector.data_class),
                    content_type="application/json",
                )
                await events.append(
                    db,
                    mission.id,
                    "artifact",
                    {"artifact_id": row.id, "kind": "data", "name": row.name},
                    job_id=job.id,
                    agent=job.agent_name,
                    step_id=job.step_id,
                )
                outcome = Outcome(
                    inv.ok,
                    text[:MAX_RESULT_CHARS]
                    + f"\n[truncated: {len(text)} characters; the full result is artifact {row.id}]",
                    verdict.verdict,
                    row.id,
                    True,
                )
            await audit.record(
                db,
                audit.Actor(f"agent:{job.agent_name}"),
                "gateway.call",
                "operation",
                op.id,
                result="success" if inv.ok else "error",
                team=mission.team_id,
                runs_as=inv.identity,
                after={
                    "mission": mission.id,
                    "job": job.id,
                    "status": inv.status,
                    "duration_ms": inv.latency_ms,
                    "args": sorted(args),
                    "judge": verdict.verdict.value,
                    "error": inv.error,
                },
            )
        return outcome
