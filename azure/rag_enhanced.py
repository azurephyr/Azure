"""
Azure Hybrid RAG System with Knowledge Graph + Temporal Memory

Combines multiple retrieval strategies:
- BM25 keyword search (fast, exact match)
- Dense vector search (semantic similarity)
- Knowledge graph traversal (entity relationships)
- Temporal decay weighting (recent = more relevant)

All results include source citations.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger("azure.rag_enhanced")


@dataclass
class RAGResult:
    """A single retrieved memory/document with metadata."""
    text: str
    source: str
    score: float
    timestamp: float
    entity_tags: list[str] = field(default_factory=list)
    memory_type: str = "conversation"


@dataclass
class KnowledgeGraph:
    """Simple knowledge graph: entities linked to memory IDs."""
    entities: dict[str, list[str]] = field(default_factory=dict)  # entity -> [memory_ids]
    relations: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # entity -> [(rel, target)]

    def add_entity(self, entity: str, memory_id: str):
        self.entities.setdefault(entity.lower(), []).append(memory_id)

    def get_related(self, entity: str) -> list[str]:
        return self.entities.get(entity.lower(), [])

    def extract_entities(self, text: str) -> list[str]:
        """Simple entity extraction: capitalized noun phrases and quoted terms."""
        import re
        entities = []
        # Quoted phrases
        entities.extend(re.findall(r'"([^"]+)"', text))
        # Capitalized words (potential proper nouns)
        for match in re.findall(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b', text):
            if len(match) > 3:  # filter out short words like "I", "The"
                entities.append(match)
        return list(set(entities))


class HybridRAG:
    """
    Hybrid retrieval-augmented generation system.

    Usage:
        rag = HybridRAG(db_path="data/rag.db", embedding_fn=embed_fn)
        rag.add_memory("User said they like Python", source="#general", tags=["python"])
        results = rag.query("What does the user like?", top_k=5)
    """

    def __init__(self, db_path: str = "data/hybrid_rag.db",
                 embedding_fn: Callable | None = None,
                 decay_halflife_days: float = 30.0):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_fn = embedding_fn
        self.decay_halflife = decay_halflife_days * 86400.0  # seconds
        self.kg = KnowledgeGraph()
        # Serialize multi-threaded add/query (bot executor + scheduler).
        # SQLite file locks alone do not protect the in-process KG or emb cache.
        self._lock = threading.RLock()
        # Dense-search cache: avoid re-JSON-parsing every embedding on each query.
        # _emb_ids[i] corresponds to row i of _emb_matrix (L2-normalized).
        self._emb_ids: list[str] = []
        self._emb_matrix: np.ndarray | None = None  # shape (n, dim)
        self._emb_dirty = True
        self._init_db()
        self._rebuild_kg_from_db()
        self._rebuild_embedding_cache()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection with WAL for multi-reader concurrency."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    source TEXT,
                    embedding BLOB,
                    timestamp REAL,
                    tags TEXT,
                    memory_type TEXT DEFAULT 'conversation'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bm25_terms (
                    term TEXT,
                    memory_id TEXT,
                    freq INTEGER DEFAULT 1,
                    PRIMARY KEY (term, memory_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_time ON memories(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_terms ON bm25_terms(term)
            """)
            conn.commit()

    def _rebuild_kg_from_db(self) -> None:
        """Rebuild process-local KG so entity boosts survive restarts.

        Previously KG was RAM-only; after restart `_kg_boost` returned {}.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, text, tags FROM memories"
            ).fetchall()
        for mid, text, tags_json in rows:
            for ent in self.kg.extract_entities(text or ""):
                self.kg.add_entity(ent, mid)
            try:
                tags = json.loads(tags_json) if tags_json else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            for tag in tags:
                self.kg.add_entity(str(tag), mid)

    def _rebuild_embedding_cache(self) -> None:
        """Load all dense vectors once into a float matrix for O(n·d) matmul.

        Before: every query did SELECT all rows + json.loads + normalize
        → O(n) Python/JSON work per query. After: O(n·d) BLAS matmul only,
        with O(1) amortized append on add_memory when shapes match.

        Mixed embedding dimensions (e.g. 384-d dense + 32-d hash) are filtered
        so only the dominant dimension is kept — never vstack mismatched rows.
        """
        ids: list[str] = []
        vectors: list[np.ndarray] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
            ).fetchall()
        parsed: list[tuple[str, np.ndarray]] = []
        dim_counts: dict[int, int] = {}
        for mid, emb_blob in rows:
            try:
                vec = np.asarray(json.loads(emb_blob), dtype=np.float64).ravel()
                if vec.size == 0:
                    continue
                norm = np.linalg.norm(vec)
                if norm == 0:
                    continue
                parsed.append((mid, vec / norm))
                dim_counts[vec.size] = dim_counts.get(vec.size, 0) + 1
            except Exception:
                continue
        target_dim = None
        if dim_counts:
            # Prefer the dimension that matches the current embedding_fn output
            preferred = None
            if self.embedding_fn is not None:
                try:
                    probe = self.embedding_fn("dimension probe")
                    preferred = int(np.asarray(probe, dtype=np.float64).ravel().size)
                except Exception:
                    preferred = None
            if preferred is not None and preferred in dim_counts:
                target_dim = preferred
            else:
                target_dim = max(dim_counts.items(), key=lambda kv: kv[1])[0]
        for mid, vec in parsed:
            if target_dim is not None and vec.size != target_dim:
                continue
            vectors.append(vec)
            ids.append(mid)
        self._emb_ids = ids
        if vectors:
            self._emb_matrix = np.vstack(vectors)
        else:
            self._emb_matrix = None
        self._emb_dirty = False
        if dim_counts and target_dim is not None and len(dim_counts) > 1:
            skipped = sum(c for d, c in dim_counts.items() if d != target_dim)
            logger.info(
                "[hybrid_rag] embedding cache: dim=%d kept=%d skipped_mismatched=%d",
                target_dim, len(vectors), skipped,
            )

    def _append_embedding_cache(self, memory_id: str, vec: np.ndarray) -> None:
        """O(1) amortized append when cache is warm; full rebuild if dirty/shape mismatch."""
        if self._emb_dirty or self._emb_matrix is None:
            self._rebuild_embedding_cache()
            return
        v = np.asarray(vec, dtype=np.float64).ravel()
        norm = np.linalg.norm(v)
        if norm == 0:
            return
        v = v / norm
        if self._emb_matrix.ndim != 2 or self._emb_matrix.shape[1] != v.shape[0]:
            self._rebuild_embedding_cache()
            return
        self._emb_matrix = np.vstack([self._emb_matrix, v.reshape(1, -1)])
        self._emb_ids.append(memory_id)

    # ------------------------------------------------------------------
    # Memory ingestion
    # ------------------------------------------------------------------

    def add_memory(self, text: str, source: str = "",
                   tags: list[str] | None = None,
                   memory_type: str = "conversation") -> str:
        """Add a memory to the hybrid system."""
        memory_id = f"mem_{time.time():.6f}_{hash(text) & 0xFFFFFF:06x}"
        timestamp = time.time()
        tags = tags or []

        # Compute embedding
        embedding = None
        raw_vec = None
        if self.embedding_fn:
            try:
                vec = self.embedding_fn(text)
                raw_vec = np.asarray(
                    vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    dtype=np.float64,
                )
                embedding = json.dumps(raw_vec.tolist())
            except Exception as e:
                logger.error(f"[hybrid_rag] embedding error: {e}")

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO memories (id, text, source, embedding, timestamp, tags, memory_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (memory_id, text, source, embedding, timestamp, json.dumps(tags), memory_type)
                )
                # BM25 terms
                terms = self._tokenize(text)
                for term in terms:
                    conn.execute(
                        "INSERT INTO bm25_terms (term, memory_id, freq) VALUES (?, ?, 1) ON CONFLICT(term, memory_id) DO UPDATE SET freq = freq + 1",
                        (term, memory_id)
                    )
                conn.commit()

            # Knowledge graph
            entities = self.kg.extract_entities(text)
            for ent in entities:
                self.kg.add_entity(ent, memory_id)
            for tag in tags:
                self.kg.add_entity(tag, memory_id)

            if raw_vec is not None:
                self._append_embedding_cache(memory_id, raw_vec)

        return memory_id

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, query_text: str, top_k: int = 5,
              time_window_hours: int | None = None,
              scope_tag: str | None = None) -> list[RAGResult]:
        """
        Hybrid query: BM25 + dense + temporal + knowledge graph.
        Returns fused results sorted by combined score.
        """
        with self._lock:
            allowed_ids = self._memory_ids_for_scope(scope_tag)
            if scope_tag is not None and not allowed_ids:
                return []

            # 1. BM25 scores
            bm25_results = self._bm25_search(query_text)
            if allowed_ids is not None:
                bm25_results = {mid: score for mid, score in bm25_results.items() if mid in allowed_ids}

            # 2. Dense scores
            dense_results = self._dense_search(query_text, allowed_ids) if self.embedding_fn else {}

            # 3. Knowledge graph boost
            kg_boost = self._kg_boost(query_text)
            if allowed_ids is not None:
                kg_boost = {mid: score for mid, score in kg_boost.items() if mid in allowed_ids}

            # 4. Temporal decay
            now = time.time()

            # Fuse scores
            all_ids = set(bm25_results) | set(dense_results) | set(kg_boost)
            fused = {}
            for mid in all_ids:
                bm25 = bm25_results.get(mid, 0.0)
                dense = dense_results.get(mid, 0.0)
                kg = kg_boost.get(mid, 0.0)
                # Weighted fusion
                score = 0.35 * bm25 + 0.45 * dense + 0.20 * kg
                fused[mid] = score

            if not fused:
                return []

            # Candidate shortlist before disk fetch
            candidate_ids = sorted(fused, key=fused.get, reverse=True)[: top_k * 3]

            # Batch fetch: O(1) SQL round-trip instead of N individual SELECTs
            placeholders = ",".join("?" for _ in candidate_ids)
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT id, text, source, timestamp, tags, memory_type "
                    f"FROM memories WHERE id IN ({placeholders})",
                    candidate_ids,
                ).fetchall()

            by_id = {r[0]: r for r in rows}
            results = []
            for mid in candidate_ids:
                row = by_id.get(mid)
                if not row:
                    continue
                _id, text, source, ts, tags_json, mtype = row
                age = now - ts
                decay = math.exp(-0.693 * age / self.decay_halflife) if self.decay_halflife > 0 else 1.0
                if time_window_hours is not None and age > time_window_hours * 3600:
                    continue
                final_score = fused[mid] * (0.5 + 0.5 * decay)  # never fully decay to 0
                try:
                    entity_tags = json.loads(tags_json) if tags_json else []
                except (json.JSONDecodeError, TypeError):
                    entity_tags = []
                results.append(RAGResult(
                    text=text,
                    source=source,
                    score=final_score,
                    timestamp=ts,
                    entity_tags=entity_tags,
                    memory_type=mtype or "conversation",
                ))

            results.sort(key=lambda r: r.score, reverse=True)
            return results[:top_k]

    def _memory_ids_for_scope(self, scope_tag: str | None) -> set[str] | None:
        """Return memory IDs carrying an exact scope tag."""
        if scope_tag is None:
            return None
        with self._connect() as conn:
            rows = conn.execute("SELECT id, tags FROM memories").fetchall()
        allowed: set[str] = set()
        for memory_id, tags_json in rows:
            try:
                tags = json.loads(tags_json) if tags_json else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            if scope_tag in tags:
                allowed.add(memory_id)
        return allowed

    # ------------------------------------------------------------------
    # Search backends
    # ------------------------------------------------------------------

    def _bm25_search(self, query: str) -> dict[str, float]:
        """Simple BM25-like scoring using sqlite."""
        terms = self._tokenize(query)
        if not terms:
            return {}

        scores = {}
        with self._connect() as conn:
            # Get document frequencies
            total_docs = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if total_docs == 0:
                return {}

            for term in terms:
                rows = conn.execute(
                    "SELECT memory_id, freq FROM bm25_terms WHERE term = ?",
                    (term,)
                ).fetchall()
                df = len(rows)
                idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1.0)
                for mid, freq in rows:
                    scores[mid] = scores.get(mid, 0.0) + idf * freq

        return scores

    def _dense_search(self, query: str, allowed_ids: set[str] | None = None) -> dict[str, float]:
        """Dense vector similarity via cached matrix · query (BLAS).

        Complexity: O(n·d) matmul after O(1) cache hit, vs previous
        O(n) Python loop + json.loads + per-vector normalize every query.
        """
        if not self.embedding_fn:
            return {}
        try:
            q_vec = self.embedding_fn(query)
            q_vec = np.asarray(
                q_vec.tolist() if hasattr(q_vec, "tolist") else list(q_vec),
                dtype=np.float64,
            )
            q_norm = np.linalg.norm(q_vec)
            if q_norm == 0:
                return {}
            q_vec = q_vec / q_norm
        except Exception as e:
            logger.error(f"[hybrid_rag] dense query error: {e}")
            return {}

        if self._emb_dirty or self._emb_matrix is None:
            self._rebuild_embedding_cache()
        if self._emb_matrix is None or not self._emb_ids:
            return {}
        if self._emb_matrix.shape[1] != q_vec.shape[0]:
            # Dimension drift (model change) — rebuild once
            self._rebuild_embedding_cache()
            if self._emb_matrix is None or self._emb_matrix.shape[1] != q_vec.shape[0]:
                return {}

        # scores[i] = cosine(matrix[i], q) since both are L2-normalized
        sims = self._emb_matrix @ q_vec  # (n,)
        scores = {}
        for mid, sim in zip(self._emb_ids, sims, strict=False):
            if allowed_ids is not None and mid not in allowed_ids:
                continue
            s = float(sim)
            if s > 0.0:
                scores[mid] = s
        return scores

    def _kg_boost(self, query: str) -> dict[str, float]:
        """Boost memories that share entities with the query."""
        entities = self.kg.extract_entities(query)
        if not entities:
            return {}
        scores = {}
        for ent in entities:
            for mid in self.kg.get_related(ent):
                scores[mid] = scores.get(mid, 0.0) + 0.5
        return scores

    # ------------------------------------------------------------------
    # Tokenization
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization for BM25."""
        import re
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", "", text)
        tokens = [t for t in text.split() if len(t) > 2 and t not in self._STOPWORDS]
        return tokens

    _STOPWORDS = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was",
        "one", "our", "out", "day", "get", "has", "him", "his", "how", "man", "new", "now",
        "old", "see", "two", "way", "who", "boy", "did", "its", "let", "put", "say", "she",
        "too", "use", "that", "with", "have", "this", "will", "your", "from", "they", "know",
        "want", "been", "good", "much", "some", "time", "very", "when", "come", "here", "just",
        "like", "long", "make", "many", "over", "such", "take", "than", "them", "well", "were",
    }
