"""Templates and `when` expressions are safe and behave as documented."""

from __future__ import annotations

import pytest
from z_server.missions.expressions import ExpressionError, evaluate, truthy
from z_server.missions.templates import TemplateFailure, render, render_value

CTX = {
    "inputs": {"product": "infra", "min_severity": "critical"},
    "item": {"id": "1001", "repo": "infra/app"},
    "steps": {"pull": {"output": {"findings": [1, 2]}}},
}


def test_render_prompt() -> None:
    assert render("Triage {{ item.id }} for {{ inputs.product }}", CTX) == "Triage 1001 for infra"


def test_missing_value_is_an_error() -> None:
    with pytest.raises(TemplateFailure):
        render("{{ inputs.nope }}", CTX)


@pytest.mark.parametrize(
    "template",
    ["{{ ''.__class__.__mro__ }}", "{{ cycler.__init__.__globals__ }}", "{% for x in range(10**9) %}{% endfor %}"],
)
def test_sandbox_refuses_internals(template: str) -> None:
    with pytest.raises(TemplateFailure):
        render(template, CTX)


def test_render_value_keeps_types() -> None:
    value = {
        "product": "{{ inputs.product }}",
        "list": "{{ steps.pull.output.findings }}",
        "text": "repo {{ item.repo }}",
    }
    assert render_value(value, CTX) == {"product": "infra", "list": [1, 2], "text": "repo infra/app"}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("steps.review.output.verdict == 'approve'", True),
        ("steps.review.output.verdict != 'approve'", False),
        ("item.severity in ['critical', 'high']", True),
        ("not item.missing", True),
        ("item.score >= 7 and item.severity == 'critical'", True),
        ("item.score < 7 or false", False),
        ("steps.review.output.blockers[0] == 'x'", True),
        ("steps.review.output.blockers[5] == 'x'", False),
    ],
)
def test_expressions(expression: str, expected: bool) -> None:
    names = {
        "steps": {"review": {"output": {"verdict": "approve", "blockers": ["x"]}}},
        "item": {"severity": "critical", "score": 9},
        "inputs": {},
    }
    assert truthy(expression, names) is expected


def test_step_for() -> None:
    reviews = {"k1": {"verdict": "approve"}, "k2": {"verdict": "changes_requested"}}
    names = {"item": {"_key": "k1"}}
    assert truthy(
        "review_for(item).verdict == 'approve'",
        names,
        lambda step, item: reviews[item["_key"]] if step == "review" else None,
    )
    names = {"item": {"_key": "k2"}}
    assert not truthy("review_for(item).verdict == 'approve'", names, lambda step, item: reviews[item["_key"]])


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "item.__class__",
        "open('x')",
        "[x for x in item]",
        "lambda: 1",
        "item.id + 1",
        "unknown_name == 1",
        "1 if True else 2",
    ],
)
def test_expressions_refuse_unsafe_constructs(expression: str) -> None:
    with pytest.raises(ExpressionError):
        evaluate(expression, {"item": {"id": 1}})


def test_expression_length_limit() -> None:
    with pytest.raises(ExpressionError):
        evaluate("1 == 1 and " * 100 + "true", {})
