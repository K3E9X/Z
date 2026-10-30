"""GitLab webhook filter shortcuts (intake.service.gitlab_view)."""

from __future__ import annotations

from z_server.intake.service import matches

ISSUE = {
    "object_kind": "issue",
    "object_attributes": {"action": "open", "title": "Rotate the TLS certificate"},
    "labels": [{"title": "security"}],
    "project": {"path_with_namespace": "infra/edge"},
    "assignees": [{"username": "alice"}, {"username": "bob"}],
}


def test_gitlab_filters_match_assignee_label_and_project() -> None:
    assert matches({"assignee": "bob", "label": "security", "project": "infra/edge"}, ISSUE, "gitlab")
    assert not matches({"assignee": "carol"}, ISSUE, "gitlab")
    older = {**{k: v for k, v in ISSUE.items() if k != "assignees"}, "assignee": {"username": "carol"}}
    assert matches({"assignee": "carol"}, older, "gitlab")
    assert not matches({"assignee": "alice"}, {"object_kind": "issue"}, "gitlab")  # nobody assigned
