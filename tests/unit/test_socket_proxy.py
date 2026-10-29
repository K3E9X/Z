"""z-socket-proxy: the policy accepts what the docker executor sends and refuses every escalation; against the
real Docker daemon the executor works through the proxy and cannot start a privileged container."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from z_server.executors.docker import DockerExecutor

PATH = Path(__file__).resolve().parents[2] / "deploy/images/z-socket-proxy/socket_proxy.py"
spec = importlib.util.spec_from_file_location("socket_proxy", PATH)
assert spec and spec.loader
sp = importlib.util.module_from_spec(spec)
sys.modules["socket_proxy"] = sp  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(sp)

IMAGE = "z-runner@sha256:" + "a" * 64
POLICY = sp.Policy(images={IMAGE})
QUERY = {"name": ["z-job-job-01abc"]}


def executor_body() -> dict[str, Any]:
    return DockerExecutor(base_url="http://proxy", image=IMAGE).container_spec("job_01ABC", {"Z_JOB_ID": "job_01ABC"})


def test_the_executor_spec_passes() -> None:
    POLICY.check_create(QUERY, executor_body())


ESCALATIONS = [
    ("privileged", lambda b: b["HostConfig"].update(Privileged=True)),
    ("bind mount", lambda b: b["HostConfig"].update(Binds=["/:/host"])),
    ("mounts", lambda b: b["HostConfig"].update(Mounts=[{"Type": "bind", "Source": "/", "Target": "/h"}])),
    ("host pid", lambda b: b["HostConfig"].update(PidMode="host")),
    ("host network", lambda b: b["HostConfig"].update(NetworkMode="host")),
    ("other network", lambda b: b["HostConfig"].update(NetworkMode="z_core")),
    ("cap add", lambda b: b["HostConfig"].update(CapAdd=["SYS_ADMIN"])),
    ("keep caps", lambda b: b["HostConfig"].update(CapDrop=[])),
    ("seccomp off", lambda b: b["HostConfig"].update(SecurityOpt=["no-new-privileges:true", "seccomp=unconfined"])),
    ("writable root", lambda b: b["HostConfig"].update(ReadonlyRootfs=False)),
    ("devices", lambda b: b["HostConfig"].update(Devices=[{"PathOnHost": "/dev/kvm"}])),
    ("no memory limit", lambda b: b["HostConfig"].pop("Memory")),
    ("huge pids", lambda b: b["HostConfig"].update(PidsLimit=10**9)),
    ("tmpfs elsewhere", lambda b: b["HostConfig"]["Tmpfs"].update({"/etc": "rw"})),
    ("tmpfs suid", lambda b: b["HostConfig"]["Tmpfs"].update({"/tmp": "rw,size=1m"})),
    ("runtime", lambda b: b["HostConfig"].update(Runtime="nvidia")),
    ("other image", lambda b: b.update(Image="alpine:latest")),
    ("root user", lambda b: b.update(User="0:0")),
    ("entrypoint", lambda b: b.update(Entrypoint=["/bin/sh"])),
    ("command", lambda b: b.update(Cmd=["sh", "-c", "id"])),
    ("volumes", lambda b: b.update(Volumes={"/data": {}})),
    ("no label", lambda b: b.update(Labels={})),
]


@pytest.mark.parametrize(("name", "mutate"), ESCALATIONS, ids=[e[0] for e in ESCALATIONS])
def test_escalations_are_refused(name: str, mutate: Any) -> None:
    body = copy.deepcopy(executor_body())
    mutate(body)
    with pytest.raises(sp.Refused):
        POLICY.check_create(QUERY, body)


def test_names_and_routes() -> None:
    with pytest.raises(sp.Refused):
        POLICY.check_create({"name": ["anything"]}, executor_body())
    assert sp.route("POST", "/v1.43/containers/create?name=z-job-x")[0] == "create"
    assert sp.route("POST", "/v1.43/containers/abc/start")[0] == "container"
    assert sp.route("DELETE", "/v1.43/containers/abc?force=true")[0] == "container"
    for method, path in [
        ("POST", "/v1.43/containers/abc/exec"),
        ("POST", "/v1.43/exec/abc/start"),
        ("POST", "/v1.43/images/create?fromImage=alpine"),
        ("GET", "/v1.43/containers/json"),
        ("POST", "/v1.43/volumes/create"),
        ("POST", "/v1.43/networks/create"),
        ("POST", "/v1.43/containers/abc/archive"),
        ("PUT", "/v1.43/containers/abc/archive"),
        ("POST", "/v1.43/build"),
        ("GET", "/v1.43/containers/abc/attach/ws"),
    ]:
        assert sp.route(method, path)[0] == "refused", (method, path)


# ---------------------------------------------------------------- against the real Docker daemon

DOCKER_SOCKET = "/var/run/docker.sock"


def _docker(method: str, path: str, **kw: Any) -> httpx.Response:
    with httpx.Client(transport=httpx.HTTPTransport(uds=DOCKER_SOCKET), base_url="http://docker") as c:
        return c.request(method, path, **kw)


def _local_image() -> str | None:
    images = _docker("GET", "/v1.43/images/json").json()
    for image in images:
        for tag in image.get("RepoTags") or []:
            if tag == "z-server:local":  # any local image with an ENTRYPOINT: `<entrypoint> --job <id>` starts
                return str(image["Id"])
    return None


@pytest.fixture
def proxy() -> Iterator[tuple[str, str]]:
    if not os.path.exists(DOCKER_SOCKET):
        pytest.skip("no Docker daemon")
    image = _local_image()
    if image is None:
        pytest.skip("z-server:local not built")
    net = "z_test_runners"
    _docker("POST", "/v1.43/networks/create", json={"Name": net, "Internal": True})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy = sp.Policy(images={image}, network=net)
    server = ThreadingHTTPServer(("127.0.0.1", port), sp.make_handler(policy, sp.Upstream(DOCKER_SOCKET)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", image
    finally:
        server.shutdown()
        _docker("DELETE", f"/v1.43/networks/{net}")


def test_executor_through_the_proxy_on_real_docker(proxy: tuple[str, str]) -> None:
    url, image = proxy
    executor = DockerExecutor(base_url=url, image=image, network="z_test_runners")

    async def scenario() -> tuple[str, dict[str, Any]]:
        ref = await executor.launch("job_01PROXYTEST", {"Z_JOB_ID": "job_01PROXYTEST"})
        inspect = _docker("GET", f"/v1.43/containers/{ref}/json").json()
        await executor.stop(ref)
        return ref, inspect

    ref, inspect = asyncio.run(scenario())
    host = inspect["HostConfig"]
    assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"] and host["Privileged"] is False
    assert inspect["Config"]["User"] == "10001:10001" and host["NetworkMode"] == "z_test_runners"
    assert _docker("GET", f"/v1.43/containers/{ref}/json").status_code == 404  # removed

    with httpx.Client(base_url=url) as c:
        body = executor.container_spec("job_01EVIL", {})
        body["HostConfig"]["Privileged"] = True
        body["HostConfig"]["Binds"] = ["/:/host"]
        refused = c.post("/v1.43/containers/create", params={"name": "z-job-evil"}, json=body)
        assert refused.status_code == 403 and "z-socket-proxy" in refused.json()["message"]
        assert c.get("/v1.43/containers/json").status_code == 403  # cannot even list other containers
        assert c.post("/v1.43/images/create", params={"fromImage": "alpine"}).status_code == 403
        assert c.get("/_ping").status_code == 200


# ---------------------------------------------------------------- tool profile (cli connectors, ADR 0025)

TOOL = "registry.example.internal/kubectl@sha256:" + "b" * 64
TOOL_POLICY = sp.Policy(images={IMAGE}, tool_images={TOOL})
TOOL_QUERY = {"name": ["z-tool-0123456789abcdef"]}


def tool_body() -> dict[str, Any]:
    executor = DockerExecutor(base_url="http://proxy", image=IMAGE)
    return executor.tool_spec("0123456789abcdef", TOOL, ["get", "pods", "-o", "json"], {"KUBE_TOKEN": "t"})


def test_the_tool_spec_passes() -> None:
    TOOL_POLICY.check_create(TOOL_QUERY, tool_body())


TOOL_ESCALATIONS = [
    ("runner image as tool", lambda b: b.update(Image=IMAGE)),
    ("unlisted tool image", lambda b: b.update(Image="alpine@sha256:" + "c" * 64)),
    ("root user", lambda b: b.update(User="0:0")),
    ("runner user", lambda b: b.update(User="10001:10001")),
    ("command not a list", lambda b: b.update(Cmd="get pods")),
    ("command too long", lambda b: b.update(Cmd=["x"] * 65)),
    ("empty command", lambda b: b.update(Cmd=[])),
    ("workspace tmpfs", lambda b: b["HostConfig"]["Tmpfs"].update({"/workspace": "rw,nosuid,nodev"})),
    ("no tool label", lambda b: b.update(Labels={sp.LABEL: "job_1"})),
    ("privileged", lambda b: b["HostConfig"].update(Privileged=True)),
    ("host network", lambda b: b["HostConfig"].update(NetworkMode="host")),
    ("entrypoint", lambda b: b.update(Entrypoint=["/bin/sh"])),
]


@pytest.mark.parametrize(("name", "mutate"), TOOL_ESCALATIONS, ids=[e[0] for e in TOOL_ESCALATIONS])
def test_tool_escalations_are_refused(name: str, mutate: Any) -> None:
    body = copy.deepcopy(tool_body())
    mutate(body)
    with pytest.raises(sp.Refused):
        TOOL_POLICY.check_create(TOOL_QUERY, body)


def test_no_tool_images_means_no_tool_containers() -> None:
    with pytest.raises(sp.Refused, match="not an allowed tool image"):
        POLICY.check_create(TOOL_QUERY, tool_body())
    with pytest.raises(sp.Refused):  # a runner name cannot carry a tool body either
        TOOL_POLICY.check_create(QUERY, tool_body())


def _local_tool_image() -> str | None:
    for image in _docker("GET", "/v1.43/images/json").json():
        for tag in image.get("RepoTags") or []:
            if tag.startswith("python:3.12-slim"):  # no ENTRYPOINT: the command is the whole argv
                return str(image["Id"])
    return None


def test_cli_tool_runs_through_the_proxy_on_real_docker(proxy: tuple[str, str]) -> None:
    _url, runner_image = proxy
    tool = _local_tool_image()
    if tool is None:
        pytest.skip("no local python:3.12-slim image")
    # the fixture's proxy only knows the runner image: start a second one that also allows the tool image
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy = sp.Policy(images={runner_image}, tool_images={tool}, network="z_test_runners")
    server = ThreadingHTTPServer(("127.0.0.1", port), sp.make_handler(policy, sp.Upstream(DOCKER_SOCKET)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    executor = DockerExecutor(base_url=f"http://127.0.0.1:{port}", image=runner_image, network="z_test_runners")
    code = "import json,os,sys; print(json.dumps({'uid': os.getuid(), 'tok': os.environ['T']})); print('diag', file=sys.stderr)"
    try:
        ok = asyncio.run(executor.run_tool(tool, ["python3", "-c", code], {"T": "tok"}, 60))
        failed = asyncio.run(executor.run_tool(tool, ["python3", "-c", "import sys; sys.exit(3)"], {}, 60))
        slow = asyncio.run(executor.run_tool(tool, ["python3", "-c", "import time; time.sleep(60)"], {}, 3))
        loud = "import sys; sys.stdout.write('x' * 3_000_000); sys.stderr.write('e' * 200_000)"
        big = asyncio.run(executor.run_tool(tool, ["python3", "-c", loud], {}, 60))
    finally:
        server.shutdown()
    assert ok.exit_code == 0 and json.loads(ok.stdout) == {"uid": 65534, "tok": "tok"} and ok.stderr.strip() == "diag"
    assert failed.exit_code == 3 and not failed.timed_out
    assert slow.timed_out
    # bounded: at most MAX_TOOL_OUTPUT of stdout and the end of stderr reach z-server
    assert big.truncated and len(big.stdout) == 1024 * 1024 and set(big.stdout) == {"x"}
    assert len(big.stderr) == 4000
    leftovers = _docker("GET", "/v1.43/containers/json", params={"all": 1}).json()
    assert not [c for c in leftovers if any(n.startswith("/z-tool-") for n in c.get("Names") or [])]
