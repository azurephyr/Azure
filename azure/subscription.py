"""
Azure Discord Bot - Subscription System Foundation

Provides tier-based access control and feature management for users.
Supports free, premium, and enterprise tiers with customizable limits.

Features:
- User tier management (free, premium, enterprise)
- Rate limiting per tier
- Feature flags per tier
- Usage tracking and analytics
- Upgrade/downgrade handling
- Expiration and renewal management
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from .constants import (
    FREE_TIER_CONTEXT_SIZE,
    FREE_TIER_MAX_MESSAGES_PER_HOUR,
    PREMIUM_TIER_CONTEXT_SIZE,
    PREMIUM_TIER_MAX_MESSAGES_PER_HOUR,
)
from .database import DatabaseManager, UserPreference

logger = logging.getLogger("azure.subscription")


# =============================================================================
# Enums and Data Classes
# =============================================================================

class SubscriptionTier(StrEnum):
    """Available subscription tiers."""
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    """Subscription status values."""
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    TRIAL = "trial"
    SUSPENDED = "suspended"


@dataclass
class TierLimits:
    """Limits and features for a subscription tier."""
    tier: SubscriptionTier
    max_messages_per_hour: int
    max_messages_per_day: int
    context_size: int
    priority_support: bool
    streaming_responses: bool
    custom_system_prompt: bool
    web_dashboard_access: bool
    api_access: bool
    max_concurrent_requests: int = 1
    response_time_sla_seconds: float = 0.0

    @staticmethod
    def get_limits(tier: SubscriptionTier) -> TierLimits:
        """Get limits for a specific tier.

        Args:
            tier: Subscription tier

        Returns:
            TierLimits configuration for the tier
        """
        if tier == SubscriptionTier.FREE:
            return TierLimits(
                tier=SubscriptionTier.FREE,
                max_messages_per_hour=FREE_TIER_MAX_MESSAGES_PER_HOUR,
                max_messages_per_day=50,
                context_size=FREE_TIER_CONTEXT_SIZE,
                priority_support=False,
                streaming_responses=False,
                custom_system_prompt=False,
                web_dashboard_access=False,
                api_access=False,
                max_concurrent_requests=1
            )
        elif tier == SubscriptionTier.PREMIUM:
            return TierLimits(
                tier=SubscriptionTier.PREMIUM,
                max_messages_per_hour=PREMIUM_TIER_MAX_MESSAGES_PER_HOUR,
                max_messages_per_day=-1,  # Unlimited
                context_size=PREMIUM_TIER_CONTEXT_SIZE,
                priority_support=True,
                streaming_responses=True,
                custom_system_prompt=True,
                web_dashboard_access=True,
                api_access=False,
                max_concurrent_requests=3,
                response_time_sla_seconds=2.0
            )
        elif tier == SubscriptionTier.ENTERPRISE:
            return TierLimits(
                tier=SubscriptionTier.ENTERPRISE,
                max_messages_per_hour=-1,  # Unlimited
                max_messages_per_day=-1,  # Unlimited
                context_size=50,
                priority_support=True,
                streaming_responses=True,
                custom_system_prompt=True,
                web_dashboard_access=True,
                api_access=True,
                max_concurrent_requests=10,
                response_time_sla_seconds=1.0
            )
        else:
            return TierLimits.get_limits(SubscriptionTier.FREE)


@dataclass
class Subscription:
    """User subscription information."""
    user_id: str
    user_name: str
    tier: SubscriptionTier = SubscriptionTier.FREE
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    start_date: float = field(default_factory=time.time)
    end_date: float | None = None
    trial_end_date: float | None = None
    auto_renew: bool = True
    payment_method: str | None = None

    @property
    def is_active(self) -> bool:
        """Check if subscription is currently active."""
        if self.status != SubscriptionStatus.ACTIVE:
            return False

        return not (self.end_date and time.time() > self.end_date)

    @property
    def is_trial(self) -> bool:
        """Check if subscription is in trial period."""
        if not self.trial_end_date:
            return False

        return time.time() < self.trial_end_date

    @property
    def days_remaining(self) -> int | None:
        """Get days remaining in subscription."""
        if not self.end_date:
            return None  # No expiration

        seconds_remaining = self.end_date - time.time()
        return max(0, int(seconds_remaining / 86400))


@dataclass
class UsageStats:
    """Usage statistics for a user."""
    user_id: str
    messages_hour: int = 0
    messages_day: int = 0
    messages_month: int = 0
    hour_reset_time: float = field(default_factory=time.time)
    day_reset_time: float = field(default_factory=time.time)
    month_reset_time: float = field(default_factory=time.time)

    def increment_message(self) -> None:
        """Increment message counters and reset if needed."""
        now = time.time()

        # Reset hourly counter
        if now - self.hour_reset_time >= 3600:
            self.messages_hour = 0
            self.hour_reset_time = now

        # Reset daily counter
        if now - self.day_reset_time >= 86400:
            self.messages_day = 0
            self.day_reset_time = now

        # Reset monthly counter (approximate)
        if now - self.month_reset_time >= 2592000:  # 30 days
            self.messages_month = 0
            self.month_reset_time = now

        self.messages_hour += 1
        self.messages_day += 1
        self.messages_month += 1


# =============================================================================
# Subscription Manager
# =============================================================================

class SubscriptionManager:
    """Manages user subscriptions, limits, and features."""

    def __init__(self, db: DatabaseManager) -> None:
        """Initialize subscription manager.

        Args:
            db: Database manager instance
        """
        self.db = db
        self._subscriptions: dict[str, Subscription] = {}
        self._usage: dict[str, UsageStats] = {}
        self._load_subscriptions()
        logger.info("[subscription] Manager initialized")

    def _load_subscriptions(self) -> None:
        """Load subscriptions from database."""
        # In a real implementation, load from database
        # For now, create default free tier for all users
        logger.info("[subscription] Loaded subscriptions from database")

    def get_subscription(self, user_id: str, user_name: str = "") -> Subscription:
        """Get or create subscription for user.

        Args:
            user_id: User ID
            user_name: User name (optional)

        Returns:
            User's subscription
        """
        if user_id not in self._subscriptions:
            # Check database first
            pref = self.db.get_user_preference(user_id)
            tier = SubscriptionTier(pref.tier) if pref else SubscriptionTier.FREE

            self._subscriptions[user_id] = Subscription(
                user_id=user_id,
                user_name=user_name,
                tier=tier
            )

        return self._subscriptions[user_id]

    def get_limits(self, user_id: str) -> TierLimits:
        """Get tier limits for user.

        Args:
            user_id: User ID

        Returns:
            Tier limits configuration
        """
        sub = self.get_subscription(user_id)
        return TierLimits.get_limits(sub.tier)

    def check_rate_limit(self, user_id: str, user_name: str = "") -> tuple[bool, str]:
        """Check if user has exceeded rate limits.

        Args:
            user_id: User ID
            user_name: User name

        Returns:
            Tuple of (allowed, reason)
        """
        sub = self.get_subscription(user_id, user_name)
        limits = TierLimits.get_limits(sub.tier)

        # Get or create usage stats
        if user_id not in self._usage:
            self._usage[user_id] = UsageStats(user_id=user_id)

        usage = self._usage[user_id]
        usage.increment_message()

        # Check hourly limit
        if limits.max_messages_per_hour != -1 and usage.messages_hour > limits.max_messages_per_hour:
                return False, f"Hourly limit reached ({limits.max_messages_per_hour} messages/hour). Upgrade to Premium for unlimited access!"

        # Check daily limit
        if limits.max_messages_per_day != -1 and usage.messages_day > limits.max_messages_per_day:
                return False, f"Daily limit reached ({limits.max_messages_per_day} messages/day). Upgrade to Premium for unlimited access!"

        return True, "OK"

    def has_feature(self, user_id: str, feature: str) -> bool:
        """Check if user has access to a feature.

        Args:
            user_id: User ID
            feature: Feature name (e.g., 'streaming_responses')

        Returns:
            True if user has access to feature
        """
        limits = self.get_limits(user_id)
        return getattr(limits, feature, False)

    def upgrade_tier(
        self,
        user_id: str,
        new_tier: SubscriptionTier,
        duration_days: int | None = None,
        trial: bool = False
    ) -> None:
        """Upgrade user to new tier.

        Args:
            user_id: User ID
            new_tier: New subscription tier
            duration_days: Subscription duration (None = permanent)
            trial: Whether this is a trial subscription
        """
        sub = self.get_subscription(user_id)
        old_tier = sub.tier

        sub.tier = new_tier
        sub.status = SubscriptionStatus.TRIAL if trial else SubscriptionStatus.ACTIVE
        sub.start_date = time.time()

        if duration_days:
            sub.end_date = time.time() + (duration_days * 86400)
        else:
            sub.end_date = None

        if trial:
            sub.trial_end_date = time.time() + (duration_days or 7) * 86400

        # Save to database
        pref = self.db.get_user_preference(user_id) or UserPreference(
            user_id=user_id,
            user_name=sub.user_name,
            created_at=time.time(),
            updated_at=time.time()
        )
        pref.tier = new_tier.value
        pref.updated_at = time.time()
        self.db.save_user_preference(pref)

        logger.info(f"[subscription] Upgraded {user_id} from {old_tier} to {new_tier}")

    def downgrade_tier(self, user_id: str, new_tier: SubscriptionTier) -> None:
        """Downgrade user to lower tier.

        Args:
            user_id: User ID
            new_tier: New subscription tier
        """
        self.upgrade_tier(user_id, new_tier)
        logger.info(f"[subscription] Downgraded {user_id} to {new_tier}")

    def cancel_subscription(self, user_id: str) -> None:
        """Cancel user's subscription.

        Args:
            user_id: User ID
        """
        sub = self.get_subscription(user_id)
        sub.status = SubscriptionStatus.CANCELLED
        sub.auto_renew = False

        logger.info(f"[subscription] Cancelled subscription for {user_id}")

    def get_usage_stats(self, user_id: str) -> UsageStats | None:
        """Get usage statistics for user.

        Args:
            user_id: User ID

        Returns:
            Usage statistics or None
        """
        return self._usage.get(user_id)

    def get_all_subscribers(self, tier: SubscriptionTier | None = None) -> list[Subscription]:
        """Get all subscribers, optionally filtered by tier.

        Args:
            tier: Filter by subscription tier (optional)

        Returns:
            List of subscriptions
        """
        subs = list(self._subscriptions.values())

        if tier:
            subs = [s for s in subs if s.tier == tier]

        return subs

    def check_and_expire_subscriptions(self) -> int:
        """Check and expire ended subscriptions.

        Returns:
            Number of subscriptions expired
        """
        expired_count = 0
        now = time.time()

        for sub in self._subscriptions.values():
            if sub.end_date and now > sub.end_date and sub.status == SubscriptionStatus.ACTIVE:
                sub.status = SubscriptionStatus.EXPIRED
                expired_count += 1
                logger.info(f"[subscription] Expired subscription for {sub.user_id}")

                # Auto-downgrade to free
                if not sub.auto_renew:
                    self.downgrade_tier(sub.user_id, SubscriptionTier.FREE)

        return expired_count


# =============================================================================
# Pricing and Plans (Reference Implementation)
# =============================================================================

@dataclass
class PricingPlan:
    """Pricing plan configuration."""
    tier: SubscriptionTier
    name: str
    price_monthly: float
    price_yearly: float
    description: str
    features: list[str]

    @staticmethod
    def get_all_plans() -> list[PricingPlan]:
        """Get all available pricing plans."""
        return [
            PricingPlan(
                tier=SubscriptionTier.FREE,
                name="Free",
                price_monthly=0.0,
                price_yearly=0.0,
                description="Perfect for casual users",
                features=[
                    "5 messages per hour",
                    "Basic context memory",
                    "Standard response time",
                    "Community support"
                ]
            ),
            PricingPlan(
                tier=SubscriptionTier.PREMIUM,
                name="Premium",
                price_monthly=9.99,
                price_yearly=99.99,
                description="For power users and enthusiasts",
                features=[
                    "Unlimited messages",
                    "Extended context memory (20 messages)",
                    "Priority response time (2s SLA)",
                    "Streaming responses",
                    "Custom system prompts",
                    "Web dashboard access",
                    "Priority support"
                ]
            ),
            PricingPlan(
                tier=SubscriptionTier.ENTERPRISE,
                name="Enterprise",
                price_monthly=49.99,
                price_yearly=499.99,
                description="For teams and businesses",
                features=[
                    "Everything in Premium",
                    "API access",
                    "99.9% uptime SLA",
                    "Dedicated support",
                    "Custom integrations",
                    "Advanced analytics",
                    "10 concurrent requests",
                    "Custom deployment options"
                ]
            )
        ]


# =============================================================================
# Usage Example
# =============================================================================

def example_usage():
    """Example of how to use the subscription system."""
    from .database import DatabaseManager

    # Initialize
    db = DatabaseManager("data/azure_bot.db")
    sub_manager = SubscriptionManager(db)

    # Check user tier
    sub = sub_manager.get_subscription("user123", "TestUser")
    print(f"User tier: {sub.tier}")

    # Check rate limit
    allowed, reason = sub_manager.check_rate_limit("user123")
    if not allowed:
        print(f"Rate limit: {reason}")

    # Check feature access
    has_streaming = sub_manager.has_feature("user123", "streaming_responses")
    print(f"Has streaming: {has_streaming}")

    # Upgrade user
    sub_manager.upgrade_tier("user123", SubscriptionTier.PREMIUM, duration_days=30)

    # Get pricing plans
    plans = PricingPlan.get_all_plans()
    for plan in plans:
        print(f"{plan.name}: ${plan.price_monthly}/month")


if __name__ == "__main__":
    example_usage()
