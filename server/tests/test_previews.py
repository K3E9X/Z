"""Review previews built from reviewed payloads (docs/03 §4): Microsoft 365 writes read as what they do, and the
Graph subset refuses fields a reviewer would not see in them (hidden recipients, extra headers)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from z_server.actions import previews

GRAPH = Path(__file__).resolve().parents[2] / "packs/workplace/connectors/microsoft-graph.openapi.json"


def request_schema(path: str, method: str = "post") -> dict[str, Any]:
    spec = json.loads(GRAPH.read_text())
    return dict(spec["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"])


def to(*addresses: str) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": a}} for a in addresses]


MAIL = {
    "message": {
        "subject": "Weekly report",
        "body": {"contentType": "Text", "content": "Two incidents, both closed."},
        "toRecipients": to("bob@example.test"),
        "ccRecipients": to("carol@example.test"),
    }
}


def test_m365_mail_event_chat_and_file_previews() -> None:
    assert previews.build("m365.mail.send", {"body": MAIL}) == {
        "kind": "mail",
        "to": ["bob@example.test"],
        "cc": ["carol@example.test"],
        "subject": "Weekly report",
        "body": "Two incidents, both closed.",
    }
    reply = previews.build("m365.mail.reply", {"message_id": "AAMk1", "body": {"comment": "Done."}})
    assert reply == {"kind": "comment", "target": "Reply to mail AAMk1", "body": "Done."}
    event = previews.build(
        "m365.calendar.events.create",
        {
            "body": {
                "subject": "Access review",
                "start": {"dateTime": "2026-11-03T10:00:00", "timeZone": "Europe/Paris"},
                "end": {"dateTime": "2026-11-03T10:30:00", "timeZone": "Europe/Paris"},
                "location": {"displayName": "Room 4"},
                "attendees": [{"emailAddress": {"address": "bob@example.test"}, "type": "required"}],
            }
        },
    )
    assert event["kind"] == "event" and event["start"] == "2026-11-03T10:00:00 Europe/Paris"
    assert event["attendees"] == ["bob@example.test"] and event["location"] == "Room 4"
    assert "invitation to every attendee" in event["body"]
    chat = previews.build("m365.teams.messages.post", {"chat_id": "19:abc", "body": {"body": {"content": "On it"}}})
    assert chat == {"kind": "chat_message", "platform": "teams", "target": "chat 19:abc", "text": "On it"}
    file = previews.build("m365.files.upload", {"parent_id": "01ABC", "filename": "report.md", "body": "# Été"})
    assert file == {"kind": "file", "name": "report.md", "size": 7, "destination": "OneDrive folder 01ABC"}


@pytest.mark.parametrize(
    "extra",
    [
        {"bccRecipients": to("eve@example.test")},
        {"replyTo": to("eve@example.test")},
        {"internetMessageHeaders": [{"name": "X-Forward", "value": "eve@example.test"}]},
        {"subject": "Hi\r\nBcc: eve@example.test"},
        {"toRecipients": to("Eve <eve@example.test>")},
    ],
)
def test_the_graph_mail_refuses_what_the_preview_would_not_show(extra: dict[str, Any]) -> None:
    schema = request_schema("/me/sendMail")
    jsonschema.validate(MAIL, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**MAIL, "message": {**MAIL["message"], **extra}}, schema)
