"""Base model and shared field types."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
Handle = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,39}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
Id = Annotated[str, StringConstraints(min_length=1, max_length=64)]
OperationId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")]
WriteOperationId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$")]
LocalOperationId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")]
ModelAlias = Annotated[str, StringConstraints(pattern=r"^(z|ext)-[a-z0-9][a-z0-9-]*$")]
RoleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,39}$")]
Permission = Annotated[str, StringConstraints(pattern=r"^[a-z_]+:[a-z_]+$")]
Scope = Annotated[str, StringConstraints(pattern=r"^(org|team:.+)$")]
Subject = Annotated[str, StringConstraints(pattern=r"^(user|group):.+$")]
SecretRef = Annotated[str, StringConstraints(pattern=r"^(env|file|vault|kubernetes):.+$")]
StepId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")]
NonNegative = Annotated[int, Field(ge=0)]
Json = dict[str, Any]
Timestamp = datetime


class ZModel(BaseModel):
    """Strict by default: unknown keys are refused, like the contracts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=False)

    def dump(self) -> dict[str, Any]:
        """JSON-compatible dict with contract key names, without unset optional keys."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)


class OpenModel(BaseModel):
    """For contract objects that allow extra keys (kind-specific config, filters)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
