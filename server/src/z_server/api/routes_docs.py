"""The documentation in the web app (docs_catalog): any signed-in user, read only."""

from __future__ import annotations

from z_common.models import api_ops as m

from z_server import docs_catalog
from z_server.api.framework import ContractRouter, CtxDep

router = ContractRouter()


@router.operation("listDocs")
async def list_docs(ctx: CtxDep) -> list[m.DocSummary]:
    return [m.DocSummary(slug=p.slug, title=p.title, section=p.section, path=p.path) for p in docs_catalog.listing()]


@router.operation("getDoc")
async def get_doc(ctx: CtxDep, slug: str) -> m.Doc:
    page, markdown = docs_catalog.read(slug)
    return m.Doc(slug=page.slug, title=page.title, section=page.section, path=page.path, markdown=markdown)
