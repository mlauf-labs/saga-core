"""Search and category-browsing endpoints (FR-19/20/21/22)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from docstore.api.dependencies import AuthDep, ServicesDep
from docstore.api.schemas import (
    CategoryTreeResponse,
    DocumentListResponse,
    DocumentResponse,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(tags=["search"], dependencies=[AuthDep])


@router.post("/search", response_model=SearchResponse, summary="Hybrid search")
async def search(services: ServicesDep, request: SearchRequest) -> SearchResponse:
    hits = await services.search.hybrid_search(
        query=request.query,
        top_k=request.top_k,
        doc_type=request.doc_type,
        category_path=request.category_path,
        title=request.title,
        filters=request.filters or None,
    )
    return SearchResponse(query=request.query, hits=hits)


@router.get(
    "/categories/tree",
    response_model=CategoryTreeResponse,
    summary="Get the hierarchical category tree",
)
async def category_tree(
    services: ServicesDep,
    prefix: Annotated[str | None, Query()] = None,
    max_depth: Annotated[int | None, Query(ge=1)] = None,
) -> CategoryTreeResponse:
    tree = await services.search.get_category_tree(prefix=prefix, max_depth=max_depth)
    return CategoryTreeResponse(tree=tree)


@router.get(
    "/categories/{category_path:path}/documents",
    response_model=DocumentListResponse,
    summary="List documents in a category branch",
)
async def documents_in_category(
    services: ServicesDep,
    category_path: str,
    include_subtree: Annotated[bool, Query()] = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    pagination = services.config.api.pagination
    effective_size = min(page_size or pagination.default_page_size, pagination.max_page_size)
    documents, total = await services.search.list_documents_in_category(
        category_path=category_path,
        include_subtree=include_subtree,
        page=page,
        page_size=effective_size,
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_document(doc, include_content=False) for doc in documents],
        page=page,
        page_size=effective_size,
        total=total,
    )
