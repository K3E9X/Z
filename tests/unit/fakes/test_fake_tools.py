from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from tests.fakes.fake_tools import FakeTools

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _reset(tools: FakeTools) -> None:
    tools.reset()


def h(tools: FakeTools, tool: str, kind: str = "read", scheme: str = "Bearer") -> dict[str, str]:
    return {"Authorization": f"{scheme} {tools.token(tool, kind)}"}


def from_path(method: str, path: str) -> str:
    """docs/03 §4 `from_path` rule, as the gateway will implement it."""
    parts = [p for p in path.strip("/").split("/") if p and p not in ("api", "v2")]
    resource = parts[0]
    has_id = len(parts) > 1 and parts[1].startswith("{")
    if method == "get":
        return f"{resource}.{'get' if has_id else 'list'}"
    if method == "post" and len(parts) > 2:
        return f"{resource}.{parts[2]}"
    return f"{resource}.{ {'post': 'create', 'put': 'update', 'patch': 'update', 'delete': 'delete'}[method] }"


def test_defectdojo_spec_maps_to_connector_operations(tools: FakeTools) -> None:
    spec = httpx.get(f"{tools.url}/defectdojo/api/v2/oa3/schema/", params={"format": "json"}).json()
    assert spec["openapi"].startswith("3.0")
    ops = {from_path(m, p) for p, item in spec["paths"].items() for m in item}
    assert ops == {"findings.list", "findings.get", "products.list", "products.get", "engagements.get"}
    assert spec["servers"][0]["url"].endswith("/defectdojo")


def test_defectdojo_findings_filters_and_paging(tools: FakeTools) -> None:
    url = f"{tools.url}/defectdojo/api/v2/findings/"
    auth = h(tools, "defectdojo", scheme="Token")
    page = httpx.get(url, params={"product_name": "infra-boutiques", "limit": 5}, headers=auth).json()
    assert page["count"] == 20 and len(page["results"]) == 5 and page["next"] and page["previous"] is None
    nxt = httpx.get(page["next"], headers=auth).json()
    assert nxt["results"][0]["id"] == page["results"][-1]["id"] + 1
    crit = httpx.get(
        url, params={"product_name": "infra-boutiques", "severity": "Critical", "limit": 100}, headers=auth
    ).json()
    assert crit["count"] == 9 and all(f["severity"] == "Critical" for f in crit["results"])
    log4j = httpx.get(url, params={"vulnerability_id": "CVE-2021-44228", "active": "true"}, headers=auth).json()
    assert {f["service"] for f in log4j["results"]} == {"pay-api-01", "pay-api-02", "pay-worker-01"}
    assert all(not k.startswith("_") for k in log4j["results"][0])
    one = httpx.get(f"{url}1001/", headers=auth).json()
    assert one["cve"] == "CVE-2021-44228"
    assert httpx.get(f"{url}9999/", headers=auth).status_code == 404
    assert httpx.get(f"{tools.url}/defectdojo/api/v2/engagements/10/", headers=auth).json()["product"] == 1
    # Bearer is not DefectDojo's scheme.
    assert httpx.get(url, headers=h(tools, "defectdojo")).status_code == 401


def test_cmdb_assets(tools: FakeTools) -> None:
    auth = h(tools, "cmdb")
    asset = httpx.get(f"{tools.url}/cmdb/assets/pay-api-01", headers=auth).json()
    assert asset["owner_team"] == "Payments Platform" and asset["exposure"] == "internet"
    assert httpx.get(f"{tools.url}/cmdb/assets/legacy-batch-07", headers=auth).status_code == 404
    found = httpx.get(f"{tools.url}/cmdb/assets", params={"ip": "10.30.1.11"}, headers=auth).json()
    assert [a["name"] for a in found["results"]] == ["web-front-01"]
    teams = {a["owner_team"] for a in httpx.get(f"{tools.url}/cmdb/assets", headers=auth).json()["results"]}
    assert teams == {"Payments Platform", "Web Platform"}
    assert httpx.get(f"{tools.url}/cmdb/assets").status_code == 401


def test_logos_spec_and_token_separation(tools: FakeTools) -> None:
    served = httpx.get(f"{tools.url}/logos/openapi.json").json()
    pack = json.loads((ROOT / "packs/ticketing/connectors/logos.openapi.json").read_text())
    assert served["paths"] == pack["paths"]
    inactive = httpx.get(f"{tools.url}/logos/accounts", params={"inactive_days": 90}, headers=h(tools, "logos")).json()
    assert {a["id"] for a in inactive} == {"acc-004", "acc-005", "acc-006", "acc-008", "acc-009"}
    disable = f"{tools.url}/logos/accounts/acc-004/disable"
    assert httpx.post(disable, headers=h(tools, "logos")).status_code == 403
    assert httpx.post(disable).status_code == 401
    r = httpx.post(disable, headers=h(tools, "logos", "write"))
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert httpx.delete(f"{tools.url}/logos/accounts/acc-005", headers=h(tools, "logos")).status_code == 403
    state = tools.state()
    assert [w["type"] for w in state["writes"]] == ["logos.accounts.disable"]
    assert state["writes"][0]["token_kind"] == "write"
    assert len(state["denied"]) == 3


def test_jira_rest(tools: FakeTools) -> None:
    base = f"{tools.url}/jira/rest/api/2"
    ro, rw = h(tools, "jira"), h(tools, "jira", "write")
    found = httpx.get(
        f"{base}/search", params={"jql": 'project = SECOPS AND text ~ "CVE-2021-44228"'}, headers=ro
    ).json()
    assert [i["key"] for i in found["issues"]] == ["SECOPS-4791"]
    assert httpx.get(f"{base}/search", params={"jql": "nonsense = 1"}, headers=ro).status_code == 400
    fields = {"project": {"key": "SECOPS"}, "summary": "Upgrade log4j-core", "issuetype": {"name": "Task"}}
    assert httpx.post(f"{base}/issue", json={"fields": fields}, headers=ro).status_code == 403
    created = httpx.post(f"{base}/issue", json={"fields": fields}, headers=rw)
    assert created.status_code == 201 and created.json()["key"] == "SECOPS-4811"
    assert httpx.post(f"{base}/issue/SECOPS-4811/comment", json={"body": "ok"}, headers=rw).status_code == 201
    assert httpx.put(f"{base}/issue/SECOPS-4811", json={"fields": {"labels": ["z"]}}, headers=rw).status_code == 204
    issue = httpx.get(f"{base}/issue/SECOPS-4811", headers=ro).json()
    assert issue["fields"]["labels"] == ["z"] and issue["fields"]["comment"]["total"] == 1
    bad = httpx.post(f"{base}/issue", json={"fields": {"project": {"key": "NOPE"}}}, headers=rw)
    assert bad.status_code == 400 and "project" in bad.json()["errors"]
    assert httpx.delete(f"{base}/issue/SECOPS-4811", headers=ro).status_code == 403
    assert httpx.delete(f"{base}/issue/SECOPS-4811", headers=rw).status_code == 204
    types = [w["type"] for w in tools.writes()]
    assert types == ["jira.issues.create", "jira.comments.create", "jira.issues.update", "jira.issues.delete"]


def test_jira_seed_has_duplicate_and_injection(tools: FakeTools) -> None:
    ro = h(tools, "jira")
    dup = httpx.get(f"{tools.url}/jira/rest/api/2/issue/SECOPS-4791", headers=ro).json()
    assert "1001" in dup["fields"]["description"]
    open_issues = httpx.get(
        f"{tools.url}/jira/rest/api/2/search", params={"jql": "project = SECOPS AND statusCategory != Done"}, headers=ro
    ).json()
    assert {i["key"] for i in open_issues["issues"]} == {"SECOPS-4791", "SECOPS-4795", "SECOPS-4810"}
    injected = httpx.get(f"{tools.url}/jira/rest/api/2/issue/SECOPS-4795", headers=ro).json()
    assert re.search("ignore all previous instructions", injected["fields"]["description"], re.I)


def test_confluence_rest(tools: FakeTools) -> None:
    ro = h(tools, "confluence")
    hits = httpx.get(
        f"{tools.url}/confluence/rest/api/content/search",
        params={"cql": 'space = SEC AND text ~ "baseline"'},
        headers=ro,
    ).json()
    assert [p["id"] for p in hits["results"]] == ["10003"]
    page = httpx.get(f"{tools.url}/confluence/rest/api/content/10001", headers=ro).json()
    assert page["body"]["storage"]["value"].startswith("<h1>")
    body = {"type": "page", "title": "New", "space": {"key": "SEC"}, "body": {"storage": {"value": "<p>x</p>"}}}
    assert httpx.post(f"{tools.url}/confluence/rest/api/content", json=body, headers=ro).status_code == 403


def test_gitlab_rest(tools: FakeTools) -> None:
    ro, rw = {"PRIVATE-TOKEN": tools.token("gitlab")}, h(tools, "gitlab", "write")
    api = f"{tools.url}/gitlab/api/v4"
    infra = httpx.get(f"{api}/groups/infra/projects", headers=ro).json()
    assert [p["path_with_namespace"] for p in infra] == [
        "infra/payments-api",
        "infra/checkout-service",
        "infra/inventory-service",
        "infra/ops-scripts",
    ]
    assert infra[0]["http_url_to_repo"] == f"{tools.url}/gitlab/infra/payments-api.git"
    for p in infra[:3]:
        pom = httpx.get(
            f"{api}/projects/{p['id']}/repository/files/pom.xml/raw", params={"ref": "main"}, headers=ro
        ).text
        assert "<version>2.14.1</version>" in pom
    readme = httpx.get(
        f"{api}/projects/infra%2Fops-scripts/repository/files/README.md", params={"ref": "main"}, headers=ro
    ).json()
    assert readme["encoding"] == "base64"
    nested = httpx.get(
        f"{api}/projects/101/repository/files/src%2Fmain%2Fjava%2Ftest%2Fexample%2Finfra%2FApp.java/raw",
        params={"ref": "main"},
        headers=ro,
    )
    assert nested.status_code == 200 and "class App" in nested.text
    changes = httpx.get(f"{api}/projects/101/merge_requests/1/changes", headers=ro).json()
    assert changes["changes"][0]["diff"].startswith("@@")
    # A merge request needs a branch that exists in the repository.
    mr = {"source_branch": "z/not-pushed", "target_branch": "main", "title": "x"}
    assert httpx.post(f"{api}/projects/101/merge_requests", json=mr, headers=ro).status_code == 403
    assert httpx.post(f"{api}/projects/101/merge_requests", json=mr, headers=rw).status_code == 400
    again = {"source_branch": "docs/readme-typo", "target_branch": "main", "title": "dup"}
    assert httpx.post(f"{api}/projects/101/merge_requests", json=again, headers=rw).status_code == 409
    note = httpx.post(f"{api}/projects/101/merge_requests/1/notes", json={"body": "LGTM"}, headers=rw)
    assert note.status_code == 201
    issue = httpx.post(f"{api}/projects/101/issues", json={"title": "Track upgrade"}, headers=rw)
    assert issue.status_code == 201 and issue.json()["iid"] == 1
    assert httpx.put(f"{api}/projects/101/merge_requests/1/merge", headers=ro).status_code == 403
    merged = httpx.put(f"{api}/projects/101/merge_requests/1/merge", headers=rw).json()
    assert merged["state"] == "merged"
    assert httpx.delete(f"{api}/projects/101/repository/branches/docs%2Freadme-typo", headers=rw).status_code == 204
    assert [w["type"] for w in tools.writes()] == [
        "gitlab.notes.create",
        "gitlab.issues.create",
        "gitlab.merge_requests.merge",
        "gitlab.branches.delete",
    ]


def test_seed_and_reset(tools: FakeTools) -> None:
    tools.seed(
        {
            "cmdb": {
                "assets": [
                    {
                        "name": "only-one",
                        "ip": "10.0.0.1",
                        "environment": "dev",
                        "exposure": "isolated",
                        "criticality": 4,
                        "owner_team": "X",
                        "data_class": "C1",
                    }
                ]
            }
        }
    )
    names = [a["name"] for a in httpx.get(f"{tools.url}/cmdb/assets", headers=h(tools, "cmdb")).json()["results"]]
    assert names == ["only-one"]
    tools.reset()
    assert httpx.get(f"{tools.url}/cmdb/assets/pay-api-01", headers=h(tools, "cmdb")).status_code == 200
