from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml

from tests.fakes.fake_prometheus import FakePrometheus

ROOT = Path(__file__).resolve().parents[3]
QUERIES: dict[str, str] = yaml.safe_load((ROOT / "config/z.example.yaml").read_text())["inference"]["prometheus"][
    "queries"
]


@pytest.fixture(scope="module")
def prom() -> Iterator[FakePrometheus]:
    with FakePrometheus() as server:
        yield server


@pytest.fixture(autouse=True)
def _reset(prom: FakePrometheus) -> None:
    prom.reset()


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_every_configured_query_has_data(prom: FakePrometheus, name: str) -> None:
    query = QUERIES[name]
    r = httpx.get(f"{prom.url}/api/v1/query", params={"query": query}).json()
    assert r["status"] == "success" and r["data"]["resultType"] == "vector"
    result = r["data"]["result"]
    assert result, name
    for sample in result:
        float(sample["value"][1])
    if "by (model_name)" in query:
        assert {s["metric"]["model_name"] for s in result} == {"z-heavy", "z-fast", "z-judge"}
    if "by (Hostname)" in query:
        assert {s["metric"]["Hostname"] for s in result} == {"gpu-node-1", "gpu-node-2", "gpu-node-3"}


def test_ratios_are_fractions(prom: FakePrometheus) -> None:
    for name in ("prefix_cache_hit", "gpu_mem_used_ratio", "kv_cache_usage"):
        r = httpx.get(f"{prom.url}/api/v1/query", params={"query": QUERIES[name]}).json()
        assert all(0 <= float(s["value"][1]) <= 1 for s in r["data"]["result"])


def test_query_range_post_and_unknown(prom: FakePrometheus) -> None:
    form = {"query": QUERIES["requests_running"], "start": "1760000000", "end": "1760000600", "step": "60s"}
    r = httpx.post(f"{prom.url}/api/v1/query_range", data=form).json()
    assert r["data"]["resultType"] == "matrix"
    assert len(r["data"]["result"]) == 3 and len(r["data"]["result"][0]["values"]) == 11
    other = httpx.get(f"{prom.url}/api/v1/query", params={"query": "kube_deployment_status_replicas_available"}).json()
    assert len(other["data"]["result"]) == 3
    unknown = httpx.get(f"{prom.url}/api/v1/query", params={"query": "made_up_metric"}).json()
    assert unknown["status"] == "success" and unknown["data"]["result"] == []
    assert httpx.get(f"{prom.url}/api/v1/query").status_code == 400


def test_overrides_and_outage(prom: FakePrometheus) -> None:
    prom.set_values({"requests_waiting": {"z-fast": 42}})
    r = httpx.get(f"{prom.url}/api/v1/query", params={"query": QUERIES["requests_waiting"]}).json()
    values = {s["metric"]["model_name"]: s["value"][1] for s in r["data"]["result"]}
    assert values["z-fast"] == "42" and values["z-heavy"] == "1"
    prom.down()
    assert httpx.get(f"{prom.url}/api/v1/query", params={"query": "up"}).status_code == 503
    prom.down(False)
    assert httpx.get(f"{prom.url}/api/v1/query", params={"query": "up"}).status_code == 200
