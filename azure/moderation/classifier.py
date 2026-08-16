"""
Azure Moderation: Message Classifier

Hybrid detection engine combining rule-based heuristics (primary)
with optional model-based classification (secondary, for v2+).

Designed for immediate deployment on CPU with zero LLM inference cost.
All rule-based detection is synchronous and fast.

Severity levels:
  NONE    -> normal message
  LOW     -> suspicious but not actionable (log only)
  MEDIUM  -> actionable with warning/deletion
  HIGH    -> immediate timeout/kick
  CRITICAL -> ban + report to admin
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class ClassificationResult:
    """Outcome of message classification."""
    severity: Severity
    category: str           # e.g. "spam", "scam", "toxicity", "normal"
    reason: str             # human-readable explanation
    scores: dict[str, float] = field(default_factory=dict)  # raw scores per detector
    confidence: float = 0.0  # 0-1, higher = more certain
    suggested_action: str = "none"


class MessageClassifier:
    """
    Rule-based message classifier with optional AI signal.

    Primary signals (fast, deterministic):
      - Spam: repetition, excessive links, rapid-fire messages
      - Scam: suspicious domains, known scam keywords, impersonation
      - Toxicity: excessive caps, excessive mentions, profanity patterns

    Secondary signal (optional, requires model):
      - AI toxicity score from v2 model (when available)
    """

    # Known scam indicators (lowercase)
    SCAM_KEYWORDS = [
        "free nitro", "free robux", "free vbucks", "free steam", "free gift",
        "claim your prize", "you won", "click here to claim", "verify your account",
        "double your crypto", "send crypto", "wallet verification",
        "discord.gg/nitro", "discord.com/nitro", "discord.gift",
        "steamcommunity.ru", "steancomunnity", "discordapp.ru",
        "@everyone free", "@here free", "dm me for",
        "airdrop", "whitelist", "pre-sale", "presale", "minting now",
        "investment opportunity", "guaranteed profit", "100% legit",
    ]

    # Suspicious TLDs and domain patterns
    SUSPICIOUS_DOMAINS = [
        ".ru", ".tk", ".ml", ".ga", ".cf", ".gq",
        "discordnitro", "freenitro", "freerobux", "discordapp", "steamcommunity",
    ]

    # Excessive caps threshold (percentage of alphabetic chars that are uppercase)
    CAPS_THRESHOLD = 0.70
    CAPS_MIN_LEN = 10   # only trigger if message has at least this many letters

    # Link thresholds
    MAX_LINKS_NORMAL = 2
    MAX_LINKS_SUSPICIOUS = 4

    # Repetition threshold (how similar messages need to be to count as spam)
    REPEAT_WINDOW = 60  # seconds
    REPEAT_MAX = 3      # more than this in the window = spam

    # Mention thresholds
    MAX_MENTIONS = 5
    MAX_EVERYONE_HERE = 1

    # Excessive emoji/spam chars
    SPAM_CHAR_THRESHOLD = 0.60

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self):
        self.scam_re = re.compile(
            r"|".join(re.escape(k) for k in self.SCAM_KEYWORDS),
            re.IGNORECASE,
        )
        self.url_re = re.compile(
            r"https?://[^\s]+|www\.[^\s]+|discord\.gg/[^\s]+|discord\.com/[^\s]+",
            re.IGNORECASE,
        )
        self.mention_re = re.compile(r"<@!?\d+>")
        self.everyone_here_re = re.compile(r"@(everyone|here)")
        self.letter_re = re.compile(r"[a-zA-Z]")
        self.upper_re = re.compile(r"[A-Z]")

    def classify(self, text: str, author_id: str | None = None,
                 recent_messages: list[dict] | None = None) -> ClassificationResult:
        """
        Main entry point. Classify a single message.

        recent_messages: list of recent messages from the same author,
                         used for repetition detection.
        """
        scores = {}

        # Run all detectors
        spam_score, spam_reason = self._detect_spam(text, recent_messages or [])
        scam_score, scam_reason = self._detect_scam(text)
        tox_score, tox_reason = self._detect_toxicity(text)

        scores["spam"] = spam_score
        scores["scam"] = scam_score
        scores["toxicity"] = tox_score

        # Determine dominant category and severity
        max_score = max(spam_score, scam_score, tox_score)
        if max_score == 0.0:
            return ClassificationResult(
                severity=Severity.NONE,
                category="normal",
                reason="No rule-based signals triggered.",
                scores=scores,
                confidence=0.0,
            )

        # Map to severity
        if scam_score >= 0.8 or spam_score >= 0.9 or tox_score >= 0.9:
            severity = Severity.CRITICAL
        elif scam_score >= 0.5 or spam_score >= 0.7 or tox_score >= 0.7:
            severity = Severity.HIGH
        elif max_score >= 0.4:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Pick dominant category
        if scam_score >= spam_score and scam_score >= tox_score:
            category = "scam"
            reason = scam_reason
        elif spam_score >= tox_score:
            category = "spam"
            reason = spam_reason
        else:
            category = "toxicity"
            reason = tox_reason

        suggested = self._suggest_action(severity, category)

        return ClassificationResult(
            severity=severity,
            category=category,
            reason=reason,
            scores=scores,
            confidence=min(max_score, 1.0),
            suggested_action=suggested,
        )

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    @staticmethod
    def _is_spam_char(c: str) -> bool:
        """True for emoji / decorative symbols, False for letters and normal
        punctuation. Letters of ANY script (Latin, Han, Cyrillic, Arabic, …)
        are never spam characters, so a foreign-language message is not
        misclassified as spam."""
        if c.isspace():
            return False
        cat = unicodedata.category(c)
        # L* = letters, M* = combining marks, Nd = decimal digits: all "real"
        # text content. Common ASCII punctuation is also fine.
        if cat[0] in ("L", "M") or cat == "Nd":
            return False
        if ord(c) < 128:
            # ASCII symbols/punctuation are normal in ordinary messages.
            return False
        # Remaining: non-ASCII symbols (So/Sk/Sm/Sc), pictographs, emoji, and
        # unusual punctuation — these are what "excessive emoji spam" means.
        return True

    def _detect_spam(self, text: str, recent: list[dict]) -> tuple[float, str]:
        """Score spam likelihood. Returns (score, reason)."""
        score = 0.0
        reasons = []

        links = self.url_re.findall(text)
        n_links = len(links)

        if n_links > self.MAX_LINKS_SUSPICIOUS:
            score += 0.60
            reasons.append(f"excessive links ({n_links})")
        elif n_links > self.MAX_LINKS_NORMAL:
            score += 0.30
            reasons.append(f"multiple links ({n_links})")

        # Repetition check
        if recent:
            identical_count = sum(
                1 for m in recent if m.get("content", "") == text
            )
            if identical_count >= self.REPEAT_MAX:
                score += 0.70
                reasons.append(f"repeated {identical_count} times")
            elif identical_count >= 2:
                score += 0.40
                reasons.append(f"repeated {identical_count} times")

        # Excessive emoji / symbol spam. Count symbols and emoji, NOT letters:
        # a plain `ord(c) > 127` test flags every non-Latin script (Chinese,
        # Russian, Arabic, …) as spam. Use Unicode categories so letters of
        # any language are exempt while emoji/symbols still count.
        total_chars = len(text)
        if total_chars > 0:
            spam_chars = sum(1 for c in text if self._is_spam_char(c))
            if spam_chars / total_chars > self.SPAM_CHAR_THRESHOLD:
                score += 0.50
                reasons.append("excessive emoji/spam characters")

        # Very short + link = suspicious
        if len(text) < 30 and n_links > 0:
            score += 0.30
            reasons.append("short message with link")

        return min(score, 1.0), "; ".join(reasons) if reasons else ""

    def _detect_scam(self, text: str) -> tuple[float, str]:
        """Score scam likelihood. Returns (score, reason)."""
        score = 0.0
        reasons = []
        lower = text.lower()

        # Scam keyword matches
        scam_hits = self.scam_re.findall(text)
        if scam_hits:
            score += min(0.40 + 0.20 * len(scam_hits), 0.90)
            reasons.append(f"scam keywords: {scam_hits[:3]}")

        # Suspicious domains
        for domain in self.SUSPICIOUS_DOMAINS:
            if domain.lower() in lower:
                score += 0.30
                reasons.append(f"suspicious domain: {domain}")
                break

        # Fake Discord / Steam impersonation
        if "discord" in lower and "nitro" in lower and "http" in lower:
            score += 0.40
            reasons.append("fake Discord Nitro link")
        if "steam" in lower and ("gift" in lower or "free" in lower):
            score += 0.30
            reasons.append("fake Steam gift")

        # @everyone / @here + link
        eh = self.everyone_here_re.findall(text)
        if eh and self.url_re.search(text):
            score += 0.50
            reasons.append("@everyone/@here with link")

        # DM solicitation
        if "dm me" in lower and ("free" in lower or "gift" in lower or "prize" in lower):
            score += 0.35
            reasons.append("DM solicitation for free item")

        return min(score, 1.0), "; ".join(reasons) if reasons else ""

    def _detect_toxicity(self, text: str) -> tuple[float, str]:
        """Score toxicity. Returns (score, reason)."""
        score = 0.0
        reasons = []

        letters = self.letter_re.findall(text)
        if len(letters) >= self.CAPS_MIN_LEN:
            uppers = self.upper_re.findall(text)
            caps_ratio = len(uppers) / len(letters)
            if caps_ratio > self.CAPS_THRESHOLD:
                score += 0.40
                reasons.append(f"excessive caps ({caps_ratio:.0%})")

        mentions = self.mention_re.findall(text)
        if len(mentions) > self.MAX_MENTIONS:
            score += 0.30
            reasons.append(f"excessive mentions ({len(mentions)})")

        eh = self.everyone_here_re.findall(text)
        if len(eh) > self.MAX_EVERYONE_HERE:
            score += 0.20
            reasons.append(f"excessive @everyone/@here ({len(eh)})")

        # Length check — extremely short with no context
        if len(text.strip()) < 5 and text.strip() in ("k", "ok", "no", "yes", "what", "why"):
            # Not toxic, just low effort
            pass

        return min(score, 1.0), "; ".join(reasons) if reasons else ""

    def _suggest_action(self, severity: Severity, category: str) -> str:
        mapping = {
            Severity.LOW: "log",
            Severity.MEDIUM: "warn",
            Severity.HIGH: "timeout",
            Severity.CRITICAL: "ban",
        }
        if category == "spam":
            if severity == Severity.HIGH:
                return "delete + timeout"
            if severity == Severity.CRITICAL:
                return "delete + ban"
        if category == "scam" and severity in (Severity.HIGH, Severity.CRITICAL):
            return "delete + ban"
        return mapping.get(severity, "none")
