"""The documentation shipped with this version of Z, for the web app (GET /docs): the guides, the numbered
specification, the operations guides and the decision records, as Markdown. Links between pages become
/docs/<slug>; anything else (source files, external sites) is left as written.

Files are read from Z_DOCS_ROOT (the image puts them under /app; from source, the repository root). The journal
(WORKLOG), the build plan (BUILD) and the ADR index and template are not pages.
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from z_server.errors import Problem

GUIDES = {"cli/README.md": "cli", "docs/ROADMAP.md": "roadmap"}
OPERATIONS = {"deploy/README.md": "deploy", "deploy/helm/z/README.md": "helm"}
SKIP = {"docs/BUILD.md", "docs/WORKLOG.md", "docs/adr/README.md", "docs/adr/template.md"}
LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)\s]+?\.md)(#[^)\s]*)?\)")


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    section: str
    path: str  # relative to the root, POSIX


def root() -> Path:
    configured = os.environ.get("Z_DOCS_ROOT")
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for parent in here.parents:  # from source: the repository root holds docs/
        if (parent / "docs" / "adr").is_dir():
            return parent
    return Path("/app")


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


@functools.cache
def pages(base: Path) -> tuple[Page, ...]:
    found: list[Page] = []

    def add(rel: str, slug: str, section: str) -> None:
        file = base / rel
        if file.is_file():
            found.append(Page(slug, _title(file.read_text(encoding="utf-8"), slug), section, rel))

    for rel, slug in GUIDES.items():
        add(rel, slug, "guide")
    for file in sorted((base / "docs").glob("[0-9][0-9]-*.md")):
        rel = f"docs/{file.name}"
        add(rel, file.stem, "operations" if file.stem.startswith(("11-", "13-", "14-")) else "specification")
    for rel, slug in OPERATIONS.items():
        add(rel, slug, "operations")
    for file in sorted((base / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        rel = f"docs/adr/{file.name}"
        if rel not in SKIP:
            add(rel, f"adr-{file.stem}", "decision")
    return tuple(found)


def _links(markdown: str, page: Page, by_path: dict[str, str]) -> str:
    folder = PurePosixPath(page.path).parent

    def replace(match: re.Match[str]) -> str:
        target, anchor = match.group(1), match.group(2) or ""
        resolved = PurePosixPath(os.path.normpath(str(folder / target))).as_posix()
        slug = by_path.get(resolved) or by_path.get(target.lstrip("./"))
        return f"](/docs/{slug}{anchor})" if slug else match.group(0)

    return LINK.sub(replace, markdown)


def listing(base: Path | None = None) -> list[Page]:
    return list(pages(base or root()))


def read(slug: str, base: Path | None = None) -> tuple[Page, str]:
    base = base or root()
    catalog = pages(base)
    page = next((p for p in catalog if p.slug == slug), None)
    if page is None:
        raise Problem("not-found")
    markdown = (base / page.path).read_text(encoding="utf-8")
    return page, _links(markdown, page, {p.path: p.slug for p in catalog})
