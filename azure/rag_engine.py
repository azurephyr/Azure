"""
Azure Local RAG Engine (Retrieval-Augmented Generation)

Stores Discord conversation embeddings locally and retrieves relevant
context to make the LLM feel smarter and more context-aware.

Uses sentence-transformers (all-MiniLM-L6-v2, ~80MB) for local embeddings.
No external API calls. Pure local CPU computation.

Usage:
    from azure.rag_engine import DiscordRAG
    rag = DiscordRAG()
    rag.add("User: How do I get the nitro role?", {"channel": "help", "user": "Bob"})
    rag.add("Admin: Type !role nitro in #bot-commands", {"channel": "help", "user": "Alice"})

    results = rag.search("how do i get nitro", k=3)
    # Returns relevant past messages for context
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Module-level slot for the SentenceTransformer class.
# Populated lazily by _load_model(). Tests can patch
# 'azure.rag_engine._SentenceTransformer' to skip network downloads.
_SentenceTransformer = None

logger = logging.getLogger("azure.rag_engine")


@dataclass
class Document:
    """A stored conversation fragment with metadata."""
    id: str
    text: str
    embedding: np.ndarray = field(repr=False)
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class DiscordRAG:
    """
    Local retrieval-augmented generation for Discord context.

    Stores recent conversations as embeddings and retrieves relevant ones
    to provide the LLM with memory of recent server activity.
    """

    def __init__(self, embedding_model: str = "all-MiniLM-L6-v2",
                 persist_path: Path | None = None,
                 max_docs: int = 1000):
        """
        Args:
            embedding_model: Sentence-transformers model name (default ~80MB)
            persist_path: Optional path to save/load the vector store
            max_docs: Maximum documents to keep (oldest are evicted)
        """
        self.max_docs = max_docs
        self.persist_path = persist_path
        self.docs: list[Document] = []
        self._embedding_model_name = embedding_model
        self._embedding_model = None
        self._dim = None
        self._lock = threading.RLock()
        # Cached stack of embeddings: search is O(n·d) matmul without
        # re-allocating via np.stack on every query (was O(n) alloc + copy).
        self._matrix: np.ndarray | None = None
        self._matrix_dirty = True
        # Persist at most once per N adds to avoid fsync storms under chat load.
        self._adds_since_save = 0
        self._save_every = 25

        # Load persisted data if available
        if persist_path and persist_path.exists():
            self._load_from_disk()

    def _load_model(self) -> None:
        """Load sentence-transformers model.

        The class is resolved once and cached in the module-level
        ``_SentenceTransformer`` slot so that tests can patch it via
        ``patch('azure.rag_engine._SentenceTransformer', ...)``. """
        if self._embedding_model is not None:
            return
        global _SentenceTransformer
        if _SentenceTransformer is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers not installed. Run:\n"
                    "  pip install sentence-transformers\n"
                ) from e
            _SentenceTransformer = SentenceTransformer

        logger.info("[rag] loading embedding model on first use: %s", self._embedding_model_name)
        allow_download = os.environ.get("AZURE_RAG_ALLOW_DOWNLOAD", "0").lower() in {
            "1", "true", "yes", "on",
        }
        try:
            self._embedding_model = _SentenceTransformer(
                self._embedding_model_name,
                local_files_only=not allow_download,
            )
        except TypeError:
            # Test doubles and older versions may not support local_files_only.
            self._embedding_model = _SentenceTransformer(self._embedding_model_name)
        # Newer sentence-transformers renamed get_sentence_embedding_dimension()
        # to get_embedding_dimension(). Try the new name first, fall back.
        try:
            self._dim = self._embedding_model.get_embedding_dimension()
        except AttributeError:
            self._dim = self._embedding_model.get_sentence_embedding_dimension()
        logger.info(f"[rag] embedding dim: {self._dim}")

    def _invalidate_matrix(self) -> None:
        self._matrix_dirty = True
        self._matrix = None

    def _ensure_matrix(self) -> np.ndarray | None:
        """Build or return the cached (n, d) embedding matrix."""
        if not self.docs:
            self._matrix = None
            self._matrix_dirty = False
            return None
        if not self._matrix_dirty and self._matrix is not None:
            return self._matrix
        self._matrix = np.stack([d.embedding for d in self.docs])
        self._matrix_dirty = False
        return self._matrix

    def add(self, text: str, metadata: dict | None = None) -> str:
        """
        Add a conversation fragment to the store.

        Args:
            text: The conversation text to embed
            metadata: Dict with keys like channel, user, timestamp, message_id

        Returns:
            Document ID
        """
        self._load_model()
        emb = self._embedding_model.encode(text, normalize_embeddings=True)

        with self._lock:
            doc_id = f"doc_{int(time.time() * 1000)}_{len(self.docs)}"
            doc = Document(
                id=doc_id,
                text=text,
                embedding=emb,
                timestamp=time.time(),
                metadata=metadata or {},
            )
            self.docs.append(doc)

            # Evict oldest if over limit
            if len(self.docs) > self.max_docs:
                self.docs = self.docs[-self.max_docs:]
                self._invalidate_matrix()
            else:
                # O(1) append path when matrix is warm and shapes match
                if (
                    not self._matrix_dirty
                    and self._matrix is not None
                    and self._matrix.ndim == 2
                    and self._matrix.shape[0] == len(self.docs) - 1
                ):
                    row = np.asarray(emb, dtype=self._matrix.dtype).reshape(1, -1)
                    if row.shape[1] == self._matrix.shape[1]:
                        self._matrix = np.vstack([self._matrix, row])
                    else:
                        self._invalidate_matrix()
                else:
                    self._invalidate_matrix()

            # Persist so restarts keep context (previously add() never saved).
            self._adds_since_save += 1
            if self.persist_path and self._adds_since_save >= self._save_every:
                try:
                    self.save()
                    self._adds_since_save = 0
                except Exception as e:
                    logger.warning("[rag] periodic save failed: %s", e)

            return doc_id

    def add_message(self, user_name: str, content: str, channel: str = "",
                    guild: str = "", message_id: str = "") -> str:
        """Convenience: add a Discord message with standard metadata."""
        text = f"[{user_name}] {content}"
        return self.add(text, {
            "user": user_name,
            "channel": channel,
            "guild": guild,
            "message_id": message_id,
            "type": "message",
        })

    def add_fact(self, key: str, value: str) -> str:
        """Add a learned fact to the RAG store."""
        text = f"FACT: {key} = {value}"
        return self.add(text, {"type": "fact", "key": key})

    def search(self, query: str, k: int = 3, scope: str | None = None) -> list[dict]:
        """
        Search for relevant past conversations.

        Args:
            query: Search query (e.g., current user message)
            k: Number of results to return

        Returns:
            List of dicts with text, score, metadata
        """
        if self._embedding_model is None:
            return []

        query_emb = self._embedding_model.encode(query, normalize_embeddings=True)

        with self._lock:
            if not self.docs:
                return []
            embeddings = self._ensure_matrix()
            if embeddings is None:
                return []

            # Cosine similarity (normalized embeddings → dot product)
            scores = np.dot(embeddings, query_emb)

            eligible = [
                index for index, doc in enumerate(self.docs)
                if scope is None or doc.metadata.get("guild") == scope
            ]
            if not eligible:
                return []

            # Get top-k only from the requested server scope.
            eligible_scores = scores[eligible]
            ranked = np.argsort(eligible_scores)[-k:][::-1]
            top_indices = [eligible[index] for index in ranked]

            results = []
            for idx in top_indices:
                doc = self.docs[idx]
                results.append({
                    "id": doc.id,
                    "text": doc.text,
                    "score": float(scores[idx]),
                    "metadata": doc.metadata,
                    "timestamp": doc.timestamp,
                })
            return results

    def search_as_context(self, query: str, k: int = 3, scope: str | None = None) -> str:
        """
        Search and format results as a context string for the LLM.

        Returns a string like:
          "Relevant context from past conversations:\n"
          "- [User] asked about X\n"
          "- [Admin] answered Y\n"
        """
        results = self.search(query, k=k, scope=scope)
        if not results:
            return ""

        lines = ["Relevant past context:"]
        for r in results:
            text = r["text"][:200]  # Truncate long messages
            lines.append(f"- {text}")
        return "\n".join(lines)

    def get_recent(self, n: int = 5) -> list[dict]:
        """Get the N most recent documents."""
        with self._lock:
            recent = self.docs[-n:]
            return [
                {"id": d.id, "text": d.text, "metadata": d.metadata, "timestamp": d.timestamp}
                for d in recent
            ]

    def clear(self) -> None:
        """Clear all stored documents."""
        with self._lock:
            self.docs = []
            self._invalidate_matrix()

    def save(self, path: Path | None = None) -> None:
        """Persist the vector store to disk."""
        save_path = path or self.persist_path
        if not save_path:
            return

        with self._lock:
            data = {
                "model": self._embedding_model_name,
                "dim": self._dim,
                "docs": [
                    {
                        "id": d.id,
                        "text": d.text,
                        "embedding": d.embedding.tolist() if hasattr(d.embedding, "tolist") else list(d.embedding),
                        "timestamp": d.timestamp,
                        "metadata": d.metadata,
                    }
                    for d in self.docs
                ],
            }
            n = len(self.docs)

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace avoids half-written JSON on crash
        tmp = save_path.with_suffix(save_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(save_path)
        logger.info(f"[rag] saved {n} docs to {save_path}")

    def _load_from_disk(self) -> None:
        """Load persisted vector store from disk."""
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            with self._lock:
                self.docs = [
                    Document(
                        id=d["id"],
                        text=d["text"],
                        embedding=np.array(d["embedding"]),
                        timestamp=d["timestamp"],
                        metadata=d.get("metadata", {}),
                    )
                    for d in data.get("docs", [])
                ]
                self._invalidate_matrix()
            logger.info(f"[rag] loaded {len(self.docs)} docs from {self.persist_path}")

        except Exception as e:
            logger.error(f"[rag] failed to load from disk: {e}")
