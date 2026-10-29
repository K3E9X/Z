"""The documentation in the web app: every signed-in user reads the pages shipped with the running version, links
between pages point at /docs/<slug>, and nothing outside the catalog can be read."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import sign_in_as


def test_signed_in_users_read_the_documentation(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC) -> None:
    client = make_client()
    assert client.get("/api/v1/docs").status_code == 401  # never public
    sign_in_as(client, oidc, "dave")  # auditor: no admin permission needed
    pages = client.get("/api/v1/docs").json()
    by_slug = {p["slug"]: p for p in pages}
    assert (
        by_slug["07-security"]["section"] == "specification" and by_slug["07-security"]["path"] == "docs/07-security.md"
    )
    assert by_slug["deploy"]["section"] == "operations" and by_slug["cli"]["section"] == "guide"
    assert by_slug["14-release-and-changes"]["section"] == "operations"
    assert by_slug["adr-0028-google-workspace-connectors"]["section"] == "decision"
    assert not {"WORKLOG", "BUILD"} & {p["title"] for p in pages} and "adr-template" not in by_slug

    adr = client.get("/api/v1/docs/adr-0028-google-workspace-connectors").json()
    assert adr["title"].startswith("ADR 0028") and adr["markdown"].startswith("# ADR 0028")
    index = client.get("/api/v1/docs/05-connectors").json()["markdown"]
    assert "(/docs/" in index or "](" not in index  # links between pages are rewritten when present

    for bad in ("../../etc/passwd", "WORKLOG", "unknown-page", "%2e%2e%2fsettings.py"):
        assert client.get(f"/api/v1/docs/{bad}").status_code == 404, bad


def test_links_between_pages_become_app_routes() -> None:
    from pathlib import Path

    from z_server import docs_catalog

    base = Path(__file__).resolve().parents[2]
    docs_catalog.pages.cache_clear()
    page, _ = docs_catalog.read("adr-0028-google-workspace-connectors", base)
    assert page.section == "decision"
    sample = docs_catalog._links(
        "See [security](../07-security.md#4-docker-compose-hardening), [0027](0027-inferx-key-from-z.md), "
        "[code](../../server/src/z_server/app.py) and [web](https://example.com/x.md).",
        page,
        {p.path: p.slug for p in docs_catalog.pages(base)},
    )
    assert "(/docs/07-security#4-docker-compose-hardening)" in sample
    assert "(/docs/adr-0027-inferx-key-from-z)" in sample
    assert "(../../server/src/z_server/app.py)" in sample and "(https://example.com/x.md)" in sample
