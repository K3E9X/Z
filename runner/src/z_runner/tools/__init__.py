"""Tools of a job, built from `JobSpec.tools` (agent profile ∩ playbook step ∩ connector exposure, computed by
z-server) and the platform rules of docs/04 §1 and §5."""

from __future__ import annotations

import logging

from z_common.models.api_internal import JobSpec
from z_common.models.enums import WorkspaceMode

from z_runner.tools.base import ToolContext, ToolResult, ToolSet, ZTool
from z_runner.tools.builtins import AskHumanTool, ProposeActionTool, SearchMemoryTool, SpawnSubagentTool
from z_runner.tools.documents import DocumentsTool
from z_runner.tools.fs import EditTool, ReadTool
from z_runner.tools.gateway import GatewayClient, GatewayOperationTool
from z_runner.tools.shell import CodeTool, TerminalTool

log = logging.getLogger("z_runner.tools")

OUTPUT_FILE = "result.json"

__all__ = ["OUTPUT_FILE", "ToolContext", "ToolResult", "ToolSet", "ZTool", "build_toolset"]


def build_toolset(spec: JobSpec, gateway: GatewayClient) -> ToolSet:
    local = set(spec.tools.local)
    tools: list[ZTool] = []
    if "read" in local:
        tools.append(ReadTool())
    if "edit" in local:
        tools.append(EditTool())
    elif spec.output is not None:
        tools.append(EditTool(only_paths=frozenset({OUTPUT_FILE})))
    if "terminal" in local:
        if spec.workspace.mode in (WorkspaceMode.files, WorkspaceMode.git_clone):
            tools.append(TerminalTool())
        else:
            log.info("terminal not offered: workspace mode %s", spec.workspace.mode.value)
    if "code" in local:
        tools.append(CodeTool())
    if "documents" in local:
        tools.append(DocumentsTool())
    if "browser" in local:
        # Browser flows run gateway-side (z-browser) and reach the runner as gateway operations.
        log.info("browser flows are listed by the gateway; no local browser tool")
    for gateway_tool in spec.tools.gateway:
        tools.append(GatewayOperationTool(gateway_tool, gateway))
    # Built-ins: ask_human and search_memory are implicit for every agent (docs/03 §4).
    tools.append(AskHumanTool())
    tools.append(SearchMemoryTool())
    if spec.writes:
        tools.append(ProposeActionTool(spec.writes))
    if "spawn_subagent" in spec.tools.builtins:
        tools.append(SpawnSubagentTool())
    names = [t.name for t in tools]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(f"duplicate tool names in the job spec: {sorted(duplicates)}")
    return ToolSet(tools=tools, write_operations=frozenset(w.operation for w in spec.writes))
