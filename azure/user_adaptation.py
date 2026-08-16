"""
Azure User Personality Adaptation System

Learns each user's communication style and adapts responses accordingly.
Tracks: formality, technical depth, verbosity, humor, and topic preferences.
"""

from __future__ import annotations

import time

from .memory_backend import MemoryBackend, UserProfile, create_memory_backend


class UserAdaptation:
    """
    Adaptive personality system that learns from user interactions.

    Usage:
        adaptation = UserAdaptation(backend)
        profile = adaptation.get_profile("123", "Alice")
        adapted = adaptation.adapt_response("Here's the code.", profile)
        # -> "Yo, check this out." (if Alice is casual)
    """

    def __init__(self, backend: MemoryBackend | None = None):
        self.backend = backend or create_memory_backend("memory")

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def get_profile(self, user_id: str, user_name: str = "") -> UserProfile:
        """Get or create a user profile."""
        if hasattr(self.backend, 'get_or_create_profile'):
            return self.backend.get_or_create_profile(user_id, user_name)
        profile = self.backend.get_user_profile(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id, user_name=user_name)
        return profile

    def save_profile(self, profile: UserProfile):
        """Persist a user profile."""
        self.backend.save_user_profile(profile)

    # ------------------------------------------------------------------
    # Learning from interactions
    # ------------------------------------------------------------------

    def learn_from_message(self, user_id: str, message: str, user_name: str = ""):
        """Analyze a user's message and update their profile."""
        profile = self.get_profile(user_id, user_name)
        msg_lower = message.strip().lower()

        # Update interaction stats
        profile.total_interactions += 1
        profile.last_interaction = time.time()

        # Detect style
        if any(w in msg_lower for w in ["lol", "lmao", "haha", "xd", "bruh", "fam"]):
            profile.communication_style = "casual"
        elif any(w in msg_lower for w in ["please", "could you", "would you mind", "kindly"]):
            profile.communication_style = "formal"
        elif any(w in msg_lower for w in ["function", "class", "def ", "import ", "api", "debug"]):
            profile.communication_style = "technical"

        # Detect verbosity
        word_count = len(message.split())
        if word_count > 50:
            profile.verbosity = "verbose"
        elif word_count < 10:
            profile.verbosity = "concise"
        else:
            profile.verbosity = "normal"

        # Detect expertise
        technical_markers = ["kubernetes", "docker", "aws", "asyncio", "terraform", "ci/cd", "graphql", "microservice"]
        if any(m in msg_lower for m in technical_markers):
            profile.expertise_level = "advanced"
        elif any(m in msg_lower for m in ["python", "javascript", "html", "css", "sql"]):
            profile.expertise_level = "intermediate"

        # Extract topics
        topics = self._extract_topics(message)
        for topic in topics:
            if topic not in profile.preferred_topics:
                profile.preferred_topics.append(topic)
                # Keep only top 20 topics
                profile.preferred_topics = profile.preferred_topics[-20:]

        self.save_profile(profile)

    def learn_from_feedback(self, user_id: str, feedback: str, user_name: str = ""):
        """Learn from explicit feedback (thumbs up/down, corrections)."""
        profile = self.get_profile(user_id, user_name)

        if feedback in ("up", "good", "like", "yes", "correct"):
            profile.thumbs_up += 1
        elif feedback in ("down", "bad", "dislike", "no", "wrong"):
            profile.thumbs_down += 1
            profile.corrections_received += 1

        self.save_profile(profile)

    # ------------------------------------------------------------------
    # Response adaptation
    # ------------------------------------------------------------------

    def adapt_response(self, text: str, profile: UserProfile) -> str:
        """
        Adapt a response to match the user's style.
        """
        style = profile.communication_style
        verbosity = profile.verbosity
        expertise = profile.expertise_level

        adapted = text

        # Adjust formality
        if style == "casual":
            adapted = self._make_casual(adapted)
        elif style == "formal":
            adapted = self._make_formal(adapted)

        # Adjust length
        if verbosity == "concise":
            adapted = self._make_concise(adapted)
        elif verbosity == "verbose":
            adapted = self._make_verbose(adapted)

        # Adjust technical depth
        if expertise == "beginner":
            adapted = self._simplify_technical(adapted)

        return adapted

    def adapt_prompt(self, base_prompt: str, profile: UserProfile) -> str:
        """Adapt a system prompt to match user style."""
        modifiers = []

        if profile.communication_style == "casual":
            modifiers.append("Be casual and conversational. Use contractions.")
        elif profile.communication_style == "formal":
            modifiers.append("Be formal and polite. Use complete sentences.")
        elif profile.communication_style == "technical":
            modifiers.append("Be precise and technical. Include specifics where relevant.")

        if profile.verbosity == "concise":
            modifiers.append("Keep responses brief and to the point.")
        elif profile.verbosity == "verbose":
            modifiers.append("Provide detailed explanations with examples.")

        if profile.expertise_level == "beginner":
            modifiers.append("Explain concepts simply, avoiding jargon.")
        elif profile.expertise_level == "expert":
            modifiers.append("Assume advanced knowledge. Be concise and technical.")

        if modifiers:
            return base_prompt + "\n\n" + "\n".join(f"- {m}" for m in modifiers)
        return base_prompt

    # ------------------------------------------------------------------
    # Style transformers
    # ------------------------------------------------------------------

    def _make_casual(self, text: str) -> str:
        """Convert to casual tone."""
        replacements = {
            "Hello": "Hey",
            "Please": "",
            "Would you mind": "Can you",
            "Could you": "Can you",
            "I am": "I'm",
            "You are": "You're",
            "It is": "It's",
            "That is": "That's",
        }
        for formal, casual in replacements.items():
            text = text.replace(formal, casual)
        return text

    def _make_formal(self, text: str) -> str:
        """Convert to formal tone."""
        replacements = {
            "Hey": "Hello",
            "I'm": "I am",
            "You're": "You are",
            "It's": "It is",
            "That's": "That is",
            "Can't": "Cannot",
            "Don't": "Do not",
        }
        for casual, formal in replacements.items():
            text = text.replace(casual, formal)
        return text

    def _make_concise(self, text: str) -> str:
        """Shorten response to essentials."""
        sentences = text.split(". ")
        if len(sentences) > 2:
            return ". ".join(sentences[:2]) + "."
        return text

    def _make_verbose(self, text: str) -> str:
        """Expand with more detail (placeholder)."""
        return text  # In production, would add examples and elaboration

    def _simplify_technical(self, text: str) -> str:
        """Simplify jargon for beginners."""
        jargon_map = {
            "API": "external service interface",
            "database": "data storage system",
            "async": "non-blocking (doesn't wait)",
            "container": "isolated environment",
        }
        for term, simple in jargon_map.items():
            text = text.replace(term, simple)
        return text

    # ------------------------------------------------------------------
    # Topic extraction
    # ------------------------------------------------------------------

    def _extract_topics(self, text: str) -> list[str]:
        """Extract potential topics from a message."""
        topics = []
        # Code languages
        for lang in ["python", "javascript", "typescript", "rust", "go", "java", "c++", "c#"]:
            if lang in text.lower():
                topics.append(lang)
        # Frameworks
        for fw in ["react", "vue", "angular", "django", "flask", "fastapi", "express"]:
            if fw in text.lower():
                topics.append(fw)
        # Concepts
        for concept in ["docker", "kubernetes", "aws", "azure", "machine learning", "ai", "blockchain"]:
            if concept in text.lower():
                topics.append(concept)
        return topics

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return adaptation statistics."""
        return {
            "total_profiles": len(self.backend.profiles) if hasattr(self.backend, 'profiles') else "unknown",
        }
