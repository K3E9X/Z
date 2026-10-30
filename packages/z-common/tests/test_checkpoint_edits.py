"""Take-over edits merged into a workspace delta keep the links the agent created."""

from __future__ import annotations

import io
import json
import tarfile

from z_common.checkpoint import DELTA_META, delta_parts, merge_edits


def delta(files: dict[str, bytes], meta: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in {**files, DELTA_META: json.dumps(meta).encode()}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_merge_edits_keeps_links_and_drops_replaced_ones() -> None:
    before = delta(
        {"a.txt": b"a"},
        {"deleted": ["old.txt"], "links": {".venv/bin/python": "/usr/bin/python3", "current": "a.txt"}},
    )
    merged = merge_edits(before, {"current": b"now a file", "b.txt": b"b", "old.txt": None})
    files, deleted, links = delta_parts(merged)
    assert files == {"a.txt": b"a", "current": b"now a file", "b.txt": b"b"}
    assert deleted == ["old.txt"] and links == {".venv/bin/python": "/usr/bin/python3"}
