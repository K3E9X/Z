"""cli connectors (ADR 0025): argv rendering without a shell, flag injection refused, credentials only in env."""

from __future__ import annotations

import pytest
from z_common.connectors.cli import CliError, connector_problems, render_argv, render_env

IMAGE = "registry.example.internal/kubectl@sha256:" + "a" * 64


def test_argv_placeholders_and_optional_arguments() -> None:
    command = ["get", "pods", "-n", "{{args.namespace}}", "--selector={{args.selector}}", "{{args.name}}"]
    assert render_argv(command, {"namespace": "z", "selector": "app=web", "name": "web-1"}) == [
        "get",
        "pods",
        "-n",
        "z",
        "--selector=app=web",
        "web-1",
    ]
    assert render_argv(["logs", "{{args.pod}}", "{{args.container}}"], {"pod": "p", "container": None}) == ["logs", "p"]
    with pytest.raises(CliError, match="missing argument selector"):
        render_argv(["--selector={{args.selector}}"], {})


@pytest.mark.parametrize(
    "value", ["--kubeconfig=/etc/x", "-A", "a\nb", "x\x00", {"a": 1}, ["x"], -1, -0.5, float("-inf"), float("nan")]
)
def test_values_cannot_become_flags_or_structure(value: object) -> None:
    with pytest.raises(CliError):
        render_argv(["get", "{{args.name}}"], {"name": value})


def test_scalars_are_rendered() -> None:
    assert render_argv(["--tail={{args.n}}", "{{args.follow}}"], {"n": 50, "follow": False}) == ["--tail=50", "false"]


def test_env_takes_credentials_only() -> None:
    env = render_env({"KUBE_TOKEN": "{{auth.secret}}", "MODE": "ro"}, {"secret": "t0k"})
    assert env == {"KUBE_TOKEN": "t0k", "MODE": "ro"}
    with pytest.raises(CliError, match="no password"):
        render_env({"P": "{{auth.password}}"}, {"secret": "x"})


def test_static_checks() -> None:
    manifest = {
        "name": "k8s",
        "kind": "cli",
        "cli": {"image": IMAGE, "env": {"KUBE_TOKEN": "{{auth.secret}}", "bad-name": "x", "LEAK": "{{args.ns}}"}},
        "declared": [
            {"id": "pods.list", "command": ["get", "pods", "-n", "{{args.ns}}"], "input_schema": {"properties": {}}},
            {"id": "secrets.get", "command": ["--token={{auth.secret}}"]},
            {"id": "nodes.list"},
        ],
    }
    problems = connector_problems(manifest)
    assert "cli.env bad-name: environment names are upper case letters, digits and _" in problems
    assert any(p.startswith("cli.env LEAK: only") for p in problems)
    assert "operation pods.list: the command uses undeclared arguments ns" in problems
    assert "operation secrets.get: credentials go in cli.env, never in the command" in problems
    assert "operation nodes.list: cli operations declare `command` (a list of strings)" in problems
    assert connector_problems({**manifest, "cli": {"image": "kubectl:latest"}, "declared": []}) == [
        "cli.image must be pinned by digest (name@sha256:...)"
    ]


def test_an_optional_value_after_a_flag_must_be_attached() -> None:
    def problems(command: list[str], required: list[str]) -> list[str]:
        op = {"id": "x.get", "command": command, "input_schema": {"properties": {"o": {}}, "required": required}}
        return connector_problems({"kind": "cli", "cli": {"image": IMAGE}, "declared": [op]})

    swallowing = problems(["tool", "--output", "{{args.o}}", "--dry-run"], [])
    assert any("optional argument o follows the flag --output" in p for p in swallowing)
    assert problems(["tool", "--output={{args.o}}", "--dry-run"], []) == []
    assert problems(["tool", "--output", "{{args.o}}"], ["o"]) == []
