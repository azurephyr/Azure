"""
Azure Discord Architect — Natural Language Server Architecture

Accepts high-level natural language goals and generates comprehensive server architecture plans.
Analyzes current state, compares to best practices, and executes plans step-by-step.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ArchitecturePlan:
    """A comprehensive server architecture plan."""
    goal: str
    analysis: str
    roles: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    channels: list[dict] = field(default_factory=list)
    permissions: list[dict] = field(default_factory=list)
    welcome_flow: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    estimated_steps: int = 0


class DiscordArchitect:
    """
    Natural language server architecture designer.

    Usage:
        architect = DiscordArchitect()
        plan = architect.generate_plan("Make this a welcoming community for indie game devs")
        await architect.execute_plan(guild, plan, ctx)
    """

    BEST_PRACTICES = {
        "gaming": {
            "roles": ["Admin", "Moderator", "Game Dev", "Artist", "Tester", "Streamer"],
            "categories": ["📢 Announcements", "🎮 General", "💻 Development", "🎨 Art & Assets", "🔧 Testing", "📚 Resources"],
            "channels": [
                {"name": "welcome", "type": "text", "category": "📢 Announcements", "topic": "New member landing"},
                {"name": "rules", "type": "text", "category": "📢 Announcements", "topic": "Server rules"},
                {"name": "announcements", "type": "text", "category": "📢 Announcements"},
                {"name": "general", "type": "text", "category": "🎮 General"},
                {"name": "introductions", "type": "text", "category": "🎮 General"},
                {"name": "showcase", "type": "text", "category": "🎮 General"},
                {"name": "dev-help", "type": "text", "category": "💻 Development"},
                {"name": "code-share", "type": "text", "category": "💻 Development"},
                {"name": "assets", "type": "text", "category": "🎨 Art & Assets"},
                {"name": "feedback", "type": "text", "category": "🔧 Testing"},
                {"name": "bugs", "type": "text", "category": "🔧 Testing"},
                {"name": "tutorials", "type": "text", "category": "📚 Resources"},
                {"name": "tools", "type": "text", "category": "📚 Resources"},
            ],
        },
        "tech": {
            "roles": ["Admin", "Moderator", "Senior Dev", "Junior Dev", "Designer", "DevOps"],
            "categories": ["📢 Info", "💬 Chat", "🔧 Engineering", "🎨 Design", "📚 Knowledge"],
            "channels": [
                {"name": "welcome", "type": "text", "category": "📢 Info"},
                {"name": "rules", "type": "text", "category": "📢 Info"},
                {"name": "announcements", "type": "text", "category": "📢 Info"},
                {"name": "general", "type": "text", "category": "💬 Chat"},
                {"name": "random", "type": "text", "category": "💬 Chat"},
                {"name": "frontend", "type": "text", "category": "🔧 Engineering"},
                {"name": "backend", "type": "text", "category": "🔧 Engineering"},
                {"name": "devops", "type": "text", "category": "🔧 Engineering"},
                {"name": "ui-ux", "type": "text", "category": "🎨 Design"},
                {"name": "resources", "type": "text", "category": "📚 Knowledge"},
                {"name": "career", "type": "text", "category": "📚 Knowledge"},
            ],
        },
        "community": {
            "roles": ["Admin", "Moderator", "Veteran", "Member", "Newcomer"],
            "categories": ["📢 Info", "💬 General", "🎯 Activities", "📚 Resources"],
            "channels": [
                {"name": "welcome", "type": "text", "category": "📢 Info"},
                {"name": "rules", "type": "text", "category": "📢 Info"},
                {"name": "announcements", "type": "text", "category": "📢 Info"},
                {"name": "general", "type": "text", "category": "💬 General"},
                {"name": "introductions", "type": "text", "category": "💬 General"},
                {"name": "off-topic", "type": "text", "category": "💬 General"},
                {"name": "events", "type": "text", "category": "🎯 Activities"},
                {"name": "memes", "type": "text", "category": "🎯 Activities"},
                {"name": "help", "type": "text", "category": "📚 Resources"},
                {"name": "suggestions", "type": "text", "category": "📚 Resources"},
            ],
        },
    }

    def __init__(self, llm=None):
        self.llm = llm

    def generate_plan(self, goal: str, current_state: dict | None = None) -> ArchitecturePlan:
        """Generate an architecture plan from a natural language goal."""
        goal_lower = goal.lower()

        # Detect template type
        template_type = "community"
        if any(w in goal_lower for w in ["game", "gaming", "gamer", "dev", "unity", "unreal"]):
            template_type = "gaming"
        elif any(w in goal_lower for w in ["tech", "code", "software", "engineering", "developer", "programming"]):
            template_type = "tech"

        template = self.BEST_PRACTICES.get(template_type, self.BEST_PRACTICES["community"])
        current_state = current_state or {}

        # Analyze current state vs ideal
        existing_roles = {r["name"] for r in current_state.get("roles", [])}
        existing_channels = {c["name"] for c in current_state.get("channels", [])}

        plan = ArchitecturePlan(goal=goal)
        plan.analysis = f"Detected community type: {template_type}. Current: {len(existing_roles)} roles, {len(existing_channels)} channels."

        # Add missing roles
        for role_name in template["roles"]:
            if role_name not in existing_roles:
                plan.roles.append({"name": role_name, "color": self._role_color(role_name)})

        # Add missing categories
        existing_cats = {c["name"] for c in current_state.get("categories", [])}
        for cat_name in template["categories"]:
            if cat_name not in existing_cats:
                plan.categories.append({"name": cat_name})

        # Add missing channels
        for ch in template["channels"]:
            if ch["name"] not in existing_channels:
                plan.channels.append(ch)

        # Welcome flow
        plan.welcome_flow = [
            "1. New member joins → Auto-DM with server rules and intro prompt",
            "2. Member reads #rules and accepts via reaction",
            "3. Member introduces themselves in #introductions",
            "4. Auto-assign 'Member' role after intro",
        ]

        # Rules
        plan.rules = [
            "Be respectful and inclusive to all members",
            "Keep discussions in appropriate channels",
            "No spam, self-promotion, or unsolicited DMs",
            "Use spoiler tags for sensitive content",
            "Follow Discord Terms of Service",
        ]

        plan.estimated_steps = len(plan.roles) + len(plan.categories) + len(plan.channels) + 2

        return plan

    async def execute_plan(self, guild, plan: ArchitecturePlan, ctx, tools) -> list[str]:
        """Execute an architecture plan step by step."""
        results = []

        # Create roles
        for role in plan.roles:
            try:
                await guild.create_role(name=role["name"], color=role["color"], reason="Azure architect")
                results.append(f"✅ Created role: {role['name']}")
            except Exception as e:
                results.append(f"❌ Role {role['name']}: {e}")

        # Create categories
        for cat in plan.categories:
            try:
                await guild.create_category(name=cat["name"], reason="Azure architect")
                results.append(f"✅ Created category: {cat['name']}")
            except Exception as e:
                results.append(f"❌ Category {cat['name']}: {e}")

        # Create channels
        for ch in plan.channels:
            try:
                cat_obj = None
                if ch.get("category"):
                    import discord
                    cat_obj = discord.utils.get(guild.categories, name=ch["category"])

                if ch.get("type") == "voice":
                    await guild.create_voice_channel(ch["name"], category=cat_obj, reason="Azure architect")
                else:
                    await guild.create_text_channel(
                        ch["name"], category=cat_obj, topic=ch.get("topic", ""),
                        reason="Azure architect"
                    )
                results.append(f"✅ Created channel: #{ch['name']}")
            except Exception as e:
                results.append(f"❌ Channel {ch['name']}: {e}")

        # Send welcome/rules messages
        try:
            welcome_ch = await self._find_or_create_channel(guild, "welcome")
            if welcome_ch:
                import discord
                # send() requires a discord.Embed, not a plain dict.
                embed = discord.Embed.from_dict(self._build_welcome_embed(plan))
                await welcome_ch.send(embed=embed)
                results.append("✅ Posted welcome message")

            rules_ch = await self._find_or_create_channel(guild, "rules")
            if rules_ch:
                rules_text = "\n".join(f"{i+1}. {rule}" for i, rule in enumerate(plan.rules))
                await rules_ch.send(f"📜 **Server Rules**\n\n{rules_text}")
                results.append("✅ Posted rules")
        except Exception as e:
            results.append(f"❌ Welcome/rules posting: {e}")

        return results

    def _role_color(self, role_name: str) -> int:
        """Assign a color based on role name."""
        colors = {
            "Admin": 0xE74C3C, "Moderator": 0x3498DB, "Senior Dev": 0x9B59B6,
            "Game Dev": 0x2ECC71, "Artist": 0xE67E22, "Designer": 0xF1C40F,
            "Tester": 0x1ABC9C, "Streamer": 0xFF69B4, "DevOps": 0x95A5A6,
            "Veteran": 0xC0C0C0, "Member": 0x2ECC71, "Newcomer": 0x3498DB,
        }
        return colors.get(role_name, 0x95A5A6)

    async def _find_or_create_channel(self, guild, name: str):
        """Find a channel by name, or return None."""
        import discord
        return discord.utils.get(guild.text_channels, name=name)

    def _build_welcome_embed(self, plan: ArchitecturePlan) -> dict:
        """Build a welcome embed."""
        return {
            "title": f"🎉 Welcome to {plan.goal}!",
            "description": "We're glad you're here! Check out the channels below to get started.",
            "color": 0x2ECC71,
            "fields": [
                {"name": "📢 Start Here", "value": "Read #rules and #welcome", "inline": True},
                {"name": "💬 Chat", "value": "Say hi in #general or #introductions", "inline": True},
                {"name": "🎯 Get Involved", "value": "Find your niche in our topic channels", "inline": True},
            ]
        }
