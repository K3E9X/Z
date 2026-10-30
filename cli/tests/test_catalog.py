"""z skills diff, z playbooks save-from, z connectors import and record-browser against the real server."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from cli.tests.harness import LiveServer, Z, plain, serve
from tests.fakes._server import FakeServer
from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import REPO, base_config

# ---------------------------------------------------------------- the CLI only calls contract operations


def test_every_cli_call_is_a_contract_operation() -> None:
    from z_cli import contract

    spec = yaml.safe_load((REPO / "contracts/openapi.yaml").read_text())
    samples = [
        (method.upper(), re.sub(r"\{[^}]+\}", "x1", path))
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    for method, pattern, *_ in contract.OPERATIONS:
        prefixed = [(m, p.removeprefix("/api/v1")) for m, p in samples]
        assert any(m == method and pattern.match(p) for m, p in prefixed), f"{method} {pattern.pattern}"
    for method, path in [
        ("GET", "/skills/summarize/versions/1.0.0"),
        ("POST", "/missions/msn_1/save-as-playbook"),
        ("POST", "/connectors/import"),
        ("POST", "/connectors/record-browser"),
        ("GET", "/connectors/recordings/brec_1"),
    ]:
        assert contract.lookup(method, path) is not None, path


# ---------------------------------------------------------------- z skills diff


def new_skill_version(live: LiveServer, tmp_path: Path) -> None:
    """A pack update of the demo `summarize` skill: version 1.1.0 next to 1.0.0 (the pack seed path)."""
    from z_server import audit
    from z_server.catalog.seed import seed

    packs = tmp_path / "packs"
    shutil.copytree(REPO / "packs", packs)
    skill = packs / "demo/skills/summarize/SKILL.md"
    skill.write_text(skill.read_text().replace("version: 1.0.0", "version: 1.1.0") + "\n6. Extra rule.\n")

    async def run() -> Any:
        async with live.state.sessions() as db, db.begin():
            return await seed(db, packs, audit.Actor("system:test"), None)

    report = live.call(run)
    assert "skill summarize@1.1.0" in report.updated


def test_skills_diff(z: Z, live: LiveServer, tmp_path: Path) -> None:
    erin = z.as_user("erin")
    single = erin("skills", "diff", "summarize")
    assert single.exit_code == 2 and "no earlier version" in plain(single.output), single.output

    new_skill_version(live, tmp_path)
    shown = erin("skills", "diff", "summarize")
    assert shown.exit_code == 0, shown.output
    text = plain(shown.output)
    assert re.search(r"summarize  v1\.0\.0 → v1\.1\.0  pack · .* \+3 -1", text), text
    assert "--- summarize@1.0.0/SKILL.md" in text and "+++ summarize@1.1.0/SKILL.md" in text
    assert "+6. Extra rule." in text and "-version: 1.0.0" in text and "+version: 1.1.0" in text

    as_json = erin("skills", "diff", "summarize", "--from", "1.0.0", "--to", "1.1.0", "--json")
    assert as_json.exit_code == 0, as_json.output
    data = json.loads(as_json.output)
    assert data["skill"] == "summarize" and data["from"]["version"] == "1.0.0" and data["to"]["version"] == "1.1.0"
    assert "content" not in data["from"] and data["to"]["origin"] == "pack" and data["to"]["checksum"]
    assert data["diff"].startswith("--- summarize@1.0.0/SKILL.md\n+++ summarize@1.1.0/SKILL.md\n@@")
    assert "\n+6. Extra rule.\n" in data["diff"]

    same = erin("skills", "diff", "summarize", "--from", "1.1.0")
    assert same.exit_code == 0 and "No differences" in plain(same.output), same.output
    unknown = erin("skills", "diff", "summarize", "--from", "0.0.9")
    assert unknown.exit_code == 1 and "no version 0.0.9" in plain(unknown.output), unknown.output


def test_skills_diff_colours_the_diff() -> None:
    from rich.console import Console
    from rich.text import Text
    from z_cli.commands.catalog import diff_text, diff_view
    from z_cli.theme import rich_theme

    text = diff_text("s", {"version": "1.0.0", "content": "a\nb\n"}, {"version": "1.1.0", "content": "a\nc\n"})
    assert text == "--- s@1.0.0/SKILL.md\n+++ s@1.1.0/SKILL.md\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
    lines = [t for t in diff_view(text).renderables if isinstance(t, Text)]
    assert [(t.plain, t.style) for t in lines] == [
        ("--- s@1.0.0/SKILL.md", "z.b"),
        ("+++ s@1.1.0/SKILL.md", "z.b"),
        ("@@ -1,2 +1,2 @@", "z.acc"),
        (" a", "z.m"),
        ("-b", "z.err"),
        ("+c", "z.ok"),
    ]
    console = Console(theme=rich_theme(None), color_system="truecolor", force_terminal=True, width=80)
    with console.capture() as captured:
        console.print(diff_view(text))
    assert "\x1b[" in captured.get()  # the theme's colours reach the terminal


# ---------------------------------------------------------------- z playbooks save-from


def test_playbooks_save_from(z: Z, live: LiveServer) -> None:
    alice = z.as_user("alice")
    plan = {"steps": [{"id": "sum", "agent": "summarizer", "prompt": "Summarize the file"}]}
    with live.http(alice.token()) as http:
        resp = http.post("/api/v1/missions", json={"title": "Summarize the notes", "prompt": "Summarize", "plan": plan})
        assert resp.status_code == 201, resp.text
        mission_id = resp.json()["id"]

    saved = alice("playbooks", "save-from", mission_id, "--name", "notes-summary", "--title", "Notes digest", "--yes")
    assert saved.exit_code == 0, saved.output
    text = plain(saved.output)
    assert "Saved playbook notes-summary v1.0.0" in text and "Notes digest" in text and "@summarizer" in text
    shown = json.loads(alice("playbooks", "show", "notes-summary", "--json").output)
    assert shown["description"] == "Notes digest" and [s["id"] for s in shown["steps"]] == ["sum"]

    as_json = alice("playbooks", "save-from", mission_id, "--name", "notes-summary-2", "--domain", "docs", "--json")
    assert as_json.exit_code == 0, as_json.output
    created = json.loads(as_json.output)
    assert created["name"] == "notes-summary-2" and created["domain"] == "docs"
    assert created["description"] == "Summarize the notes"  # the mission title by default

    assert alice("playbooks", "save-from", mission_id, "--name", "x").exit_code == 2  # no terminal: needs --yes
    bad = alice("playbooks", "save-from", mission_id, "--name", "Not A Slug", "--yes")
    assert bad.exit_code == 2 and "invalid request" in plain(bad.output), bad.output
    missing = alice("playbooks", "save-from", "msn_doesnotexist0", "--name", "other", "--yes")
    assert missing.exit_code == 1 and "Not found" in plain(missing.output), missing.output


# ---------------------------------------------------------------- z connectors import

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Inventory", "version": "1"},
    "servers": [{"url": "https://inventory.example.test/api"}],
    "paths": {
        "/hosts": {
            "get": {"operationId": "listHosts", "summary": "List hosts"},
            "post": {"operationId": "createHost", "summary": "Add a host"},
        },
        "/hosts/{id}": {
            "delete": {
                "operationId": "deleteHost",
                "summary": "Remove a host",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            }
        },
    },
}


def test_connectors_import(z: Z, live: LiveServer, tmp_path: Path) -> None:
    alice = z.as_user("alice")  # connectors:manage
    spec = tmp_path / "inventory-openapi.yaml"
    spec.write_text(yaml.safe_dump(SPEC))

    shown = alice("connectors", "import", str(spec))
    assert shown.exit_code == 0, shown.output
    text = plain(shown.output)
    assert "(inventory, openapi)" in text and "https://inventory.example.test/api" in text
    assert re.search(r"inventory\.list_hosts\s+read\s+read\s+expose", text), text
    assert re.search(r"inventory\.create_host\s+write\s+\w+\s+hidden", text), text
    assert re.search(r"inventory\.delete_host\s+destructive\s+\w+\s+hidden", text), text
    assert "approve-exposure inventory" in text and "none exposed" in text

    as_json = alice("connectors", "import", str(spec), "--kind", "openapi", "--name", "inv", "--json")
    assert as_json.exit_code == 0, as_json.output
    draft = json.loads(as_json.output)
    assert draft["manifest"]["name"] == "inv" and draft["manifest"]["kind"] == "openapi"
    ops = {o["id"]: o for o in draft["operations"]}
    assert ops["inv.list_hosts"]["class"] == "read" and ops["inv.delete_host"]["class"] == "destructive"
    assert not any(o["exposed"] for o in draft["operations"])  # importing never exposes anything
    with live.http(alice.token()) as http:
        assert http.get("/api/v1/connectors/inv").status_code == 404  # and saves nothing

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"openapi": "3.0.3"}))
    refused = alice("connectors", "import", str(broken), "--kind", "openapi", "--name", "broken")
    assert refused.exit_code == 2 and "not an OpenAPI document" in plain(refused.output), refused.output
    unknown = alice("connectors", "import", str(tmp_path / "notes.bin"))
    assert unknown.exit_code == 2 and "cannot read" in plain(unknown.output)
    assert alice("connectors", "import", str(spec), "--kind", "soap").exit_code == 2
    denied = z.as_user("bob")("connectors", "import", str(spec))
    assert denied.exit_code == 4, denied.output


# ---------------------------------------------------------------- z connectors record-browser

WORKER_TOKEN = "browser-token-" + "0123456789abcdef"


def fake_recorder() -> Any:
    """The z-browser recording API (ADR 0016): one more recorded step per look (two at most), then finish; with
    `gone` set, the worker has closed the recording."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    calls: dict[str, Any] = {"get": 0, "started": [], "gone": False}
    steps = [
        {"action": "click", "selector": "#signin"},
        {"action": "extract_table", "selector": "#rules", "name": "rules"},
    ]

    @app.post("/v1/record")
    async def start(body: dict[str, Any]) -> dict[str, Any]:
        calls["started"].append(body)
        calls["get"] = 0
        return {"id": f"wrk{len(calls['started'])}"}

    @app.get("/v1/record/{rid}")
    async def get(rid: str) -> Any:
        if calls["gone"]:
            return JSONResponse({"detail": "no such recording"}, status_code=404)
        calls["get"] += 1
        return {
            "url": "http://fw.test/rules",
            "title": "Rules",
            "steps": steps[: min(calls["get"], 2)],
            "blocked_hosts": [],
            "viewport": {"width": 1280, "height": 800},
        }

    @app.post("/v1/record/{rid}/finish")
    async def finish(rid: str) -> dict[str, Any]:
        return {"flow": {"steps": steps}, "blocked_hosts": []}

    app.state.calls = calls
    return app


def recording_ids(live: LiveServer) -> list[str]:
    """Open recordings (the server keeps them as settings rows)."""
    from sqlalchemy import select
    from z_server.db.models import Setting

    async def run() -> list[str]:
        async with live.state.sessions() as db:
            rows = (await db.execute(select(Setting.key).where(Setting.key.startswith("browser_rec:")))).scalars()
            return [key.removeprefix("browser_rec:") for key in rows]

    return list(live.call(run))


@pytest.fixture
def recorder() -> Iterator[FakeServer]:
    with FakeServer(fake_recorder()) as srv:
        yield srv


@pytest.fixture
def live_browser(
    database_url: str, oidc: FakeGitLabOIDC, tmp_path: Path, recorder: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> Iterator[LiveServer]:
    for key, value in {
        "FWCONSOLE_URL": "http://127.0.0.1:9/fw",
        "FWCONSOLE_READ_USER": "z-read",
        "FWCONSOLE_READ_PASSWORD": "ro-" + "fwconsole",
        "FWCONSOLE_WRITE_USER": "z-write",
        "FWCONSOLE_WRITE_PASSWORD": "rw-" + "fwconsole",
    }.items():
        monkeypatch.setenv(key, value)
    oidc.configure(device_interval=1)
    server = serve(
        database_url, base_config(oidc.issuer), tmp_path, browser_url=recorder.url, browser_token=WORKER_TOKEN
    )
    server.seed()
    yield server
    server.stop()


def test_connectors_record_browser(
    live_browser: LiveServer, oidc: FakeGitLabOIDC, tmp_path: Path, recorder: FakeServer, monkeypatch: Any
) -> None:
    from z_cli.commands import catalog

    monkeypatch.setattr(catalog, "RECORD_POLL_S", 0.01)
    alice = Z(live_browser.url, tmp_path / "home", oidc).as_user("alice")

    started = alice("connectors", "record-browser", "fwconsole", "--yes")
    assert started.exit_code == 0, started.output
    text = plain(started.output)
    rec_id = re.search(r"Recording (brec_\w+) started on fwconsole \(read account\)", text)
    assert rec_id, text
    assert f"http://127.0.0.1/connectors/record/{rec_id.group(1)}" in text  # the server's viewer URL
    assert recorder.app.state.calls["started"][0]["start_url"] == "login"  # the flows' start page

    # the person records in the web app while the CLI follows, then finishes there
    calls = recorder.app.state.calls
    before = set(recording_ids(live_browser))
    finished: list[int] = []

    def web_app_finishes() -> None:
        deadline = time.monotonic() + 20
        while calls["get"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        [rec] = [r for r in recording_ids(live_browser) if r not in before]
        with live_browser.http(alice.token()) as http:
            finished.append(http.post(f"/api/v1/connectors/recordings/{rec}/finish").status_code)

    web = threading.Thread(target=web_app_finishes)
    web.start()
    followed = alice(
        "connectors",
        "record-browser",
        "fwconsole",
        "--name",
        "rules overview",
        "--follow",
        "--yes",
        env={"Z_WEB_URL": "http://web.example.test"},
    )
    web.join(20)
    assert finished == [200] and followed.exit_code == 0, followed.output
    text = plain(followed.output)
    assert re.search(r"http://web\.example\.test/connectors/record/brec_\w+", text), text  # the web app's address
    assert "1  click #signin" in text and "2  extract_table #rules → rules" in text
    assert text.count("click #signin") == 1  # each step once
    assert re.search(r"Recording brec_\w+ ended after 2 steps: finished or cancelled", text), text

    # the worker closed the recording (idle, or restarted): the CLI says so and stops
    calls["gone"] = True
    closed = alice("connectors", "record-browser", "fwconsole", "--follow", "--yes", "--json")
    assert closed.exit_code == 0, closed.output
    ended = json.loads(closed.output)
    assert ended["recording"]["id"].startswith("brec_") and ended["state"] is None
    assert "ended on the browser worker" in ended["ended"]

    with live_browser.http(alice.token()) as http:
        events = http.get("/api/v1/audit", params={"action": "connector.recording_started"}).json()["items"]
    assert len(events) == 3  # each start is audited

    not_browser = alice("connectors", "record-browser", "jira", "--yes")
    assert not_browser.exit_code == 2 and "only browser connectors" in plain(not_browser.output)
    assert alice("connectors", "record-browser", "fwconsole", "--purpose", "admin", "--yes").exit_code == 2
    assert alice("connectors", "record-browser", "fwconsole").exit_code == 2  # no terminal: needs --yes
