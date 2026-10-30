"""Fixtures: the fake LLM (tests/fakes/fake_llm), the fake z-server + gateway, and a helper that runs one job
with the real OpenHandsBackend in-process."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from z_common.models.api_internal import JobSpec
from z_runner.runner import Runner, RunnerConfig

from runner.tests.fake_internal import FakeJob, FakeZServer, demo_spec, make_archive, make_manifest
from tests.fakes.fake_llm import FakeLLM

NOTES = (Path(__file__).resolve().parents[2] / "packs" / "demo" / "evals" / "fixtures" / "notes.md").read_text()


@pytest.fixture(scope="session")
def fake_llm() -> Iterator[FakeLLM]:
    with FakeLLM() as llm:
        yield llm


@pytest.fixture
def llm(fake_llm: FakeLLM) -> Iterator[FakeLLM]:
    fake_llm.reset()
    yield fake_llm
    fake_llm.reset()


@pytest.fixture
def server() -> Iterator[FakeZServer]:
    with FakeZServer() as srv:
        yield srv


@dataclass
class JobRun:
    code: int
    job: FakeJob
    spec: JobSpec
    workspace: Path
    runner: Runner

    @property
    def completion(self) -> dict[str, Any]:
        assert self.job.completion is not None, "the job was not completed"
        return self.job.completion

    def types(self) -> list[str]:
        return [e["type"] for e in self.job.events]

    def events(self, type_: str) -> list[dict[str, Any]]:
        return [e["payload"] for e in self.job.events if e["type"] == type_]


class Harness:
    def __init__(self, server: FakeZServer, llm: FakeLLM, tmp_path: Path) -> None:
        self.server = server
        self.llm = llm
        self.tmp_path = tmp_path
        self._count = 0

    def spec(self, **overrides: Any) -> JobSpec:
        return demo_spec(self.llm.base_url, self.server.url, **overrides)

    def add(
        self,
        spec: JobSpec,
        files: dict[str, bytes | str] | None = None,
        flagged: list[str] | None = None,
        archive: bytes | None = None,
    ) -> FakeJob:
        files = {"notes.md": NOTES} if files is None else files
        if spec.workspace.mode.value == "none":
            return self.server.add_job(spec)
        return self.server.add_job(
            spec,
            archive=archive if archive is not None else make_archive(files),
            manifest=make_manifest(files, flagged),
        )

    def run(self, spec: JobSpec, job: FakeJob | None = None, *, workspace: Path | None = None, **config: Any) -> JobRun:
        job = job or self.add(spec)
        self._count += 1
        workspace = workspace or self.tmp_path / f"ws-{self._count}"
        cfg = RunnerConfig(
            workspace_dir=workspace,
            state_dir=self.tmp_path / f"state-{self._count}",
            strict_events=True,
            **config,
        )
        runner = Runner(self.server.url, spec.job_id, job.bootstrap_token, cfg)
        code = runner.run()
        self.server.assert_clean()
        return JobRun(code, job, spec, workspace, runner)


@pytest.fixture
def harness(server: FakeZServer, llm: FakeLLM, tmp_path: Path) -> Harness:
    return Harness(server, llm, tmp_path)
