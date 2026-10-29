"""Mission helpers shared by `z run`, `z ask`, `z -p` and `z missions watch`: inputs, files, following."""

from __future__ import annotations

import contextlib
import json
import mimetypes
import sys
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Literal

from rich.live import Live

from z_cli import output, sse
from z_cli.client import Api
from z_cli.errors import CliError, UsageError
from z_cli.live import TERMINAL_MISSION, MissionView
from z_cli.render import event_line, mission_tree

Mode = Literal["live", "lines", "jsonl", "quiet"]
ATTENTION = {"waiting", "planned"}


def parse_inputs(pairs: list[str], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """`k=v` (string, or the type the playbook's input schema declares) and `k:=<json>` (raw JSON)."""
    props: dict[str, Any] = (schema or {}).get("properties") or {}
    out: dict[str, Any] = {}
    for pair in pairs:
        eq = pair.find("=")
        if eq > 0 and pair[eq - 1] == ":":
            key, raw = pair[: eq - 1], pair[eq + 1 :]
            try:
                out[key.strip()] = json.loads(raw)
            except ValueError as exc:
                raise UsageError(f"--input {key}: not valid JSON ({exc})") from exc
            continue
        if "=" not in pair:
            raise UsageError(f"--input {pair!r}: use key=value (or key:=<json>)")
        key, value = pair.split("=", 1)
        key = key.strip()
        out[key] = _coerce(key, value, props.get(key) or {})
    return out


def _coerce(key: str, value: str, prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    try:
        if "integer" in kinds:
            return int(value)
        if "number" in kinds:
            return float(value)
        if "boolean" in kinds:
            if value.lower() in {"true", "yes", "1", "on"}:
                return True
            if value.lower() in {"false", "no", "0", "off"}:
                return False
            raise ValueError("expected true or false")
        if "array" in kinds:
            if value.startswith("["):
                return json.loads(value)
            return [v.strip() for v in value.split(",") if v.strip()]
        if "object" in kinds:
            return json.loads(value)
    except ValueError as exc:
        raise UsageError(f"--input {key}: {exc}") from exc
    return value


def upload_files(api: Api, mission_id: str, files: list[Path]) -> list[dict[str, Any]]:
    uploaded = []
    for path in files:
        if not path.is_file():
            raise UsageError(f"--file {path}: no such file")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            uploaded.append(
                api.post(
                    f"/missions/{mission_id}/files",
                    files={"file": (path.name, fh, content_type)},
                    data={"name": path.name},
                )
            )
    return uploaded


def choose_mode(output_format: str = "text") -> Mode:
    if output_format == "stream-json":
        return "jsonl"
    if output_format == "json":
        return "quiet"
    return "live" if sys.stdout.isatty() else "lines"


class Follower:
    """Follows one mission's events until it ends (or until `stop` says so), resuming after a pause."""

    def __init__(self, api: Api, mission: dict[str, Any], mode: Mode) -> None:
        self.api = api
        self.mission = mission
        self.mode = mode
        self.view = MissionView(str(mission["id"]))
        self.view.load_mission(mission)
        if mode == "live":
            with contextlib.suppress(CliError):
                self.view.use_agents(list(api.paginate("/agents", limit=500)))
        self.stream_state = sse.StreamState()
        self.events: list[dict[str, Any]] = []

    def refresh(self) -> dict[str, Any]:
        self.mission = self.api.get(f"/missions/{self.view.mission_id}")
        self.view.load_mission(self.mission)
        return self.mission

    def run(self, stop: Callable[[MissionView], bool] = lambda v: False) -> dict[str, Any]:
        """Returns the mission as the API reports it when following ends."""
        view = self.view
        stopped = False

        def finished() -> bool:
            return stopped or str(self.refresh()["status"]) in TERMINAL_MISSION

        live = (
            Live(mission_tree(view), console=output.out(), refresh_per_second=8, transient=False)
            if self.mode == "live"
            else nullcontext()
        )
        with live as lv:
            for message in sse.stream(
                self.api, f"/missions/{view.mission_id}/events", finished=finished, state=self.stream_state
            ):
                event = message.json()
                if not isinstance(event, dict):
                    continue
                self.events.append(event)
                view.apply(event)
                if self.mode == "jsonl":
                    output.print_jsonl(event)
                elif self.mode == "lines":
                    output.out().print(event_line(event))
                elif isinstance(lv, Live):
                    lv.update(mission_tree(view))
                if stop(view):
                    self.refresh()  # replayed history passes through old states: check the live one
                    if stop(view):
                        stopped = True
                        break
            self.refresh()
            if isinstance(lv, Live):
                lv.update(mission_tree(view))
        return self.mission


def needs_attention(view: MissionView) -> bool:
    return view.status in ATTENTION and (view.status != "planned" or view.plan_first)
