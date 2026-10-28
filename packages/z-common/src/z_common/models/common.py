"""Small shapes of contracts/common.schema.json."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from z_common.models.base import ZModel
from z_common.models.enums import CredentialScope


class Avatar(ZModel):
    initials: Annotated[str, StringConstraints(min_length=1, max_length=3)]
    color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None
    image_url: str | None = None


class Persona(ZModel):
    tone: Literal["concise", "detailed", "friendly", "formal"] | None = None
    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None


MemoryScope = Literal["org", "team", "agent"]
ALL_MEMORY_SCOPES: tuple[MemoryScope, ...] = ("org", "team", "agent")


class MemoryConfig(ZModel):
    scopes: list[MemoryScope] = Field(default_factory=lambda: list(ALL_MEMORY_SCOPES))
    top_k: Annotated[int, Field(ge=0, le=50)] = 8


class Limits(ZModel):
    max_steps: Annotated[int, Field(ge=1)]
    max_tokens: Annotated[int, Field(ge=1)]
    timeout_s: Annotated[int, Field(ge=1)]


class MissionBudget(ZModel):
    max_tokens: Annotated[int, Field(ge=1)] | None = None
    timeout_s: Annotated[int, Field(ge=1)] | None = None


class RunsAs(ZModel):
    scope: CredentialScope
    identity: str
    user_id: str | None = None


class FieldError(ZModel):
    path: str
    message: str


class Problem(ZModel):
    """RFC 9457 problem details (extra keys allowed by the RFC are listed explicitly)."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    permission: str | None = None
    step_up_url: str | None = None
    errors: list[FieldError] | None = None
