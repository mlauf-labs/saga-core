"""Embedding provider abstraction (Ollama / OpenAI / Azure), FR-33 / NFR-34."""

from __future__ import annotations

from docstore.embeddings.base import EmbeddingProvider
from docstore.embeddings.config import EmbeddingsConfig, load_embeddings_config
from docstore.embeddings.providers import build_embedding_provider

__all__ = [
    "EmbeddingProvider",
    "EmbeddingsConfig",
    "build_embedding_provider",
    "load_embeddings_config",
]
