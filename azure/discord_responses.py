"""
Azure Discord Visual Response System

Makes the bot look smart and alive:
- Multi-phase animated thinking indicators
- Rich embed messages with color-coding
- Callout boxes for warnings/notes/errors
- Formatted tool call displays
- Structured moderation reports
- Cooldown animations

Usage:
    from azure.discord_responses import (
        ThinkingAnimation,
        EmbedBuilder,
        format_reply,
        thinking_embed,
        tool_call_embed,
        moderation_report_embed,
        callout_block,
    )
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import re
import textwrap
from dataclasses import dataclass, field

logger = logging.getLogger("azure.discord_responses")


# ---------------------------------------------------------------------------
# Color palette (Discord embed int values)
# ---------------------------------------------------------------------------
# Discord embeds use decimal color values, not hex.
# Compute via: int.from_bytes((r, g, b), 'big')

def rgb(r: int, g: int, b: int) -> int:
    return (r << 16) | (g << 8) | b


BLUE    = rgb(88, 101, 242)   # Blurple — info, normal chat
GREEN   = rgb(35, 232, 142)   # Emerald — success, confirmations
RED     = rgb(237, 66,  69)   # Red — errors, failures
ORANGE  = rgb(255, 159,  26)  # Orange — warnings, caution
PURPLE  = rgb(160, 100, 220)  # Purple — moderation, intelligence
TEAL    = rgb(57, 202, 185)   # Teal — tools, actions
YELLOW  = rgb(255, 214,  10)  # Yellow — planning, analysis
DARK    = rgb(40,  43,  47)   # Dark grey — neutral, neutral embeds
GRAY    = rgb(79,  84,  92)   # Gray — timestamps, footers


# ---------------------------------------------------------------------------
@dataclass
class ThinkingPhase:
    emoji: str
    label: str
    sub: str = ""


THINKING_PHASES = [
    ThinkingPhase("🧠", "Analyzing", "understanding intent..."),
    ThinkingPhase("🔍", "Searching", "checking memory..."),
    ThinkingPhase("💭", "Reasoning", "forming response..."),
    ThinkingPhase("⚙️", "Processing", "loading context..."),
    ThinkingPhase("✍️", "Drafting", "writing reply..."),
]


class ThinkingAnimation:
    """
    Shows a Discord message that cycles through thinking phases.

    Usage:
        anim = await ThinkingAnimation.start(channel)
        await anim.advance_to("Searching")       # jump to a phase
        # ... do work ...
        await anim.finish("Here's your answer!")  # replaces the message
        # OR
        await anim.cancel()                         # deletes the message

    The message content updates in-place, giving a live "working on it" feel.
    """

    DOTS = ["   ", " ● ", " ◐ ", " ◑ ", " ● "]

    def __init__(self, message, loop: asyncio.AbstractEventLoop):
        self.message = message
        self.loop = loop
        self._task: asyncio.Task | None = None
        self._phase_idx = 0
        self._dots_idx = 0
        self._done = False
        self._cancelled = False

    @classmethod
    async def start(cls, channel, phases=None) -> ThinkingAnimation:
        """Start the animation. Sends the initial thinking message."""
        if phases is None:
            phases = THINKING_PHASES

        import time
        loop = asyncio.get_running_loop()
        anim = cls(None, loop)
        anim._phases = phases
        anim._start_time = time.perf_counter()

        embed = anim._make_embed(phases[0], 0)
        msg = await channel.send(embed=embed)
        anim.message = msg
        anim._task = loop.create_task(anim._run())
        return anim

    def _make_embed(self, current_phase: ThinkingPhase, dots_idx: int):
        import time

        import discord
        dots = self.DOTS[dots_idx % len(self.DOTS)]

        embed = discord.Embed(
            title=f"{current_phase.emoji} {current_phase.label}{dots}",
            color=0x5865F2, # Blurple
            description=f"> {current_phase.sub}" if current_phase.sub else ""
        )

        # Build checklist of phases
        checklist = ""
        for i, phase in enumerate(self._phases):
            if i < self._phase_idx:
                checklist += f"✅ ~~{phase.label}~~\n"
            elif i == self._phase_idx:
                checklist += f"🔄 **{phase.label}**\n"
            else:
                checklist += f"⏳ {phase.label}\n"

        embed.add_field(name="Cognitive Pipeline", value=checklist, inline=False)

        if hasattr(self, '_start_time'):
            elapsed = time.perf_counter() - self._start_time
            embed.set_footer(text=f"Azure Cognitive Engine • {elapsed:.1f}s elapsed")

        return embed

    async def _run(self):
        """Background loop: cycles dots every 0.6s, advances phase every 2.5s."""
        phases = self._phases
        try:
            while not self._done and not self._cancelled:
                for i in range(len(phases)):
                    if self._done or self._cancelled:
                        return
                    self._phase_idx = i
                    for d in range(len(self.DOTS)):
                        if self._done or self._cancelled:
                            return
                        self._dots_idx = d
                        try:
                            embed = self._make_embed(phases[i], d)
                            await self.message.edit(content="", embed=embed)
                        except Exception:
                            return
                        await asyncio.sleep(0.6)
        except asyncio.CancelledError:
            logger.debug("Thinking animation cancelled")

    async def advance_to(self, label: str, sub: str = ""):
        """Jump immediately to a named phase, resetting the dots."""
        for i, p in enumerate(self._phases):
            if label.lower() in p.label.lower():
                self._phase_idx = i
                self._dots_idx = 0
                phase = self._phases[i]
                if sub:
                    phase = ThinkingPhase(phase.emoji, phase.label, sub)
                try:
                    embed = self._make_embed(phase, 0)
                    await self.message.edit(content="", embed=embed)
                except Exception as e_adv:
                    logger.warning("Failed to advance animation: %s", e_adv)
                return

    async def update_sub(self, sub: str):
        """Update the sub-text of the current phase."""
        try:
            phase = self._phases[self._phase_idx]
            phase = ThinkingPhase(phase.emoji, phase.label, sub)
            embed = self._make_embed(phase, self._dots_idx)
            await self.message.edit(content="", embed=embed)
        except Exception as e_sub:
            logger.warning("Failed to update animation subtext: %s", e_sub)

    async def finish(self, final_content: str = None, embed=None, view=None):
        """Stop the animation and replace with final content."""
        self._done = True
        if self._task:
            self._task.cancel()
            # Await the cancellation so an in-flight message.edit() in the
            # animation loop can't land AFTER we write the final content and
            # overwrite it with a stale 'thinking' embed.
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._task = None

        kwargs = {}
        kwargs['embed'] = embed  # Will pass None to explicitly clear the thinking checklist
        if view is not None:
            kwargs['view'] = view

        if final_content:
            try:
                if len(final_content) > 2000:
                    # Send as multi-message fallback
                    await self.message.delete()
                    for i in range(0, len(final_content), 1900):
                        if i + 1900 >= len(final_content):
                            await self.message.channel.send(final_content[i:i + 1900], **kwargs)
                        else:
                            await self.message.channel.send(final_content[i:i + 1900])
                else:
                    await self.message.edit(content=final_content, **kwargs)
            except Exception:
                # Message was deleted or edit failed — send fresh, chunking to
                # respect Discord's 2000-char limit (a single send of long
                # content is always rejected).
                for i in range(0, len(final_content), 1900):
                    chunk = final_content[i:i + 1900]
                    if i + 1900 >= len(final_content):
                        await self.message.channel.send(chunk, **kwargs)
                    else:
                        await self.message.channel.send(chunk)
        elif kwargs:
            try:
                await self.message.edit(content="", **kwargs)
            except Exception:
                await self.message.channel.send(**kwargs)

    async def cancel(self):
        """Delete the thinking message."""
        self._cancelled = True
        self._done = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.message.delete()
        except Exception as e_del:
            logger.warning("Failed to delete thinking message: %s", e_del)


# ---------------------------------------------------------------------------
# Embed builder — fluent API
# ---------------------------------------------------------------------------
@dataclass
class EmbedBuilder:
    """
    Fluent builder for Discord embeds.

    Usage:
        embed = (
            EmbedBuilder()
            .title("Server Health Report")
            .color(BLUE)
            .field("Members", "142", inline=True)
            .field("Online", "23", inline=True)
            .field("Channels", "18", inline=False)
            .footer("Azure \u00b7 scanned just now")
            .timestamp()
            .build()
        )
    """
    _title: str = ""
    _description: str = ""
    _color: int = DARK
    _fields: list = field(default_factory=list)
    _footer_text: str = ""
    _footer_icon: str = ""
    _author_name: str = ""
    _author_icon: str = ""
    _url: str = ""
    _thumbnail: str = ""
    _image: str = ""
    _timestamp: bool = False

    def title(self, text: str, url: str = "") -> EmbedBuilder:
        self._title = text
        self._url = url
        return self

    def description(self, text: str) -> EmbedBuilder:
        self._description = text
        return self

    def color(self, color: int) -> EmbedBuilder:
        self._color = color
        return self

    def field(self, name: str, value: str, inline: bool = True) -> EmbedBuilder:
        self._fields.append({"name": name, "value": value, "inline": inline})
        return self

    def footer(self, text: str, icon_url: str = "") -> EmbedBuilder:
        self._footer_text = text
        self._footer_icon = icon_url
        return self

    def author(self, name: str, icon_url: str = "") -> EmbedBuilder:
        self._author_name = name
        self._author_icon = icon_url
        return self

    def thumbnail(self, url: str) -> EmbedBuilder:
        self._thumbnail = url
        return self

    def image(self, url: str) -> EmbedBuilder:
        self._image = url
        return self

    def timestamp(self) -> EmbedBuilder:
        self._timestamp = True
        return self

    def build(self):
        import discord
        embed = discord.Embed(
            title=self._title,
            description=self._description,
            color=self._color,
            url=self._url,
        )
        for f in self._fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
        if self._footer_text:
            embed.set_footer(text=self._footer_text, icon_url=self._footer_icon)
        if self._author_name:
            embed.set_author(name=self._author_name, icon_url=self._author_icon)
        if self._thumbnail:
            embed.set_thumbnail(url=self._thumbnail)
        if self._image:
            embed.set_image(url=self._image)
        if self._timestamp:
            embed.timestamp = datetime.datetime.now(datetime.UTC)
        return embed


# ---------------------------------------------------------------------------
# Pre-built embed helpers
# ---------------------------------------------------------------------------

def thinking_embed(phase: str, sub: str = "") -> EmbedBuilder:
    """Brief thinking indicator embed (not animated — for quick status)."""
    emoji_map = {
        "analyzing": ("🧠", BLUE),
        "searching": ("🔍", TEAL),
        "reasoning": ("💭", PURPLE),
        "drafting":  ("✍️", YELLOW),
        "done":      ("✅", GREEN),
        "error":     ("❌", RED),
    }
    emoji, color = emoji_map.get(phase.lower(), ("🧠", BLUE))
    return (
        EmbedBuilder()
        .description(f"{emoji} **{phase.title()}**" + (f"\n> {sub}" if sub else ""))
        .color(color)
    )


def tool_call_embed(tool_name: str, params: dict, status: str = "running") -> EmbedBuilder:
    """Show a tool being invoked."""
    status_emoji = {"running": "⚙️", "success": "✅", "error": "❌", "skipped": "⏭️"}
    emoji = status_emoji.get(status.lower(), "⚙️")
    params_str = "\n".join(f"`{k}` → `{v}`" for k, v in params.items()) if params else "_No parameters_"
    return (
        EmbedBuilder()
        .title(f"{emoji} Tool Call: {tool_name}")
        .description(f"**Parameters:**\n{params_str}")
        .color(TEAL)
        .footer(f"Status: {status.title()}")
    )


def moderation_report_embed(
    action: str,
    reason: str,
    confidence: float,
    risk_level: str,
    user_name: str = "Unknown User",
) -> EmbedBuilder:
    """Structured moderation action report."""
    risk_colors = {"low": GREEN, "medium": ORANGE, "high": RED}
    color = risk_colors.get(risk_level.lower(), ORANGE)

    risk_bar = "🟢" if risk_level == "low" else "🟡" if risk_level == "medium" else "🔴"
    conf_pct = f"{confidence * 100:.0f}%"

    embed = (
        EmbedBuilder()
        .title(f"🛡️ Moderation Action: {action}")
        .description(f"**User:** {user_name}\n**Reason:** {reason}")
        .color(color)
        .field("Risk Level", f"{risk_bar} {risk_level.title()}", inline=True)
        .field("Confidence", f"`{conf_pct}`", inline=True)
        .field("Action Taken", action, inline=False)
        .footer("Azure Moderation Engine · Phase: Reactive")
        .timestamp()
    )
    return embed


def callout_block(text: str, kind: str = "note") -> str:
    """
    Render text as a Discord callout block.

    kind: 'note' | 'warning' | 'error' | 'tip' | 'success'
    """
    icons = {
        "note":     ("📋", "NOTE", BLUE),
        "warning":  ("⚠️",  "WARNING", ORANGE),
        "error":    ("🚫", "ERROR", RED),
        "tip":     ("💡", "TIP", GREEN),
        "success": ("✅", "SUCCESS", GREEN),
        "info":    ("ℹ️", "INFO", BLUE),
    }
    icon, label, _ = icons.get(kind.lower(), icons["note"])
    # Wrap text at 60 chars to avoid Discord overflow
    wrapped = textwrap.fill(text, width=60)
    return (
        f"> **{icon} {label}:**\n" +
        "\n".join(f"> {line}" for line in wrapped.split("\n"))
    )


def format_reply(text: str) -> str:
    """
    Apply light formatting to raw LLM output to make it Discord-ready:
    - Adds spacing before bullet lists
    - Trims excessive newlines
    - Wraps very long lines
    - Adds a subtle bot signature on long messages
    """
    # Normalize excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Wrap long lines (Discord hard limit 2000, but readability limit 120)
    lines = text.split('\n')
    formatted_lines = []
    for line in lines:
        if len(line) > 120 and not line.startswith('```'):
            # Soft-wrap long lines
            wrapped = textwrap.fill(line, width=120)
            formatted_lines.append(wrapped)
        else:
            formatted_lines.append(line)

    return '\n'.join(formatted_lines)


def short_reply(text: str, user_name: str = "") -> str:
    """
    Format a casual short reply.
    Does NOT prepend the username — the bot speaks directly, never as a scriptwriter.
    """
    return format_reply(text)


def info_embed(title: str, description: str = "", fields: list | None = None) -> EmbedBuilder:
    """Quick info embed."""
    b = (
        EmbedBuilder()
        .title(f"ℹ️ {title}")
        .color(BLUE)
        .description(description)
    )
    for f in (fields or []):
        b.field(f["name"], f["value"], inline=f.get("inline", True))
    return b


def success_embed(title: str, description: str = "") -> EmbedBuilder:
    """Quick success embed."""
    return (
        EmbedBuilder()
        .title(f"✅ {title}")
        .color(GREEN)
        .description(description)
    )


def error_embed(title: str, description: str = "") -> EmbedBuilder:
    """Quick error embed."""
    return (
        EmbedBuilder()
        .title(f"❌ {title}")
        .color(RED)
        .description(description)
    )


def health_embed(stats: dict) -> EmbedBuilder:
    """Server health report embed."""
    embed = (
        EmbedBuilder()
        .title("🏥 Server Health Report")
        .color(GREEN)
        .timestamp()
    )

    if "members" in stats:
        embed.field("Members", str(stats["members"]), inline=True)
    if "online" in stats:
        embed.field("Online", str(stats["online"]), inline=True)
    if "channels" in stats:
        embed.field("Channels", str(stats["channels"]), inline=True)
    if "roles" in stats:
        embed.field("Roles", str(stats["roles"]), inline=True)
    if "issues" in stats:
        issues = stats["issues"]
        if isinstance(issues, list) and issues:
            embed.field("⚠️ Issues Found", "\n".join(f"- {i}" for i in issues[:5]), inline=False)
        elif isinstance(issues, str) and issues:
            embed.field("⚠️ Issues Found", issues, inline=False)
    if "score" in stats:
        score = stats["score"]
        bar = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"
        embed.field("Health Score", f"{bar} {score}/100", inline=True)

    embed.footer("Azure \u00b7 health scan complete")
    return embed


def plan_embed(steps: list, request: str) -> EmbedBuilder:
    """Plan generation result embed."""
    steps_text = "\n".join(f"**{i+1}.** {s}" for i, s in enumerate(steps[:10]))
    if len(steps) > 10:
        steps_text += f"\n_...and {len(steps) - 10} more steps_"
    return (
        EmbedBuilder()
        .title("📋 Generated Plan")
        .description(f"**Request:** {request[:200]}")
        .color(YELLOW)
        .field("Steps", steps_text, inline=False)
        .footer("Reply 'yes' to execute, or ask me to modify anything.")
    )


def memory_reveal_embed(query: str, results: list) -> EmbedBuilder:
    """Show RAG memory retrieval results."""
    if not results:
        return (
            EmbedBuilder()
            .title("🔍 Memory Search")
            .description(f"**Query:** {query}\n_No relevant memories found._")
            .color(GRAY)
        )

    # Show up to 3 results
    chunks = []
    for r in results[:3]:
        text = r.get("text", str(r))[:200]
        source = r.get("source", "unknown")
        chunks.append(f"> {text}...\n> _Source: {source}_")

    body = "\n\n".join(chunks)
    if len(results) > 3:
        body += f"\n\n_...and {len(results) - 3} more memories_"

    return (
        EmbedBuilder()
        .title("🔍 Retrieved Memory")
        .description(f"**Query:** {query}\n\n{body}")
        .color(TEAL)
        .footer("Long-term memory · RAG retrieval")
    )


# ---------------------------------------------------------------------------
# Planning progress phases
# ---------------------------------------------------------------------------
PLANNING_PHASES = [
    ThinkingPhase("🔍", "Analyzing Request", "understanding what you want..."),
    ThinkingPhase("🛠️", "Loading Tools", "finding available actions..."),
    ThinkingPhase("📋", "Building Plan", "deciding execution order..."),
    ThinkingPhase("✍️", "Formatting", "preparing response..."),
]


# ---------------------------------------------------------------------------
# Response templates
# ---------------------------------------------------------------------------
RESPONSE_TEMPLATES = {
    "error": (
        "❌ **Error:** {error_message}\n\n"
        "{suggestion}"
    ),
    "permission_denied": (
        "🚫 **Permission Denied**\n"
        "You need the `{required_permission}` permission to use `{command}`."
    ),
    "unknown_command": (
        "❓ Unknown command `{command}`.\n"
        "Use `{prefix}help` to see available commands."
    ),
}

def render_template(template_name: str, **kwargs: object) -> str:
    """Render a response template with the given kwargs."""
    template = RESPONSE_TEMPLATES.get(template_name)
    if template is None:
        logger.warning("Unknown response template: %s", template_name)
        return f"Unknown template: {template_name}"
    try:
        return template.format(**kwargs)
    except KeyError as e:
        logger.warning("Missing template variable %s for '%s'", e, template_name)
        return f"Error rendering template '{template_name}': missing {e}"
