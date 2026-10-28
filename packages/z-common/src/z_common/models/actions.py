"""action.schema.json: action envelope, previews and built-in payloads."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from z_common.models.base import Slug, WriteOperationId, ZModel
from z_common.models.common import RunsAs


class DiffFile(ZModel):
    path: str
    added: int | None = None
    removed: int | None = None


class DiffPreview(ZModel):
    kind: Literal["diff"]
    project: str
    source_branch: str
    target_branch: str
    title: str
    description: str | None = None
    patch_artifact: str | None = None
    diff: str | None = None
    files: list[DiffFile] | None = None


class IssuePreview(ZModel):
    kind: Literal["issue"]
    tracker: Literal["jira", "gitlab"]
    project: str
    title: str
    description: str | None = None
    issue_type: str | None = None
    priority: str | None = None
    labels: list[str] | None = None
    assignee: str | None = None
    due_date: str | None = None
    fields: dict[str, Any] | None = None


class FieldChange(ZModel):
    field: str
    from_: Annotated[Any, Field(alias="from")] = None
    to: Any = None


class IssueUpdatePreview(ZModel):
    kind: Literal["issue_update"]
    tracker: Literal["jira", "gitlab"]
    issue: str
    changes: list[FieldChange]


class CommentPreview(ZModel):
    kind: Literal["comment"]
    tracker: Literal["jira", "gitlab"] | None = None
    target: str
    body: str


class MailPreview(ZModel):
    kind: Literal["mail"]
    from_: Annotated[str | None, Field(alias="from")] = None
    to: list[str]
    cc: list[str] | None = None
    subject: str
    body: str
    in_reply_to: str | None = None
    attachments: list[str] | None = None


class EventPreview(ZModel):
    kind: Literal["event"]
    title: str
    start: str
    end: str
    attendees: list[str] | None = None
    location: str | None = None
    body: str | None = None


class ChatMessagePreview(ZModel):
    kind: Literal["chat_message"]
    platform: Literal["teams", "slack", "mattermost"]
    target: str
    text: str


class FilePreview(ZModel):
    kind: Literal["file"]
    name: str
    size: int | None = None
    destination: str
    artifact_id: str | None = None


class JsonPreview(ZModel):
    kind: Literal["json"]
    data: Any


Preview = Annotated[
    DiffPreview
    | IssuePreview
    | IssueUpdatePreview
    | CommentPreview
    | MailPreview
    | EventPreview
    | ChatMessagePreview
    | FilePreview
    | JsonPreview,
    Field(discriminator="kind"),
]


class ActionEnvelope(ZModel):
    connector: Slug
    operation: WriteOperationId
    payload: dict[str, Any]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    preview: Preview | None = None
    class_: Annotated[Literal["write", "destructive"] | None, Field(alias="class")] = None
    reversible: bool | None = None
    runs_as: RunsAs | None = None
    idempotency_key: str | None = None


class GitlabMergeRequestCreate(ZModel):
    project: str
    source_branch: Annotated[str, StringConstraints(pattern=r"^z/")]
    target_branch: str
    title: Annotated[str, StringConstraints(min_length=1)]
    description: str | None = None
    patch_artifact: str
    labels: list[str] | None = None


class GitlabIssueCreate(ZModel):
    project: str
    title: str
    description: str | None = None
    labels: list[str] | None = None
    assignee: str | None = None
    due_date: str | None = None


class GitlabNoteCreate(ZModel):
    project: str
    merge_request_iid: int | None = None
    issue_iid: int | None = None
    body: Annotated[str, StringConstraints(min_length=1)]


class JiraIssueCreate(ZModel):
    project: str
    summary: Annotated[str, StringConstraints(min_length=1)]
    description: str
    issue_type: str = "Task"
    priority: str | None = None
    labels: list[str] | None = None
    assignee: str | None = None
    due_date: str | None = None
    components: list[str] | None = None


class JiraIssueUpdate(ZModel):
    issue: str
    fields: dict[str, Any]


class JiraCommentCreate(ZModel):
    issue: str
    body: Annotated[str, StringConstraints(min_length=1)]


BUILTIN_PAYLOADS: dict[str, type[ZModel]] = {
    "gitlab.merge_requests.create": GitlabMergeRequestCreate,
    "gitlab.issues.create": GitlabIssueCreate,
    "gitlab.notes.create": GitlabNoteCreate,
    "jira.issues.create": JiraIssueCreate,
    "jira.issues.update": JiraIssueUpdate,
    "jira.comments.create": JiraCommentCreate,
}
