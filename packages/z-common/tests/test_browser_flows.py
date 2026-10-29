from __future__ import annotations

import pytest
from pydantic import ValidationError
from z_common.connectors.browser import (
    FlowError,
    check_flow,
    connector_problems,
    host_allowed,
    hosts_for,
    render,
    resolve_url,
)
from z_common.connectors.operations import flow_ref, materialize
from z_common.models.manifests import BrowserFlow

LOGIN = [
    {"action": "fill", "selector": "#u", "value": "{{auth.username}}"},
    {"action": "fill", "selector": "#p", "value": "{{auth.password}}"},
    {"action": "click", "selector": "#go"},
]


def test_check_flow_returns_the_arguments_used() -> None:
    flow = {
        "start_url": "login?next={{args.page}}",
        "steps": [
            *LOGIN,
            {"action": "goto", "url": "x?q={{ args.q }}"},
            {"action": "fill", "selector": "#f", "value": "{{args.text}}"},
        ],
    }
    assert check_flow(flow) == {"page", "q", "text"}


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"action": "click", "selector": "#row-{{args.q}}"}, "args.q can only be used"),
        ({"action": "goto", "url": "x?p={{auth.password}}"}, "auth.password can only be typed"),
        ({"action": "assert_text", "value": "{{auth.username}}"}, "auth.username can only be typed"),
        ({"action": "fill", "selector": "#a", "value": "{{auth.token}}"}, "unknown credential field auth.token"),
        ({"action": "fill", "selector": "#a", "value": "{{env.HOME}}"}, "unknown placeholder"),
        ({"action": "click"}, "needs selector"),
        ({"action": "extract_attribute", "selector": "a", "name": "x"}, "needs attribute"),
    ],
)
def test_check_flow_refuses(step: dict[str, str], message: str) -> None:
    with pytest.raises(FlowError, match=message):
        check_flow({"steps": [step]})


def test_duplicate_outputs_and_credentials_in_start_url() -> None:
    with pytest.raises(FlowError, match="extracted twice"):
        check_flow(
            {
                "steps": [
                    {"action": "extract_text", "selector": "a", "name": "x"},
                    {"action": "extract_text", "selector": "b", "name": "x"},
                ]
            }
        )
    with pytest.raises(FlowError, match="start_url cannot contain a credential"):
        check_flow({"start_url": "x?u={{auth.username}}", "steps": [{"action": "screenshot"}]})


def test_model_validates_flows() -> None:
    BrowserFlow.model_validate({"steps": LOGIN})
    with pytest.raises(ValidationError, match="can only be used"):
        BrowserFlow.model_validate({"steps": [{"action": "click", "selector": "{{args.x}}"}]})
    with pytest.raises(ValidationError):
        BrowserFlow.model_validate({"steps": []})


def test_render_quotes_url_arguments_and_fills_credentials() -> None:
    assert render("q={{args.q}}", {"q": "a b&c=/"}, in_url=True) == "q=a%20b%26c%3D%2F"
    assert render("{{args.q}}", {"q": "a b"}) == "a b"
    assert render("{{args.on}}", {"on": True}) == "true"
    assert render("{{auth.password}}", {}, {"password": "pw"}) == "pw"
    with pytest.raises(FlowError, match="missing argument q"):
        render("{{args.q}}", {})
    with pytest.raises(FlowError, match="no password"):
        render("{{auth.password}}", {}, {})


def test_hosts() -> None:
    hosts = hosts_for("https://Console.example.internal/app", ["*.cdn.example.net", "static.example.org"])
    assert hosts == ["console.example.internal", "*.cdn.example.net", "static.example.org"]
    assert host_allowed("https://console.example.internal/x", hosts)
    assert host_allowed("https://a.cdn.example.net/lib.js", hosts)
    assert not host_allowed("https://cdn.example.net.evil.test/", hosts)
    assert not host_allowed("https://evil.test/?u=console.example.internal", hosts)
    assert not host_allowed("file:///etc/passwd", hosts)
    assert not host_allowed("javascript:alert(1)", hosts)
    assert resolve_url("https://h/app", "login") == "https://h/app/login"
    assert resolve_url("https://h/app", "https://h/other") == "https://h/other"


def test_connector_problems_and_flow_digest() -> None:
    manifest = {
        "name": "fw",
        "kind": "browser",
        "declared": [
            {"id": "rules.search", "flow": "search", "input_schema": {"properties": {}}},
            {"id": "rules.other", "flow": "nope"},
            {"id": "rules.noflow"},
        ],
        "flows": {"search": {"steps": [{"action": "goto", "url": "r?q={{args.q}}"}]}},
    }
    assert connector_problems(manifest) == [
        "operation rules.search: flow search uses undeclared arguments q",
        "operation rules.other: flow 'nope' is not defined in flows",
        "operation rules.noflow: browser operations name a flow",
    ]
    assert connector_problems({"kind": "rest", "flows": {"a": {}}}) == ["flows are only used by browser connectors"]
    before = flow_ref(manifest, "search")
    assert before.startswith("flow:search@")
    [op, *_] = materialize(manifest)
    assert op.source_ref == before
    manifest["flows"]["search"]["steps"].append({"action": "screenshot"})
    assert flow_ref(manifest, "search") != before
