"""Invariant 13 on Docker Compose (docs/07 §4): every service of deploy/compose/docker-compose.yml runs with a
read-only root filesystem, all capabilities dropped, no privilege escalation, a memory limit and a non-root user,
and only the socket proxy sees the Docker socket. Trivy's config scanner does not read Compose files, so this is
the check. A service without `user:` runs as its image's USER: that counts for the images built here, whose
final stage must set a non-root USER."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
SPEC: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))  # resolves the x-hardening merge
SERVICES: dict[str, dict[str, Any]] = SPEC["services"]
ROOT_USERS = {"0", "root"}


def dockerfiles() -> dict[str, Path]:
    """Local image name -> the Dockerfile that builds it (from the services that carry `build:`)."""
    out = {}
    for service in SERVICES.values():
        build = service.get("build")
        if build:
            out[service["image"]] = (COMPOSE.parent / build["context"] / build["dockerfile"]).resolve()
    return out


def final_user(dockerfile: Path) -> str | None:
    """The USER in effect in the last stage (None when that stage sets none: the base image's, often root)."""
    user = None
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*FROM\s", line, re.IGNORECASE):
            user = None
        elif match := re.match(r"^\s*USER\s+(\S+)", line, re.IGNORECASE):
            user = match.group(1)
    return user


def uid(user: str) -> str:
    return user.split(":", 1)[0].strip()


def test_the_compose_file_has_the_expected_services() -> None:
    assert {"postgres", "litellm", "z-server", "z-ui", "docker-socket-proxy", "z-migrate", "z-seed"} <= set(SERVICES)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_is_hardened(name: str) -> None:
    service = SERVICES[name]
    assert service.get("read_only") is True, f"{name}: read_only"
    assert service.get("cap_drop") == ["ALL"], f"{name}: cap_drop ALL"
    assert not service.get("cap_add"), f"{name}: cap_add"
    assert not service.get("privileged"), f"{name}: privileged"
    security = service.get("security_opt") or []
    assert "no-new-privileges:true" in security, f"{name}: no-new-privileges"
    assert not [o for o in security if "unconfined" in o], f"{name}: {security}"
    assert service.get("mem_limit"), f"{name}: mem_limit"
    assert service.get("pids_limit"), f"{name}: pids_limit"
    for key in ("network_mode", "pid", "ipc", "userns_mode"):
        assert service.get(key) not in {"host"}, f"{name}: {key}=host"
    assert not service.get("devices"), f"{name}: devices"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_runs_as_a_non_root_user(name: str) -> None:
    service = SERVICES[name]
    if "user" in service:
        assert uid(str(service["user"])) not in ROOT_USERS, f"{name}: user {service['user']}"
        return
    built = dockerfiles()
    image = str(service.get("image", ""))
    assert image in built, f"{name}: no `user:` and {image!r} is not built here: set a non-root user"
    user = final_user(built[image])
    assert user is not None and uid(user) not in ROOT_USERS, f"{name}: {built[image]} ends with USER {user}"


def test_only_the_socket_proxy_mounts_the_docker_socket() -> None:
    for name, service in SERVICES.items():
        mounts = [str(v) if isinstance(v, str) else str(v.get("source", "")) for v in service.get("volumes") or []]
        has_socket = any("docker.sock" in m for m in mounts)
        assert has_socket == (name == "docker-socket-proxy"), name


def test_final_user_reads_the_last_stage() -> None:
    assert final_user(ROOT / "deploy/images/z-ui/Dockerfile") == "101:101"  # USER root for apk, then back
    assert final_user(ROOT / "deploy/images/z-server/Dockerfile") == "10001:10001"
