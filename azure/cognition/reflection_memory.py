"""
ReflectionMemory — Upgrade 6: Self-Learning Reflection System

Stores Azure's own reasoning failures and corrections so it doesn't repeat
mistakes across sessions. Indexed by message pattern hash for fast retrieval.

Storage: JSON file in logs/reflection/ with in-memory LRU cache.

Reflection categories:
  - intent_misclassification: wrong intent was assigned
  - tool_mismatch: wrong tool was chosen
  - plan_failure: plan failed at execution
  - risky_output: adversarial review triggered
  - confidence_miscalibration: heuristic was wrong
  - success_pattern: something worked well and should be repeated
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("azure.cognition.reflection_memory")


# ---------------------------------------------------------------------------
# Reflection dataclass
# ---------------------------------------------------------------------------

@dataclass
class Reflection:
    """A single reflection — one learned experience."""
    reflection_id: str = ""                     # unique hash-based ID
    message_pattern: str = ""                   # normalized message pattern
    true_intent: str = ""                       # what the user actually wanted
    predicted_intent: str = ""                  # what Azure thought they wanted
    correction: str = ""                        # what should have been done instead
    category: str = "general"                   # intent_misclassification | tool_mismatch | plan_failure | risky_output | confidence_miscalibration | success_pattern
    score: int = 50                             # 0-100, higher = more valuable to remember
    context: dict = field(default_factory=dict) # extra metadata (modes, tools, risk, etc.)
    timestamp: float = 0.0                      # when this reflection was created
    access_count: int = 0                       # how many times retrieved (for LRU eviction)
    last_accessed: float = 0.0                  # timestamp of last retrieval

    def __post_init__(self):
        if not self.reflection_id:
            self.reflection_id = self._generate_id()
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if self.last_accessed == 0.0:
            self.last_accessed = self.timestamp

    def _generate_id(self) -> str:
        """Generate a stable hash ID from pattern + category + timestamp."""
        base = f"{self.message_pattern}:{self.category}:{self.timestamp}"
        return hashlib.sha256(base.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Reflection:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ReflectionMemory storage
# ---------------------------------------------------------------------------

class ReflectionMemory:
    """
    Persistent reflection storage with LRU cache.

    Design:
      - All reflections stored in logs/reflection/reflections.json
      - In-memory OrderedDict provides LRU eviction
      - Only high-value reflections (score >= threshold) are stored
      - Pattern-based retrieval for fast lookup
    """

    DEFAULT_THRESHOLD = 60        # Only store reflections with score >= 60
    MAX_REFLECTIONS = 200         # Max reflections in memory (LRU eviction)
    DEFAULT_LOG_DIR = "logs/reflection"

    def __init__(self, log_dir: str | Path = DEFAULT_LOG_DIR, threshold: int = DEFAULT_THRESHOLD):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.reflections_file = self.log_dir / "reflections.json"
        self.threshold = threshold

        # In-memory LRU cache: OrderedDict preserves insertion order for eviction.
        # Mutation now goes through `_lock` so concurrent calls from
        # async tasks cannot race on `move_to_end`/eviction.
        self._cache: OrderedDict[str, Reflection] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {"stored": 0, "retrieved": 0, "rejected_low_score": 0, "evicted": 0}

        self._load()

    # -----------------------------------------------------------------------
    # Storage
    # -----------------------------------------------------------------------

    def add(self, reflection: Reflection) -> bool:
        """
        Store a reflection if it meets the quality threshold.

        Returns:
            True if stored, False if rejected (score too low or duplicate).
        """
        if reflection.score < self.threshold:
            self._stats["rejected_low_score"] += 1
            return False

        with self._lock:
            # Deduplicate: if same pattern+category exists, keep the higher score
            existing = self._find_by_pattern(reflection.message_pattern, reflection.category)
            if existing:
                if reflection.score <= existing.score:
                    return False  # existing is better or equal
                self._remove(existing.reflection_id)

            if len(self._cache) >= self.MAX_REFLECTIONS:
                self._evict_oldest()

            self._cache[reflection.reflection_id] = reflection
            self._cache.move_to_end(reflection.reflection_id)
            self._stats["stored"] += 1
            self._save()
        return True

    def _remove(self, reflection_id: str):
        """Remove a reflection by ID."""
        if reflection_id in self._cache:
            del self._cache[reflection_id]

    def _evict_oldest(self):
        """Evict the least-recently-used reflection."""
        if self._cache:
            oldest_id = next(iter(self._cache))  # First item = oldest
            del self._cache[oldest_id]
            self._stats["evicted"] += 1

    def _find_by_pattern(self, pattern: str, category: str) -> Reflection | None:
        """Find an existing reflection by exact pattern+category."""
        for r in self._cache.values():
            if r.message_pattern == pattern and r.category == category:
                return r
        return None

    # -----------------------------------------------------------------------
    # Retrieval
    # -----------------------------------------------------------------------

    def retrieve(self, query_pattern: str, k: int = 3) -> list[Reflection]:
        """
        Retrieve relevant reflections matching a message pattern.

        Uses substring matching for simplicity. Returns top-k by score.
        """
        query = query_pattern.lower()
        matches = []
        accessed_ids = []

        with self._lock:
            for r in list(self._cache.values()):
                pattern = r.message_pattern.lower()
                if any(word in pattern for word in query.split() if len(word) > 3):
                    r.access_count += 1
                    r.last_accessed = time.time()
                    accessed_ids.append(r.reflection_id)
                    matches.append(r)

            for rid in accessed_ids:
                self._cache.move_to_end(rid)

        self._stats["retrieved"] += len(matches)
        matches.sort(key=lambda r: (r.score, r.last_accessed), reverse=True)
        return matches[:k]

    def retrieve_by_category(self, category: str, k: int = 5) -> list[Reflection]:
        """Retrieve reflections of a specific category."""
        with self._lock:
            matches = [r for r in list(self._cache.values()) if r.category == category]
        matches.sort(key=lambda r: r.score, reverse=True)
        return matches[:k]

    def get_all(self) -> list[Reflection]:
        """Return all reflections sorted by score."""
        with self._lock:
            return sorted(list(self._cache.values()), key=lambda r: r.score, reverse=True)

    def get_stats(self) -> dict:
        """Return memory statistics."""
        return {
            **self._stats,
            "total_in_memory": len(self._cache),
            "threshold": self.threshold,
            "avg_score": sum(r.score for r in self._cache.values()) / len(self._cache) if self._cache else 0,
        }

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _save(self):
        """Save all reflections to disk."""
        data = [r.to_dict() for r in self._cache.values()]
        try:
            tmp = self.reflections_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.reflections_file)
        except Exception as e:
            logger.error(f"[reflection_memory] save error: {e}")


    def _load(self):
        """Load reflections from disk into cache."""
        if not self.reflections_file.exists():
            return
        try:
            data = json.loads(self.reflections_file.read_text(encoding="utf-8"))
            for item in data:
                r = Reflection.from_dict(item)
                self._cache[r.reflection_id] = r
            self._stats["stored"] = len(self._cache)
        except Exception as e:
            logger.error(f"[reflection_memory] load error: {e}")


    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def cleanup(self, max_age_days: int = 30, min_score: int = 50):
        """Remove old, low-value reflections."""
        cutoff = time.time() - (max_age_days * 86400)
        to_remove = [
            r_id for r_id, r in self._cache.items()
            if r.timestamp < cutoff and r.score < min_score and r.access_count < 2
        ]
        for r_id in to_remove:
            del self._cache[r_id]
            self._stats["evicted"] += 1
        if to_remove:
            self._save()

    def __len__(self):
        return len(self._cache)

    def __bool__(self):
        """Always truthy — empty cache doesn't mean the memory is falsy."""
        return True
