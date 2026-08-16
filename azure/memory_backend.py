"""
Azure Persistent Memory Backend

Pluggable memory system supporting SQLite (default), Redis (optional), and in-memory fallback.

Stores:
- User profiles (preferences, style, expertise)
- Conversation memories (vectorized, timestamped, topic-tagged)
- Episodic events (key moments, decisions, outcomes)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.memory_backend")


@dataclass
class UserProfile:
    """Adaptive user profile learned from interactions."""
    user_id: str
    user_name: str = ""
    communication_style: str = "neutral"  # casual, formal, technical, social
    expertise_level: str = "general"      # beginner, intermediate, advanced, expert
    verbosity: str = "normal"             # concise, normal, verbose
    humor_score: float = 0.5              # 0-1, learned from reactions
    preferred_topics: list[str] = field(default_factory=list)
    disliked_topics: list[str] = field(default_factory=list)
    last_interaction: float = 0.0
    total_interactions: int = 0
    corrections_received: int = 0
    thumbs_up: int = 0
    thumbs_down: int = 0


@dataclass
class EpisodicEvent:
    """A key event in the server's history."""
    event_id: str
    timestamp: float
    event_type: str  # "decision", "achievement", "conflict", "milestone"
    description: str
    participants: list[str] = field(default_factory=list)
    outcome: str = ""
    sentiment: float = 0.0  # -1 to 1


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MemoryBackend:
    """Base class for memory backends with in-memory dict + JSON persistence."""

    _MAX_MEMORIES = 5000  # Cap to prevent unbounded growth
    _MAX_EVENTS = 2000    # Cap episodic events

    def __init__(self):
        self._profiles: dict[str, str] = {}
        self._memories: list[dict] = []
        self._events: list[EpisodicEvent] = []
        self._conversations: dict[str, list[str]] = {}
        self._json_path: Path | None = None
        self._lock = threading.RLock()

    def _set_json_path(self, path: str):
        self._json_path = Path(path)
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_from_json()

    def _load_from_json(self):
        if self._json_path and self._json_path.exists():
            try:
                with open(self._json_path, encoding="utf-8") as f:
                    data = json.load(f)
                self._conversations = data.get("conversations", {})
                logger.info("Loaded %d conversations from %s", len(self._conversations), self._json_path)
            except Exception as e:
                logger.warning("Failed to load memory from %s: %s", self._json_path, e)

    def _save_to_json(self):
        if self._json_path:
            with self._lock:
                try:
                    tmp = self._json_path.with_suffix('.tmp')
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"conversations": self._conversations}, f, indent=2)
                    tmp.replace(self._json_path)
                except Exception as e:
                    logger.warning("Failed to save memory to %s: %s", self._json_path, e)

    _MAX_CONVERSATION_LEN = 500

    def store(self, user_id: str, message: str):
        with self._lock:
            if user_id not in self._conversations:
                self._conversations[user_id] = []
            self._conversations[user_id].append(message)
            if len(self._conversations[user_id]) > self._MAX_CONVERSATION_LEN:
                self._conversations[user_id] = self._conversations[user_id][-self._MAX_CONVERSATION_LEN // 2:]
            self._save_to_json()

    def retrieve(self, user_id: str) -> list[str]:
        with self._lock:
            return list(self._conversations.get(user_id, []))

    def search(self, query: str) -> list[dict[str, Any]]:
        results = []
        with self._lock:
            snapshot = dict(self._conversations)
        for user_id, msgs in snapshot.items():
            for msg in msgs:
                if query.lower() in msg.lower():
                    results.append({"user_id": user_id, "message": msg})
        return results

    def delete(self, user_id: str):
        with self._lock:
            self._conversations.pop(user_id, None)
            self._save_to_json()

    def save_user_profile(self, profile: UserProfile):
        with self._lock:
            self._profiles[profile.user_id] = json.dumps(asdict(profile))

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        with self._lock:
            data = self._profiles.get(user_id)
        return UserProfile(**json.loads(data)) if data else None

    def save_memory(self, text: str, user_id: str, source: str = "",
                    tags: list[str] | None = None, embedding: list[float] | None = None) -> str:
        import uuid
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._memories.append({
                "id": mem_id, "user_id": user_id, "text": text,
                "source": source, "tags": tags or [], "timestamp": time.time()
            })
            # Evict oldest if over cap (prevent unbounded growth)
            if len(self._memories) > self._MAX_MEMORIES:
                self._memories = self._memories[-self._MAX_MEMORIES:]
        return mem_id

    def query_memories(self, user_id: str = "", tags: list[str] | None = None,
                       limit: int = 10) -> list[dict]:
        with self._lock:
            snapshot = list(reversed(self._memories))
        results = []
        for m in snapshot:
            if user_id and m.get("user_id") != user_id:
                continue
            if tags and not any(t in m.get("tags", []) for t in tags):
                continue
            results.append(m)
            if len(results) >= limit:
                break
        return results

    def search_memories(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        """Search memories by keyword relevance ranking.

        Extracts keywords from the query and ranks matching memories by
        number of keyword hits, recency, and user match.
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # Take a snapshot under lock to avoid RuntimeError from concurrent mutation
        with self._lock:
            snapshot = list(reversed(self._memories))

        scored = []
        for m in snapshot:
            if user_id and m.get("user_id") != user_id:
                continue
            text = (m.get("text", "") or "").lower()
            hits = sum(1 for kw in keywords if kw in text)
            if hits == 0:
                continue
            tag_hits = sum(1 for kw in keywords for t in (m.get("tags", []) or []) if kw in t.lower())
            total_hits = hits + tag_hits * 2
            # Recency bonus (higher for more recent memories)
            age_hours = (time.time() - m.get("timestamp", 0)) / 3600
            recency = max(0, 1 - age_hours / 720)  # 30-day half-life
            scored.append((total_hits * recency, m))

        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:limit]]

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful keywords from a search query."""
        import re
        # Remove punctuation and split
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        # Common stop words to filter
        stop_words = {
            "the", "and", "for", "are", "but", "not", "you", "all", "can",
            "had", "her", "was", "one", "our", "out", "has", "have", "been",
            "some", "them", "than", "what", "when", "why", "how", "who",
            "which", "where", "their", "there", "would", "could", "should",
            "about", "into", "over", "after", "before", "without", "also",
        }
        return [w for w in words if w not in stop_words]

    def save_event(self, event: EpisodicEvent):
        with self._lock:
            self._events.append(event)
            # Evict oldest if over cap (prevent unbounded growth)
            if len(self._events) > self._MAX_EVENTS:
                self._events = self._events[-self._MAX_EVENTS:]

    def get_events(self, event_type: str = "", limit: int = 10) -> list[EpisodicEvent]:
        with self._lock:
            events = list(reversed(self._events))
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[:limit]

    def close(self):
        self._save_to_json()


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

class SQLiteMemoryBackend(MemoryBackend):
    """Persistent memory using SQLite.

    Uses a single shared connection with `check_same_thread=False` for
    pooling. Concurrent callers (bot + scheduler + cognition) must be
    serialized via `_wlock` — without it, writers surface as
    `SystemError` / `OperationalError: cannot commit` and silently drop
    rows (same failure class as KL-4 on DatabaseManager).
    """

    def __init__(self, db_path: str = "data/memory.db"):
        # Initialize base state (_conversations, _lock, _profiles, ...) so the
        # inherited conversation API (store/retrieve/search/delete) works —
        # this subclass does not override those methods.
        super().__init__()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._wlock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            self._init_tables()
        except sqlite3.DatabaseError as exc:
            corruption_markers = ("malformed", "not a database", "file is encrypted")
            if not any(marker in str(exc).lower() for marker in corruption_markers):
                raise
            self._quarantine_corrupt_database(exc)
            self._init_tables()

    def _quarantine_corrupt_database(self, exc: sqlite3.DatabaseError) -> None:
        """Preserve a corrupt SQLite memory database before rebuilding it.

        The bot can reconstruct conversational working memory from its JSON
        fallback. Keeping the damaged database beside the fresh one allows a
        later manual recovery attempt without preventing the bot from starting.
        """
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        quarantine = self.db_path.with_name(f"{self.db_path.stem}.corrupt-{stamp}{self.db_path.suffix}")
        try:
            if self.db_path.exists():
                self.db_path.replace(quarantine)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self.db_path}{suffix}")
                if sidecar.exists():
                    sidecar.unlink()
        except OSError as rename_exc:
            raise RuntimeError(
                f"Could not quarantine corrupt memory database {self.db_path}: {rename_exc}"
            ) from rename_exc

        logger.error(
            "Memory database was malformed and was quarantined at %s: %s",
            quarantine,
            exc,
        )

    @contextmanager
    def _locked_conn(self) -> Iterator[sqlite3.Connection]:
        """Yield the shared connection under the write lock."""
        with self._wlock:
            yield self._conn

    def _init_tables(self):
        with self._wlock:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            conn = self._conn
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    text TEXT NOT NULL,
                    source TEXT,
                    tags TEXT,
                    embedding TEXT,
                    timestamp REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    timestamp REAL,
                    event_type TEXT,
                    description TEXT,
                    participants TEXT,
                    outcome TEXT,
                    sentiment REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp)")
            conn.commit()

    # -- User profiles --

    def save_user_profile(self, profile: UserProfile):
        payload = json.dumps(asdict(profile))
        with self._locked_conn() as conn:
            conn.execute(
                "INSERT INTO user_profiles (user_id, data) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET data = ?",
                (profile.user_id, payload, payload),
            )
            conn.commit()

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT data FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row:
            return UserProfile(**json.loads(row[0]))
        return None

    def get_or_create_profile(self, user_id: str, user_name: str = "") -> UserProfile:
        profile = self.get_user_profile(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id, user_name=user_name)
        return profile

    # -- Memories --

    def save_memory(self, text: str, user_id: str, source: str = "",
                    tags: list[str] | None = None, embedding: list[float] | None = None) -> str:
        import uuid
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        with self._locked_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memories "
                "(id, user_id, text, source, tags, embedding, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    mem_id,
                    user_id,
                    text,
                    source,
                    json.dumps(tags or []),
                    json.dumps(embedding) if embedding else None,
                    time.time(),
                ),
            )
            conn.commit()
        return mem_id

    def query_memories(self, user_id: str = "", tags: list[str] | None = None, limit: int = 10) -> list[dict]:
        """Return up to `limit` memories matching optional user/tag filters.

        BUG FIX: Tag filtering used to run *after* SQL LIMIT, so a request
        for limit=10 with tags=["x"] could return 0 rows even when matching
        rows existed beyond the newest 10. When tags are set we over-fetch
        then filter in Python until `limit` matches are collected.
        """
        # Over-fetch when post-filtering by tags so LIMIT is applied to matches.
        fetch_limit = limit
        if tags:
            fetch_limit = min(max(limit * 20, 100), 2000)

        with self._locked_conn() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT id, text, source, tags, timestamp FROM memories "
                    "WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (user_id, fetch_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, text, source, tags, timestamp FROM memories "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (fetch_limit,),
                ).fetchall()

        results = []
        for row in rows:
            try:
                mem_tags = json.loads(row[3]) if row[3] else []
            except (json.JSONDecodeError, TypeError):
                mem_tags = []
            if tags and not any(t in mem_tags for t in tags):
                continue
            results.append({
                "id": row[0],
                "text": row[1],
                "source": row[2],
                "tags": mem_tags,
                "timestamp": row[4],
            })
            if len(results) >= limit:
                break
        return results

    def search_memories(self, query: str, user_id: str = "", limit: int = 5) -> list[dict]:
        """Search memories by keyword relevance using SQL LIKE matching."""
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        conditions = " AND ".join(["text LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]

        sql = f"SELECT id, user_id, text, source, tags, timestamp FROM memories WHERE {conditions}"
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._locked_conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "id": r[0], "user_id": r[1], "text": r[2],
                "source": r[3],
                "tags": json.loads(r[4]) if r[4] else [],
                "timestamp": r[5],
            }
            for r in rows
        ]

    # -- Events --

    def save_event(self, event: EpisodicEvent):
        with self._locked_conn() as conn:
            conn.execute(
                "INSERT INTO events (event_id, timestamp, event_type, description, "
                "participants, outcome, sentiment) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.timestamp,
                    event.event_type,
                    event.description,
                    json.dumps(event.participants),
                    event.outcome,
                    event.sentiment,
                ),
            )
            conn.commit()

    def get_events(self, event_type: str = "", limit: int = 10) -> list[EpisodicEvent]:
        with self._locked_conn() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT event_id, timestamp, event_type, description, participants, "
                    "outcome, sentiment FROM events WHERE event_type = ? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT event_id, timestamp, event_type, description, participants, "
                    "outcome, sentiment FROM events ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            EpisodicEvent(
                event_id=r[0],
                timestamp=r[1],
                event_type=r[2],
                description=r[3],
                participants=json.loads(r[4]) if r[4] else [],
                outcome=r[5],
                sentiment=r[6],
            )
            for r in rows
        ]

    def close(self):
        """Close database connection and commit any pending changes."""
        if hasattr(self, "_conn") and self._conn:
            try:
                with self._wlock:
                    self._conn.commit()
                    self._conn.close()
                logger.info("[memory] SQLite connection closed")
            except Exception as e:
                logger.error(f"[memory] Failed to close connection: {e}")


# ---------------------------------------------------------------------------
# Redis implementation (optional)
# ---------------------------------------------------------------------------

class RedisMemoryBackend(MemoryBackend):
    """Memory backend using Redis (requires redis-py)."""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        try:
            import redis
            self.client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            self.client.ping()
        except Exception as exc:
            raise RuntimeError(f"Redis connection failed: {exc}") from exc

    def save_user_profile(self, profile: UserProfile):
        self.client.hset("user_profiles", profile.user_id, json.dumps(asdict(profile)))

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        data = self.client.hget("user_profiles", user_id)
        return UserProfile(**json.loads(data)) if data else None

    def save_memory(self, text: str, user_id: str, source: str = "",
                    tags: list[str] | None = None, embedding: list[float] | None = None) -> str:
        import uuid
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        self.client.hset("memories", mem_id, json.dumps({
            "user_id": user_id, "text": text, "source": source,
            "tags": tags or [], "timestamp": time.time()
        }))
        return mem_id

    def query_memories(self, user_id: str = "", tags: list[str] | None = None, limit: int = 10) -> list[dict]:
        items = self.client.hgetall("memories")
        results = []
        for _k, v in items.items():
            d = json.loads(v)
            if user_id and d.get("user_id") != user_id:
                continue
            if tags and not any(t in d.get("tags", []) for t in tags):
                continue
            results.append(d)
        return results[:limit]

    def save_event(self, event: EpisodicEvent):
        self.client.lpush("events", json.dumps(asdict(event)))

    def get_events(self, event_type: str = "", limit: int = 10) -> list[EpisodicEvent]:
        raw = self.client.lrange("events", 0, limit - 1)
        events = [EpisodicEvent(**json.loads(r)) for r in raw]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

class InMemoryMemoryBackend(MemoryBackend):
    """In-memory backend with JSON file persistence at data/memory_v2.json."""

    def __init__(self):
        super().__init__()
        self.profiles: dict[str, UserProfile] = {}
        self.memories: list[dict] = []
        self.events: list[EpisodicEvent] = []
        self._set_json_path("data/memory_v2.json")
        self._load_inmemory_from_json()

    def _load_inmemory_from_json(self):
        if self._json_path and self._json_path.exists():
            try:
                with open(self._json_path, encoding="utf-8") as f:
                    data = json.load(f)
                raw_memories = data.get("memories", [])
                if raw_memories:
                    self.memories = raw_memories
                raw_events = data.get("events", [])
                if raw_events:
                    self.events = [EpisodicEvent(**e) for e in raw_events]
                logger.info("Loaded %d memories, %d events from %s",
                            len(self.memories), len(self.events), self._json_path)
            except Exception as e:
                logger.warning("Failed to load InMemory data from %s: %s", self._json_path, e)

    def save_user_profile(self, profile: UserProfile):
        with self._lock:
            self.profiles[profile.user_id] = profile

    def get_user_profile(self, user_id: str) -> UserProfile | None:
        with self._lock:
            return self.profiles.get(user_id)

    def save_memory(self, text: str, user_id: str, source: str = "",
                    tags: list[str] | None = None, embedding: list[float] | None = None) -> str:
        mem_id = f"mem_{time.time():.6f}"
        with self._lock:
            self.memories.append({
                "id": mem_id, "user_id": user_id, "text": text,
                "source": source, "tags": tags or [], "timestamp": time.time()
            })
            self.store(user_id, text)
        return mem_id

    def query_memories(self, user_id: str = "", tags: list[str] | None = None, limit: int = 10) -> list[dict]:
        with self._lock:
            snapshot = list(reversed(self.memories))
        results = []
        for m in snapshot:
            if user_id and m.get("user_id") != user_id:
                continue
            if tags and not any(t in m.get("tags", []) for t in tags):
                continue
            results.append(m)
            if len(results) >= limit:
                break
        return results

    def save_event(self, event: EpisodicEvent):
        with self._lock:
            self.events.append(event)

    def get_events(self, event_type: str = "", limit: int = 10) -> list[EpisodicEvent]:
        with self._lock:
            events = list(reversed(self.events))
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events[:limit]

    def close(self):
        with self._lock:
            try:
                tmp = self._json_path.with_suffix('.tmp')
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({
                        "conversations": self._conversations,
                        "memories": self.memories,
                        "events": [asdict(e) for e in self.events],
                    }, f, indent=2)
                tmp.replace(self._json_path)
            except Exception as e:
                logger.warning("Failed to save InMemory data: %s", e)
        logger.info("Persisted memory to %s", self._json_path)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_memory_backend(backend_type: str = "sqlite", **kwargs) -> MemoryBackend:
    """
    Factory for memory backends.

    Usage:
        backend = create_memory_backend("sqlite", db_path="data/memory.db")
        backend = create_memory_backend("redis", host="localhost", port=6379)
        backend = create_memory_backend("memory")
    """
    backend_type = backend_type.lower()
    if backend_type == "sqlite":
        return SQLiteMemoryBackend(**kwargs)
    elif backend_type == "redis":
        return RedisMemoryBackend(**kwargs)
    elif backend_type in ("memory", "inmemory", "in_memory"):
        return InMemoryMemoryBackend()
    else:
        raise ValueError(f"Unknown memory backend: {backend_type}")
