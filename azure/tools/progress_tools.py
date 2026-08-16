"""Progress embed tools."""
import logging

import discord

from .server_tools import _embed_color

logger = logging.getLogger("tools.progress_tools")


class ProgressToolsMixin:
    """Mixin providing progress embed reporting for DiscordManagementTools."""

    async def _send_progress_embed(self, ctx, plan: dict, completed: int, total: int,
                                    results: list, status: str) -> discord.Message:
        embed = discord.Embed(
            title="Agentic Server Setup",
            description=f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n{status or ''}",
            color=_embed_color("info"),
        )
        embed.add_field(name="Progress", value=f"{completed}/{total} steps", inline=False)
        if results:
            recent = [r for r in results[-5:] if r.success]
            if recent:
                embed.add_field(name="Recent Successes", value="\n".join(f" {r.name}" for r in recent), inline=False)
        return await ctx.send(embed=embed)

    async def _update_progress_embed(self, msg: discord.Message, plan: dict, completed: int, total: int,
                                      results: list, status: str):
        embed = discord.Embed(
            title="Agentic Server Setup",
            description=f"**Plan:** {plan.get('analysis', 'No analysis')}",
            color=_embed_color("info") if completed < total else _embed_color("success"),
        )
        if status:
            embed.description += f"\n\n{status}"

        pct = completed / total if total > 0 else 0
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        embed.add_field(name=f"Progress {bar}", value=f"{completed}/{total} steps ({int(pct*100)}%)", inline=False)

        if results:
            lines = []
            for r in results[-5:]:
                icon = "✅" if r.success else "❌"
                lines.append(f"{icon} {r.action}: {r.name}")
            embed.add_field(name="Recent", value="\n".join(lines), inline=False)

        await msg.edit(embed=embed)

    async def _finalize_progress_embed(self, msg: discord.Message, plan: dict, results: list):
        completed = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        total = len(results)

        pct = len(completed) / total if total > 0 else 0
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)

        color = _embed_color("success") if len(failed) == 0 else (_embed_color("warning") if len(completed) > 0 else _embed_color("error"))
        embed = discord.Embed(
            title="Agentic Setup Complete" if len(failed) == 0 else "Setup Complete (with issues)",
            description=f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n"
                        f"**Progress:** {bar} {len(completed)}/{total}\n"
                        f"**Completed:** {len(completed)} | **Failed:** {len(failed)}",
            color=color,
        )

        if len(results) <= 10:
            lines = [f"{'✅' if r.success else '❌'} {r.action}: {r.name}" for r in results]
            embed.add_field(name="All Steps", value="\n".join(lines), inline=False)
        else:
            success_lines = [f" {r.action}: {r.name}" for r in completed[:5]]
            fail_lines = [f" {r.action}: {r.name} — {r.error[:50]}" for r in failed[:5]]
            if success_lines:
                embed.add_field(name="Successes", value="\n".join(success_lines), inline=False)
            if fail_lines:
                embed.add_field(name="Failures", value="\n".join(fail_lines), inline=False)

        if failed:
            embed.set_footer(text=f"{len(failed)} step(s) failed. Use undo to revert if needed.")

        await msg.edit(embed=embed)
