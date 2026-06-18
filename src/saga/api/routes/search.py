"""Fused hybrid search endpoint (FR-19/20/21)."""

from __future__ import annotations

from fastapi import APIRouter

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import SearchRequest, SearchResponse

router = APIRouter(tags=["search"], dependencies=[AuthDep])


@router.post("/search", response_model=SearchResponse, summary="Fused hybrid search (RRF)")
async def search(services: ServicesDep, request: SearchRequest) -> SearchResponse:
    result = await services.search.hybrid_search(
        keyword_query=request.keyword_query,
        semantic_query=request.semantic_query,
        top_k=request.top_k,
        doc_type=request.doc_type,
        folder_id=request.folder_id,
        include_subtree=request.include_subtree,
        title=request.title,
        status=request.status,
        created_from=request.created_from,
        created_to=request.created_to,
        filters=request.filters or None,
        metadata=request.metadata or None,
    )
    return SearchResponse(results=result.results)
