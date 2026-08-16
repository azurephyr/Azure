"""Guarded live Discord certification for an isolated staging guild.

The default mode is offline and prints the planned checks. Live mutations need
both ``--execute`` and an exact ``AZURE_LIVE_TEST_GUILD_ID`` environment match.
Only resources prefixed with ``azure-cert-`` are created, and cleanup is always
attempted before the client exits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import discord

PREFIX = "azure-cert-"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def certification_plan() -> list[str]:
    return [
        "connect to the exact staging guild",
        "verify Manage Channels, Manage Roles, and Manage Messages",
        "create a test role below the bot role",
        "create a test category and text channel",
        "edit the test channel name and topic",
        "set and clear a role-specific channel overwrite",
        "send, edit, and delete a test message",
        "delete the test channel, category, and role",
        "verify no azure-cert-* resources remain",
    ]


def validate_live_gate(guild_id: int, execute: bool) -> None:
    if not execute:
        return
    configured = os.environ.get("AZURE_LIVE_TEST_GUILD_ID", "").strip()
    if configured != str(guild_id):
        raise RuntimeError(
            "Refusing live mutations: AZURE_LIVE_TEST_GUILD_ID must exactly "
            "match --guild-id."
        )


async def run_live(guild_id: int) -> list[CheckResult]:
    token = os.environ.get("AZURE_DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("AZURE_DISCORD_TOKEN is required for live certification")

    results: list[CheckResult] = []
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    finished = asyncio.Event()
    created_channel = None
    created_category = None
    created_role = None
    run_id = str(int(time.time()))[-8:]

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append(CheckResult(name=name, passed=passed, detail=detail[:500]))

    @client.event
    async def on_ready() -> None:
        nonlocal created_channel, created_category, created_role
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise RuntimeError(f"Bot is not connected to staging guild {guild_id}")
            record("guild_connection", True, guild.name)

            me = guild.me
            if me is None:
                raise RuntimeError("Could not resolve the bot member in the staging guild")
            required = {
                "manage_channels": me.guild_permissions.manage_channels,
                "manage_roles": me.guild_permissions.manage_roles,
                "manage_messages": me.guild_permissions.manage_messages,
            }
            missing = [name for name, allowed in required.items() if not allowed]
            record("bot_permissions", not missing, ", ".join(missing) if missing else "ok")
            if missing:
                raise RuntimeError(f"Bot is missing staging permissions: {', '.join(missing)}")

            created_role = await guild.create_role(
                name=f"{PREFIX}role-{run_id}",
                reason="Azure staging certification",
            )
            record("create_role", True, str(created_role.id))
            await created_role.edit(mentionable=True, reason="Azure staging certification")
            await created_role.edit(mentionable=False, reason="Azure staging certification")
            record("edit_role", True)

            created_category = await guild.create_category(
                f"{PREFIX}category-{run_id}",
                reason="Azure staging certification",
            )
            record("create_category", True, str(created_category.id))
            created_channel = await guild.create_text_channel(
                f"{PREFIX}channel-{run_id}",
                category=created_category,
                topic="Azure staging certification",
                reason="Azure staging certification",
            )
            record("create_channel", True, str(created_channel.id))

            renamed = f"{PREFIX}edited-{run_id}"
            await created_channel.edit(
                name=renamed,
                topic="Azure staging certification edited",
                reason="Azure staging certification",
            )
            record("edit_channel", created_channel.name == renamed)

            overwrite = discord.PermissionOverwrite(send_messages=False)
            await created_channel.set_permissions(
                created_role,
                overwrite=overwrite,
                reason="Azure staging certification",
            )
            denied = created_channel.overwrites_for(created_role).send_messages is False
            record("set_permissions", denied)
            await created_channel.set_permissions(
                created_role,
                overwrite=None,
                reason="Azure staging certification",
            )
            record("clear_permissions", created_role not in created_channel.overwrites)

            message = await created_channel.send("Azure staging certification message")
            await message.edit(content="Azure staging certification message edited")
            record("send_edit_message", message.content.endswith("edited"))
            await message.delete()
            record("delete_message", True)
        except Exception as exc:
            record("live_run", False, f"{type(exc).__name__}: {exc}")
        finally:
            for name, resource in (
                ("cleanup_channel", created_channel),
                ("cleanup_category", created_category),
                ("cleanup_role", created_role),
            ):
                if resource is None:
                    continue
                try:
                    await resource.delete(reason="Azure staging certification cleanup")
                    record(name, True)
                except Exception as exc:
                    record(name, False, f"{type(exc).__name__}: {exc}")

            guild = client.get_guild(guild_id)
            leftovers = []
            if guild is not None:
                leftovers.extend(x.name for x in guild.channels if x.name.startswith(PREFIX))
                leftovers.extend(x.name for x in guild.roles if x.name.startswith(PREFIX))
            record("no_leftovers", not leftovers, ", ".join(leftovers))
            finished.set()
            await client.close()

    client_task = asyncio.create_task(client.start(token))
    try:
        await asyncio.wait_for(finished.wait(), timeout=120)
    finally:
        if not client.is_closed():
            await client.close()
        if not client_task.done():
            client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild-id", type=int, required=True, help="Dedicated staging guild ID")
    parser.add_argument("--execute", action="store_true", help="Connect and run safe mutations")
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_live_gate(args.guild_id, args.execute)
    if not args.execute:
        print(json.dumps({"mode": "plan", "guild_id": args.guild_id, "checks": certification_plan()}, indent=2))
        return 0

    results = asyncio.run(run_live(args.guild_id))
    payload = {
        "mode": "live",
        "guild_id": args.guild_id,
        "passed": all(result.passed for result in results),
        "results": [asdict(result) for result in results],
    }
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
