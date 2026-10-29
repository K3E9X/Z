"""Previews shown in Review (docs/03 §4 "Actions"). Built by z-server from the canonical payload."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from z_common.models.actions import (
    ChatMessagePreview,
    CommentPreview,
    DiffFile,
    DiffPreview,
    IssuePreview,
    IssueUpdatePreview,
    JsonPreview,
)

MAX_DIFF_CHARS = 20_000
HUNK_FILE = re.compile(r"^\+\+\+ (?:b/)?(\S+)")


def diff_stats(patch: str) -> list[DiffFile]:
    files: list[DiffFile] = []
    current: DiffFile | None = None
    for line in patch.splitlines():
        match = HUNK_FILE.match(line)
        if match:
            current = DiffFile(path=match.group(1), added=0, removed=0)
            files.append(current)
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current.added = (current.added or 0) + 1
        elif current is not None and line.startswith("-") and not line.startswith("---"):
            current.removed = (current.removed or 0) + 1
    return files


def build(operation: str, payload: dict[str, Any], artifact_path: Path | None = None) -> dict[str, Any]:
    if operation == "gitlab.merge_requests.create":
        patch = (
            artifact_path.read_text(encoding="utf-8", errors="replace")
            if artifact_path and artifact_path.is_file()
            else ""
        )
        return DiffPreview(
            kind="diff",
            project=payload["project"],
            source_branch=payload["source_branch"],
            target_branch=payload["target_branch"],
            title=payload["title"],
            description=payload.get("description"),
            patch_artifact=payload.get("patch_artifact"),
            diff=patch[:MAX_DIFF_CHARS],
            files=diff_stats(patch),
        ).dump()
    if operation in {"gitlab.issues.create", "jira.issues.create"}:
        jira = operation.startswith("jira")
        return IssuePreview(
            kind="issue",
            tracker="jira" if jira else "gitlab",
            project=payload["project"],
            title=payload["summary"] if jira else payload["title"],
            description=payload.get("description"),
            issue_type=payload.get("issue_type"),
            priority=payload.get("priority"),
            labels=payload.get("labels"),
            assignee=payload.get("assignee"),
            due_date=payload.get("due_date"),
        ).dump()
    if operation == "jira.issues.update":
        return IssueUpdatePreview.model_validate(
            {
                "kind": "issue_update",
                "tracker": "jira",
                "issue": payload["issue"],
                "changes": [{"field": k, "to": v} for k, v in payload["fields"].items()],
            }
        ).dump()
    if operation == "jira.comments.create":
        return CommentPreview(kind="comment", tracker="jira", target=payload["issue"], body=payload["body"]).dump()
    if operation == "gitlab.notes.create":
        iid = payload.get("merge_request_iid")
        target = f"{payload['project']}!{iid}" if iid else f"{payload['project']}#{payload.get('issue_iid')}"
        return CommentPreview(kind="comment", tracker="gitlab", target=target, body=payload["body"]).dump()
    if operation == "mattermost.posts.create":
        body = payload.get("body") or {}
        target = str(body.get("channel_id", "")) + (f" (reply to {body['root_id']})" if body.get("root_id") else "")
        return ChatMessagePreview(
            kind="chat_message", platform="mattermost", target=target, text=str(body.get("message", ""))
        ).dump()
    return JsonPreview(kind="json", data=payload).dump()
