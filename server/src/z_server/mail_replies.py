"""Reply to a Z mail answers a question (docs/05 §10, ADR 0034).

A question notification sent by e-mail carries `[Z-Q:<question id>.<mac>]` in its subject. The mac is an HMAC
of the question id with a key derived from the platform secret, so a token cannot be guessed or forged for
another question. When the e-mail channel receives a mail whose subject carries such a tag:
- the sender must be a known, active Z user (the channel's usual mapping) who may answer the question, by the
  same rules and the same code as the API (`questions.answer`: audit, events, job resumed);
- the answer is the first line of the body that is not quoted (`>`) and not the client's "On ... wrote:"
  header; for a question with options it must match one of them, case-insensitive;
- the mail is data, never instructions (invariant 6): the line is recorded as the person's answer, after the
  judge's pattern scan, and nothing else in the mail is read.
The sender gets a confirmation or the refusal reason by e-mail through the admins' SMTP server (the outbox, kind
`mail_reply`). Unknown senders get nothing back (no backscatter); a mail without a tag stays a task.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass, JudgeVerdict

from z_server import audit, judge, notifications, questions
from z_server.crypto import derive_key
from z_server.db.models import Channel, Mission, Question, User
from z_server.errors import Problem
from z_server.rbac import Principal
from z_server.state import AppState

PURPOSE = "mail-reply-token"
MAC_HEX = 24  # 96 bits
TAG = re.compile(r"\[Z-Q:([^\]\s]{1,120})\]", re.IGNORECASE)
TOKEN = re.compile(r"^([A-Za-z0-9]{1,16}_[0-9A-Z]{26})\.([0-9a-f]{" + str(MAC_HEX) + r"})$")
QUOTE_HEADER = re.compile(r"^(on\b.*\bwrote:|-{2,}\s*original message\s*-{2,}|from:\s.*|-- ?)$", re.IGNORECASE)
MAX_ANSWER = 2000


def _mac(secret: str, question_id: str) -> str:
    key = derive_key(secret, PURPOSE)
    return hmac.new(key, question_id.encode(), hashlib.sha256).hexdigest()[:MAC_HEX]


def token(secret: str, question_id: str) -> str:
    return f"{question_id}.{_mac(secret, question_id)}"


def subject_tag(secret: str, question_id: str) -> str:
    return f"[Z-Q:{token(secret, question_id)}]"


def tag_in(subject: str | None) -> str | None:
    """The raw token of a reply tag in the subject (forged or not), or None when the mail is not a reply."""
    match = TAG.search(subject or "")
    return match.group(1) if match else None


def question_for(secret: str, raw: str) -> str | None:
    """The question id a token was issued for, or None for an unknown or forged token."""
    match = TOKEN.match(raw)
    if match is None:
        return None
    question_id, mac = match.groups()
    return question_id if hmac.compare_digest(mac, _mac(secret, question_id)) else None


def first_line(text: str) -> str:
    """The first line that is neither blank nor quoted; empty when the reply starts with the quoted mail."""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">") or QUOTE_HEADER.match(line):
            return ""
        return line[:MAX_ANSWER]
    return ""


def _option(options: list[object], line: str) -> str:
    wanted = line.strip().rstrip(".").casefold()
    for option in options:
        if str(option).casefold() == wanted:
            return str(option)
    return line


def _shown(mission: Mission | None) -> bool:
    if mission is None:
        return False
    try:
        return DataClass(mission.data_class).rank <= DataClass(notifications.PERSONAL_CONTENT_UP_TO).rank
    except ValueError:
        return False


async def answer(
    db: AsyncSession, state: AppState, channel: Channel, user: User, principal: Principal, raw: str, body: str
) -> str:
    """Answer the question a reply token names, as `user`; returns the reply shown to the relay. A refusal changes
    nothing but the audit log and the reply mail."""
    actor = audit.Actor(f"channel:{channel.id}")
    base = state.settings.public_url.rstrip("/")
    question_id = question_for(state.settings.secret_key, raw)

    async def refuse(reason: str, detail: str, link: str = base) -> str:
        await audit.record(
            db,
            actor,
            "channel.denied",
            "channel",
            channel.id,
            result="denied",
            runs_as=user.username,
            after={"reason": reason, "question": question_id},
        )
        _reply(db, user, "Your answer was not recorded", detail, link)
        return f"Not recorded: {detail}"

    if question_id is None:
        return await refuse("unknown reply token", "this mail does not answer a question Z asked.")
    row = await db.get(Question, question_id)
    mission = await db.get(Mission, row.mission_id) if row is not None else None
    if row is None or mission is None:
        return await refuse("unknown question", "this question is no longer in Z.")
    link = f"{base}/missions/{mission.id}"
    line = first_line(body)
    if not line:
        return await refuse("empty answer", "write your answer on the first line of the reply.", link)
    if judge.scan(line).verdict == JudgeVerdict.blocked:
        return await refuse("judge blocked", "the judge held this answer: answer the question in Z.", link)
    text = _option(list(row.options or []), line)
    if row.answer is not None and row.answered_by == user.username and row.answer == text:
        return "Already received."  # the relay delivered the same reply twice
    try:
        async with db.begin_nested():
            await questions.answer(
                db, state, principal, question_id, text, actor, runs_as=user.username, note={"channel": "email"}
            )
    except Problem as exc:
        if exc.kind == "validation" and row.options:
            detail = (
                f"answer with one of: {', '.join(str(o) for o in row.options)}."
                if _shown(mission)
                else "your answer is not one of the options: see them in Z."
            )
        else:
            detail = {
                "not-found": "this question is not in Z, or you cannot see its mission.",
                "forbidden": "you may not answer this question (questions:answer on the mission's team).",
                "conflict": "this question is already answered.",
            }.get(exc.kind, exc.detail or exc.kind)
        return await refuse(exc.kind, detail, link)
    _reply(db, user, "Answer recorded", "Z recorded your answer; the agent goes on with it.", link)
    return "Answer recorded."


def _reply(db: AsyncSession, user: User, title: str, text: str, link: str) -> None:
    """A mail back to the person, through the outbox (sent once the transaction commits)."""
    notifications.enqueue(
        db,
        "mail_reply",
        payload={"user_id": user.id, "title": title, "text": text, "link": link, "link_label": "Open in Z"},
    )
