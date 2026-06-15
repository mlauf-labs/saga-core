"""Unit tests for ingestion-time similarity scoring and folder voting (FR-16)."""

from __future__ import annotations

from saga.core.config import SimilarityConfig
from saga.core.models import SimilarDocument
from saga.search.similarity import score_candidates, vote_folders


def _cand(
    doc_id: str,
    score: float,
    *,
    doc_type: str | None = None,
    folder_ids: list[str] | None = None,
    primary: str | None = None,
    value_terms: list[str] | None = None,
) -> SimilarDocument:
    return SimilarDocument(
        document_id=doc_id,
        title=f"{doc_id}.pdf",
        score=score,
        doc_type=doc_type,
        folder_ids=folder_ids or [],
        primary_folder_id=primary,
        value_terms=value_terms or [],
    )


def test_score_candidates_combines_signals() -> None:
    config = SimilarityConfig()
    semantic = [_cand("a", 0.9, doc_type="invoice"), _cand("b", 0.1)]
    lexical = [_cand("a", 1.0, value_terms=["x"]), _cand("c", 0.5)]
    ranked = score_candidates(
        semantic=semantic,
        lexical=lexical,
        new_doc_type="invoice",
        new_value_terms=["x"],
        config=config,
    )
    # 'a' wins: high semantic + lexical + doc_type match + value overlap.
    assert ranked[0].document_id == "a"
    assert {c.document_id for c in ranked} == {"a", "b", "c"}


def test_score_candidates_respects_top_k() -> None:
    config = SimilarityConfig(top_k=1)
    semantic = [_cand("a", 0.9), _cand("b", 0.5), _cand("c", 0.1)]
    ranked = score_candidates(
        semantic=semantic,
        lexical=[],
        new_doc_type=None,
        new_value_terms=[],
        config=config,
    )
    assert len(ranked) == 1


def test_vote_folders_primary_boost_and_ancestor_credit() -> None:
    config = SimilarityConfig(primary_folder_boost=2.0, ancestor_credit=0.5, max_folder_votes=5)
    parents = {"child": "root", "root": None}
    similar = [_cand("a", 1.0, folder_ids=["child"], primary="child")]
    votes = {v.folder_id: v.score for v in vote_folders(similar, parents=parents, config=config)}
    # child gets score * boost (2.0); ancestor 'root' gets that weight * ancestor_credit.
    assert votes["child"] == 2.0
    assert votes["root"] == 1.0


def test_vote_folders_empty() -> None:
    assert vote_folders([], parents={}, config=SimilarityConfig()) == []
