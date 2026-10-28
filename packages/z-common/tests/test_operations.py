"""Operation materialization from seed connectors: classes, floor, exposure proposals."""

from __future__ import annotations

from pathlib import Path

import yaml
from z_common.connectors.operations import decide, materialize
from z_common.models.enums import OperationClass


def _ops(repo: Path, rel: str) -> dict[str, object]:
    path = repo / rel
    return {o.local_id: o for o in materialize(yaml.safe_load(path.read_text()), path.parent)}


def test_logos_openapi_ops(repo: Path) -> None:
    ops = _ops(repo, "packs/ticketing/connectors/logos.yaml")
    assert set(ops) == {"assets.get", "accounts.list", "accounts.disable", "accounts.delete"}
    disable = ops["accounts.disable"]
    assert disable.op_class == OperationClass.destructive and disable.proposed_exposed  # type: ignore[attr-defined]
    assert ops["accounts.delete"].proposed_exposed is False  # type: ignore[attr-defined]
    assert ops["assets.get"].input_schema["required"] == ["id"]  # type: ignore[attr-defined]


def test_gitlab_mcp_ops(repo: Path) -> None:
    ops = _ops(repo, "packs/dev/connectors/gitlab.yaml")
    assert ops["merge_requests.create"].op_class == OperationClass.write  # type: ignore[attr-defined]
    assert ops["merge_requests.merge"].op_class == OperationClass.destructive  # type: ignore[attr-defined]
    assert ops["merge_requests.diff"].op_class == OperationClass.read  # type: ignore[attr-defined]
    assert ops["files.get"].source_ref == "mcp:get_file_contents"  # type: ignore[attr-defined]


def test_rule_cannot_lower_and_glob_does_not_cover_writes() -> None:
    manifest = {
        "kind": "rest",
        "operations": {
            "default_exposed": False,
            "rules": [
                {"match": "accounts.disable", "class": "read", "exposed": True},
                {"match": "accounts.*", "class": "read", "exposed": True},
            ],
        },
    }
    floor, cls, exposed = decide(manifest, "accounts.disable", "POST")
    assert (floor, cls, exposed) == (OperationClass.destructive, OperationClass.destructive, True)
    floor, cls, exposed = decide(manifest, "accounts.create", "POST")
    assert cls == OperationClass.write and exposed is False  # glob ignored for a write
    assert decide(manifest, "accounts.list", "GET")[2] is True
