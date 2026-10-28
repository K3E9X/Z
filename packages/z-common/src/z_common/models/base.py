"""Base model and shared field types."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, StringConstraints, model_serializer

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
    """Strict by default: unknown keys are refused, like the contracts.

    Serialization drops optional fields whose value is None: in the contracts an optional field is
    absent rather than null unless it is declared nullable AND required (those keep null).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=False)

    @model_serializer(mode="wrap")
    def _drop_optional_none(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            for name, field in type(self).model_fields.items():
                key = field.serialization_alias or field.alias or name
                if key in data and data[key] is None and not field.is_required():
                    del data[key]
        return data

    def dump(self) -> dict[str, Any]:
        """JSON-compatible dict with contract key names, without unset optional keys."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)


class OpenModel(BaseModel):
    """For contract objects that allow extra keys (kind-specific config, filters)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
