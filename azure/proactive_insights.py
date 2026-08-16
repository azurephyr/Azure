"""
Azure Proactive Insights Engine

This is what makes Azure "alive" and helpful - not just reactive, but PROACTIVE.

The engine continuously analyzes server state and generates intelligent suggestions:
- "Channel #general is getting crowded - create topical channels?"
- "User @John has been very active - consider giving them a helper role?"
- "Engagement dropped 40% this week - run an event?"
- "3 users left after negative interactions - check moderation?"
- "Voice channels unused - promote voice chat activities?"

Features:
- Pattern detection (trends, anomalies, opportunities)
- LLM-powered contextual recommendations
- Priority scoring (urgent vs nice-to-have)
- Actionable suggestions with specific steps
- Learning from admin feedback (what worked, what didn't)

This makes Azure feel like a proactive community manager, not just a bot.

Usage:
    insights = ProactiveInsights(llm, awareness_engine)
    suggestions = await insights.generate_suggestions(guild_id)

    for suggestion in suggestions[:3]:  # Top 3
        await channel.send(f"💡 {suggestion.title}: {suggestion.description}")
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("azure.proactive_insights")


class InsightType(Enum):
    """Types of proactive insights."""
    ENGAGEMENT = "engagement"              # Activity/participation
    MODERATION = "moderation"              # Safety/rule enforcement
    ORGANIZATION = "organization"          # Channel/role structure
    COMMUNITY = "community"                # Social health, conflicts
    CONTENT = "content"                    # Topics, conversations
    GROWTH = "growth"                      # Member retention, acquisition
    TECHNICAL = "technical"                # Bot config, permissions


class Priority(Enum):
    """Insight priority levels."""
    LOW = "low"           # Nice to have
    MEDIUM = "medium"     # Should address soon
    HIGH = "high"         # Important, take action
    URGENT = "urgent"     # Requires immediate attention


@dataclass
class Suggestion:
    """A single proactive suggestion."""
    suggestion_id: str
    type: InsightType
    priority: Priority

    title: str
    description: str
    reasoning: str

    # Actionable steps
    recommended_actions: list[str] = field(default_factory=list)

    # Context
    evidence: list[str] = field(default_factory=list)
    affected_users: list[str] = field(default_factory=list)
    affected_channels: list[str] = field(default_factory=list)

    # Metadata
    confidence: float = 0.0
    expected_impact: str = ""
    timestamp: float = 0.0

    # Feedback tracking
    was_helpful: bool | None = None
    admin_feedback: str = ""


class ProactiveInsights:
    """
    Proactive intelligence engine that analyzes server health
    and generates helpful suggestions.

    This makes Azure feel truly "alive" - not just responding
    to commands, but actively helping improve the community.
    """

    def __init__(self, llm=None, awareness_engine=None):
        """
        Initialize proactive insights engine.

        Args:
            llm: Optional local LLM for contextual analysis
            awareness_engine: ServerAwarenessEngine for real-time data
        """
        self.llm = llm
        self.awareness = awareness_engine

        # Suggestion history (for learning)
        self.suggestion_history: list[Suggestion] = []

        # Thresholds (configurable)
        self.LOW_ENGAGEMENT_THRESHOLD = 20.0  # % of members active
        self.HIGH_ACTIVITY_CHANNEL_THRESHOLD = 100  # messages/hour
        self.INACTIVE_CHANNEL_DAYS = 7
        self.CONCERNING_LEAVE_RATE = 0.1  # 10% of members leaving

        logger.info("[proactive] ProactiveInsights engine initialized")

    async def _llm_generate_suggestion_text(self, suggestion_type: str, evidence: list[str],
                                            context: str = "") -> dict | None:
        """Use LLM to generate natural suggestion text instead of hardcoded templates."""
        if not self.llm:
            return None
        evidence_str = "\n".join(f"- {e}" for e in evidence)
        prompt = (
            f"You are a Discord server community manager AI. Generate a proactive suggestion.\n"
            f"Type: {suggestion_type}\n"
            f"Evidence:\n{evidence_str}\n"
            f"{f'Additional context: {context}' if context else ''}\n\n"
            f"Reply with ONLY a JSON object (no markdown):\n"
            f'{{"title": "short catchy title", "description": "1-2 sentence summary", '
            f'"reasoning": "why this matters", '
            f'"actions": ["action 1", "action 2", "action 3"], '
            f'"impact": "expected outcome"}}'
        )
        try:
            import json as _json
            if hasattr(self.llm, 'chat_async'):
                raw = await self.llm.chat_async(
                    [{"role": "user", "content": prompt}],
                    max_tokens=300, temperature=0.7
                )
            else:
                import asyncio
                raw = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: self.llm.chat(
                        [{"role": "user", "content": prompt}],
                        max_tokens=300, temperature=0.7
                    )
                )
            # Strip markdown fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            return _json.loads(raw)
        except Exception as e:
            logger.debug("[proactive] LLM suggestion generation failed: %s", e)
            return None

    async def _enhance_suggestion(self, suggestion: Suggestion, context: str = "") -> Suggestion:
        """Replace hardcoded text fields with LLM-generated text."""
        llm_text = await self._llm_generate_suggestion_text(
            suggestion.type.value, suggestion.evidence, context
        )
        if llm_text:
            suggestion.title = llm_text.get("title", suggestion.title)
            suggestion.description = llm_text.get("description", suggestion.description)
            suggestion.reasoning = llm_text.get("reasoning", suggestion.reasoning)
            if "actions" in llm_text and isinstance(llm_text["actions"], list):
                suggestion.recommended_actions = llm_text["actions"][:5]
            suggestion.expected_impact = llm_text.get("impact", suggestion.expected_impact)
        return suggestion

    async def generate_suggestions(self, guild_id: str,
                                   guild=None,
                                   max_suggestions: int = 10) -> list[Suggestion]:
        """
        Generate proactive suggestions for a server.

        Args:
            guild_id: Discord guild ID
            guild: Optional Discord guild object (for more context)
            max_suggestions: Maximum suggestions to return

        Returns:
            List of suggestions, sorted by priority
        """
        if not self.awareness:
            logger.warning("[proactive] No awareness engine - cannot generate insights")
            return []

        suggestions = []

        # Get server state
        insights = self.awareness.get_server_insights(guild_id)
        active_users = self.awareness.get_active_users(guild_id, window_seconds=86400)

        # === ENGAGEMENT INSIGHTS ===
        suggestions.extend(await self._analyze_engagement(guild_id, insights, active_users, guild))

        # === MODERATION INSIGHTS ===
        suggestions.extend(await self._analyze_moderation(guild_id, insights, guild))

        # === ORGANIZATION INSIGHTS ===
        suggestions.extend(await self._analyze_organization(guild_id, insights, guild))

        # === COMMUNITY INSIGHTS ===
        suggestions.extend(await self._analyze_community(guild_id, active_users, guild))

        # === CONTENT INSIGHTS ===
        suggestions.extend(await self._analyze_content(guild_id, guild))

        # Sort by priority and confidence
        priority_order = {Priority.URGENT: 4, Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}
        suggestions.sort(
            key=lambda s: (priority_order.get(s.priority, 0), s.confidence),
            reverse=True
        )

        # Enhance top suggestions with LLM-generated text
        if self.llm:
            for suggestion in suggestions[:max_suggestions]:
                with contextlib.suppress(Exception):
                    await self._enhance_suggestion(suggestion)

        # Store in history
        self.suggestion_history.extend(suggestions[:max_suggestions])
        # Cap history to prevent memory leak
        if len(self.suggestion_history) > 500:
            self.suggestion_history = self.suggestion_history[-500:]

        return suggestions[:max_suggestions]

    async def _analyze_engagement(self, guild_id: str, insights, active_users, guild) -> list[Suggestion]:
        """Analyze engagement and activity patterns."""
        suggestions = []

        # Low overall engagement
        if insights.engagement_rate < self.LOW_ENGAGEMENT_THRESHOLD:
            suggestions.append(Suggestion(
                suggestion_id=f"engage_{time.time()}",
                type=InsightType.ENGAGEMENT,
                priority=Priority.HIGH if insights.engagement_rate < 10 else Priority.MEDIUM,
                title="Low Member Engagement Detected",
                description=f"Only {insights.engagement_rate:.1f}% of members have been active in the last 24 hours.",
                reasoning=(
                    "Low engagement suggests members aren't finding value or interest in the server. "
                    "This often indicates lack of activity, unclear purpose, or insufficient content."
                ),
                recommended_actions=[
                    "Post conversation starters in active channels",
                    "Run a community event (game night, movie watch, Q&A)",
                    "Create polls to understand member interests",
                    "Review and clarify server purpose/rules",
                    "Consider welcome messages for new members"
                ],
                evidence=[
                    f"Active users (24h): {insights.active_users_day}/{insights.total_users}",
                    f"Messages (1h): {insights.messages_last_hour}"
                ],
                confidence=0.85,
                expected_impact="Increased activity, better retention, stronger community",
                timestamp=time.time()
            ))

        # Silent server (low message rate)
        if insights.messages_last_hour < 5 and insights.total_users > 20:
            suggestions.append(Suggestion(
                suggestion_id=f"silent_{time.time()}",
                type=InsightType.ENGAGEMENT,
                priority=Priority.MEDIUM,
                title="Server Is Very Quiet",
                description=f"Only {insights.messages_last_hour} messages in the last hour with {insights.total_users} members.",
                reasoning=(
                    "A quiet server may indicate members are disengaged or unsure what to talk about. "
                    "Breaking the ice with prompts can restart conversations."
                ),
                recommended_actions=[
                    "Post an open-ended question or discussion topic",
                    "Share interesting content related to server theme",
                    "Host a scheduled chat session or event",
                    "Create a 'topic of the day' channel",
                ],
                evidence=[f"Messages/hour: {insights.messages_last_hour}"],
                confidence=0.75,
                expected_impact="More conversations, increased member participation",
                timestamp=time.time()
            ))

        # Identify power users for recognition
        if len(active_users) > 0:
            top_contributors = sorted(active_users, key=lambda u: u.message_count, reverse=True)[:3]
            high_quality_users = [u for u in top_contributors
                                 if u.trust_score > 70 and u.reactions_received > 10]

            if high_quality_users:
                user = high_quality_users[0]
                suggestions.append(Suggestion(
                    suggestion_id=f"recognize_{user.user_id}",
                    type=InsightType.COMMUNITY,
                    priority=Priority.LOW,
                    title="Recognize Active Contributors",
                    description=f"{user.user_name} has been highly active and well-received by the community.",
                    reasoning=(
                        "Recognizing valuable contributors encourages continued participation "
                        "and sets a positive example for other members."
                    ),
                    recommended_actions=[
                        f"Consider giving {user.user_name} a helper/contributor role",
                        "Publicly thank them for their contributions",
                        "Ask if they'd like to help moderate or organize events"
                    ],
                    affected_users=[user.user_id],
                    evidence=[
                        f"Messages: {user.message_count}",
                        f"Trust score: {user.trust_score:.1f}",
                        f"Reactions received: {user.reactions_received}"
                    ],
                    confidence=0.80,
                    expected_impact="Stronger community bonds, motivated contributors",
                    timestamp=time.time()
                ))

        return suggestions

    async def _analyze_moderation(self, guild_id: str, insights, guild) -> list[Suggestion]:
        """Analyze moderation and safety concerns."""
        suggestions = []

        # Suspicious users detected
        if len(insights.suspicious_users) > 0:
            suggestions.append(Suggestion(
                suggestion_id=f"suspicious_{time.time()}",
                type=InsightType.MODERATION,
                priority=Priority.HIGH,
                title="Suspicious User Activity Detected",
                description=f"{len(insights.suspicious_users)} users showing suspicious patterns.",
                reasoning=(
                    "Users with low trust scores, burst messaging, or unusual patterns "
                    "may be spammers, bots, or troublemakers."
                ),
                recommended_actions=[
                    "Review recent messages from flagged users",
                    "Check account ages and join times",
                    "Monitor for coordination with other suspicious accounts",
                    "Consider timeout if behavior continues"
                ],
                affected_users=insights.suspicious_users,
                evidence=[f"Suspicious users: {len(insights.suspicious_users)}"],
                confidence=0.70,
                expected_impact="Prevent spam/raids before they escalate",
                timestamp=time.time()
            ))

        # Potential raid
        if insights.raid_probability > 0.5:
            suggestions.append(Suggestion(
                suggestion_id=f"raid_{time.time()}",
                type=InsightType.MODERATION,
                priority=Priority.URGENT,
                title="⚠️ Possible Raid Detected",
                description=f"Raid probability: {insights.raid_probability:.0%}",
                reasoning=(
                    "Rapid member joins, coordinated messaging, or similar usernames "
                    "suggest a coordinated raid attack."
                ),
                recommended_actions=[
                    "Enable server lockdown (pause invites)",
                    "Increase verification level temporarily",
                    "Ban obvious raid accounts",
                    "Alert all moderators",
                    "Monitor for mass pings or spam"
                ],
                evidence=[f"Raid probability: {insights.raid_probability:.0%}"],
                confidence=0.90,
                expected_impact="Stop raid before major damage",
                timestamp=time.time()
            ))

        # Health degradation
        if insights.health_score < 70:
            suggestions.append(Suggestion(
                suggestion_id=f"health_{time.time()}",
                type=InsightType.MODERATION,
                priority=Priority.MEDIUM,
                title="Server Health Degraded",
                description=f"Health score: {insights.health_score:.1f}/100",
                reasoning=(
                    "Health score combines engagement, moderation issues, and community sentiment. "
                    "A low score indicates problems that need attention."
                ),
                recommended_actions=[
                    "Review recent moderation actions",
                    "Check for unresolved conflicts",
                    "Assess if rules need clarification",
                    "Consider community feedback survey"
                ],
                evidence=[
                    f"Health: {insights.health_score:.1f}/100",
                    f"Engagement: {insights.engagement_rate:.1f}%"
                ],
                confidence=0.75,
                expected_impact="Improved community health and satisfaction",
                timestamp=time.time()
            ))

        return suggestions

    async def _analyze_organization(self, guild_id: str, insights, guild) -> list[Suggestion]:
        """Analyze server organization and structure."""
        suggestions = []

        if not guild:
            return suggestions

        # Overcrowded channel
        channels = self.awareness.channels.get(guild_id, {})
        for channel_id, channel_data in channels.items():
            if channel_data.messages_last_hour > self.HIGH_ACTIVITY_CHANNEL_THRESHOLD:
                suggestions.append(Suggestion(
                    suggestion_id=f"crowded_{channel_id}",
                    type=InsightType.ORGANIZATION,
                    priority=Priority.MEDIUM,
                    title=f"Channel #{channel_data.channel_name} Is Very Active",
                    description=f"{channel_data.messages_last_hour} messages/hour - consider splitting into topic-specific channels.",
                    reasoning=(
                        "Very active channels can become chaotic and hard to follow. "
                        "Splitting into focused channels improves organization and discoverability."
                    ),
                    recommended_actions=[
                        "Create topic-specific sub-channels",
                        "Use threads for specific discussions",
                        "Add slow-mode to reduce spam",
                        "Consider voice channels for real-time discussion"
                    ],
                    affected_channels=[channel_id],
                    evidence=[f"Messages/hour: {channel_data.messages_last_hour}"],
                    confidence=0.70,
                    expected_impact="Better organization, easier conversations",
                    timestamp=time.time()
                ))

        # Inactive channels
        for channel_id, channel_data in channels.items():
            days_inactive = (time.time() - channel_data.last_activity) / 86400
            if days_inactive > self.INACTIVE_CHANNEL_DAYS:
                suggestions.append(Suggestion(
                    suggestion_id=f"inactive_{channel_id}",
                    type=InsightType.ORGANIZATION,
                    priority=Priority.LOW,
                    title=f"Channel #{channel_data.channel_name} Is Inactive",
                    description=f"No activity for {days_inactive:.1f} days - archive or repurpose?",
                    reasoning=(
                        "Inactive channels clutter the server and make navigation harder. "
                        "Archiving unused channels keeps things clean."
                    ),
                    recommended_actions=[
                        "Archive the channel if no longer needed",
                        "Repurpose for a new topic if relevant",
                        "Post a prompt to restart activity",
                        "Move to an 'Archive' category"
                    ],
                    affected_channels=[channel_id],
                    evidence=[f"Days inactive: {days_inactive:.1f}"],
                    confidence=0.80,
                    expected_impact="Cleaner server, better navigation",
                    timestamp=time.time()
                ))

        return suggestions

    async def _analyze_community(self, guild_id: str, active_users, guild) -> list[Suggestion]:
        """Analyze community health and social dynamics."""
        suggestions = []

        # Check for users with warnings/timeouts (potential conflicts)
        warned_users = [u for u in active_users if u.warnings > 0 or u.timeouts > 0]
        if len(warned_users) > 3:
            suggestions.append(Suggestion(
                suggestion_id=f"conflicts_{time.time()}",
                type=InsightType.COMMUNITY,
                priority=Priority.MEDIUM,
                title="Multiple Users Have Received Warnings",
                description=f"{len(warned_users)} users have warnings/timeouts - check for recurring issues.",
                reasoning=(
                    "Multiple moderation actions may indicate unclear rules, "
                    "community conflicts, or inadequate moderation."
                ),
                recommended_actions=[
                    "Review what rules are being broken most",
                    "Clarify rules if there's confusion",
                    "Check for user conflicts or drama",
                    "Consider mediating disputes"
                ],
                affected_users=[u.user_id for u in warned_users[:5]],
                evidence=[f"Users with warnings: {len(warned_users)}"],
                confidence=0.65,
                expected_impact="Reduced conflicts, clearer expectations",
                timestamp=time.time()
            ))

        # New members not engaging
        new_users = [u for u in active_users
                    if time.time() - u.first_seen < 86400  # Last 24h
                    and u.message_count < 3]

        if len(new_users) > 5:
            suggestions.append(Suggestion(
                suggestion_id=f"new_quiet_{time.time()}",
                type=InsightType.COMMUNITY,
                priority=Priority.MEDIUM,
                title="New Members Not Engaging",
                description=f"{len(new_users)} new members joined but haven't participated much.",
                reasoning=(
                    "New members may feel intimidated or unsure how to participate. "
                    "A welcoming environment encourages first messages."
                ),
                recommended_actions=[
                    "Set up automated welcome messages",
                    "Create an #introductions channel",
                    "Personally welcome new members in chat",
                    "Clarify what topics/activities the server focuses on"
                ],
                affected_users=[u.user_id for u in new_users[:5]],
                evidence=[f"New quiet users: {len(new_users)}"],
                confidence=0.70,
                expected_impact="Better onboarding, higher retention",
                timestamp=time.time()
            ))

        return suggestions

    async def _analyze_content(self, guild_id: str, guild) -> list[Suggestion]:
        """Analyze conversation content and topics."""
        suggestions = []

        # Check for trending topics (if LLM available)
        if self.llm and self.awareness:
            # Get recent conversations across channels
            all_messages = []
            channels = self.awareness.channels.get(guild_id, {})
            for channel_id in channels:
                history = self.awareness.get_conversation_context(channel_id, limit=20)
                all_messages.extend([msg['content'] for msg in history])

            if len(all_messages) > 50:
                # Use LLM to identify trending topics
                try:
                    topics = await self._extract_trending_topics(all_messages)
                    if topics:
                        suggestions.append(Suggestion(
                            suggestion_id=f"topics_{time.time()}",
                            type=InsightType.CONTENT,
                            priority=Priority.LOW,
                            title="Trending Topics Detected",
                            description=f"Members are discussing: {', '.join(topics[:3])}",
                            reasoning=(
                                "Understanding what your community talks about helps you "
                                "create relevant channels, events, and content."
                            ),
                            recommended_actions=[
                                f"Create channels for popular topics: {', '.join(topics[:3])}",
                                "Run events related to these interests",
                                "Share related content or resources"
                            ],
                            evidence=[f"Topics: {', '.join(topics)}"],
                            confidence=0.60,
                            expected_impact="More relevant channels, higher engagement",
                            timestamp=time.time()
                        ))
                except Exception as e:
                    logger.debug(f"[proactive] Topic extraction failed: {e}")

        return suggestions

    async def _extract_trending_topics(self, messages: list[str]) -> list[str]:
        """Use LLM to extract trending topics from conversations."""
        if not self.llm or len(messages) < 20:
            return []

        try:
            # Sample messages to fit context window
            sample = messages[-100:] if len(messages) > 100 else messages
            content_sample = "\n".join(sample[:50])

            prompt = (
                "Analyze these recent server messages and identify the top 3-5 topics "
                "people are discussing. Return ONLY a comma-separated list of topics.\n\n"
                f"Messages:\n{content_sample[:1500]}\n\n"
                "Topics (comma-separated):"
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.llm.chat([
                    {"role": "system", "content": "You extract topics from conversations."},
                    {"role": "user", "content": prompt}
                ], max_tokens=100, temperature=0.3)
            )

            # Parse response
            topics = [t.strip() for t in response.split(",") if t.strip()]
            return topics[:5]

        except Exception as e:
            logger.error(f"[proactive] Topic extraction error: {e}")
            return []

    def record_feedback(self, suggestion_id: str, was_helpful: bool, feedback: str = ""):
        """Record admin feedback on a suggestion."""
        for suggestion in self.suggestion_history:
            if suggestion.suggestion_id == suggestion_id:
                suggestion.was_helpful = was_helpful
                suggestion.admin_feedback = feedback
                logger.info(f"[proactive] Feedback recorded: {suggestion_id} = {'helpful' if was_helpful else 'not helpful'}")
                break

    def get_statistics(self) -> dict:
        """Get statistics about suggestions."""
        total = len(self.suggestion_history)
        if total == 0:
            return {"total": 0}

        helpful = sum(1 for s in self.suggestion_history if s.was_helpful is True)
        not_helpful = sum(1 for s in self.suggestion_history if s.was_helpful is False)

        by_type = defaultdict(int)
        by_priority = defaultdict(int)

        for s in self.suggestion_history:
            by_type[s.type.value] += 1
            by_priority[s.priority.value] += 1

        return {
            "total": total,
            "helpful": helpful,
            "not_helpful": not_helpful,
            "no_feedback": total - helpful - not_helpful,
            "helpfulness_rate": helpful / total if total > 0 else 0.0,
            "by_type": dict(by_type),
            "by_priority": dict(by_priority),
        }
