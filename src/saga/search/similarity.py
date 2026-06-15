"""Document-similarity scoring and folder voting for ingestion-time placement (FR-16).

The goal is to find the documents most similar to a freshly ingested document so
that similar documents end up in the same folder. The combined similarity score of a
candidate document ``d`` is::

    score(d) = w_sem * sem(d) + w_lex * lex(d)
             + w_type * [doc_type(d) == doc_type(new)]
             + w_val  * jaccard(values(d), values(new))

where ``sem``/``lex`` are the per-list min-max-normalised semantic (summary kNN) and
lexical (more_like_this) scores. The top-K similar documents then vote for folders,
with a boost for a document's primary folder and partial credit to ancestor folders.

The scoring/voting functions are pure so they can be unit-tested deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from saga.core.logging import get_logger
from saga.core.models import FolderVote, SimilarDocument
from saga.storage.postgres import ancestor_ids

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.config import SimilarityConfig

_log = get_logger("saga.search.similarity")


def _normalise(candidates: Sequence[SimilarDocument]) -> dict[str, float]:
    """Min-max normalise candidate scores into ``{document_id: score in [0, 1]}``."""
    if not candidates:
        return {}
    scores = [c.score for c in candidates]
    lo, hi = min(scores), max(scores)
    span = hi - lo
    if span <= 0:
        return {c.document_id: 1.0 for c in candidates}
    return {c.document_id: (c.score - lo) / span for c in candidates}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def score_candidates(
    *,
    semantic: Sequence[SimilarDocument],
    lexical: Sequence[SimilarDocument],
    new_doc_type: str | None,
    new_value_terms: Sequence[str],
    config: SimilarityConfig,
) -> list[SimilarDocument]:
    """Combine semantic + lexical candidates into a ranked list of similar documents."""
    sem_norm = _normalise(semantic)
    lex_norm = _normalise(lexical)
    by_id: dict[str, SimilarDocument] = {}
    for cand in (*semantic, *lexical):
        by_id.setdefault(cand.document_id, cand)

    new_values = {t for t in new_value_terms if t}
    scored: list[SimilarDocument] = []
    for doc_id, cand in by_id.items():
        sem = sem_norm.get(doc_id, 0.0)
        lex = lex_norm.get(doc_id, 0.0)
        type_match = (
            1.0 if new_doc_type and cand.doc_type and cand.doc_type == new_doc_type else 0.0
        )
        val_sim = _jaccard(new_values, {t for t in cand.value_terms if t})
        combined = (
            config.weight_semantic * sem
            + config.weight_lexical * lex
            + config.weight_doc_type * type_match
            + config.weight_values * val_sim
        )
        scored.append(cand.model_copy(update={"score": combined}))

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[: config.top_k]


def vote_folders(
    similar: Sequence[SimilarDocument],
    *,
    parents: dict[str, str | None],
    config: SimilarityConfig,
) -> list[FolderVote]:
    """Aggregate folder votes from similar documents (the "likely placement")."""
    votes: dict[str, float] = {}
    for cand in similar:
        for folder_id in cand.folder_ids:
            weight = cand.score
            if cand.primary_folder_id == folder_id:
                weight *= config.primary_folder_boost
            votes[folder_id] = votes.get(folder_id, 0.0) + weight
            if config.ancestor_credit > 0:
                for ancestor in ancestor_ids([folder_id], parents):
                    if ancestor == folder_id:
                        continue
                    votes[ancestor] = votes.get(ancestor, 0.0) + weight * config.ancestor_credit
    ranked = sorted(
        (FolderVote(folder_id=fid, score=score) for fid, score in votes.items()),
        key=lambda v: v.score,
        reverse=True,
    )
    return ranked[: config.max_folder_votes]
