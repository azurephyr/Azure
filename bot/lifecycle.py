"""Bot lifecycle: startup, signal handling, and graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys

try:
    from bot.pydantic_config import config as _pc
except ImportError:
    _pc = None

logger = logging.getLogger("azure.discord")


def _close_sqlite_connections():
    """Close all SQLite database connections on shutdown."""
    from bot.context import ctx

    if ctx.agent:
        try:
            if hasattr(ctx.agent, 'memory_backend') and ctx.agent.memory_backend and hasattr(ctx.agent.memory_backend, 'close'):
                    ctx.agent.memory_backend.close()
                    logger.info("[azure] Memory backend closed")
        except Exception as e:
            logger.error(f"[azure] Error closing memory backend: {e}")
        try:
            if hasattr(ctx.agent, '_conn') and ctx.agent._conn:
                    ctx.agent._conn.close()
                    logger.info("[azure] Agent SQLite conn closed")
        except Exception as e:
            logger.warning("[shutdown] Failed to close agent SQLite conn: %s", e)
    try:
        from azure.database import get_shared_db
        db = get_shared_db()
        if hasattr(db, 'close'):
            db.close()
            logger.info("[azure] Shared database closed")
    except Exception as e:
        logger.error(f"[azure] Error closing shared database: {e}")
    try:
        from azure.rag_enhanced import HybridRAG
        if HybridRAG._instance is not None and hasattr(HybridRAG._instance, 'close'):
            HybridRAG._instance.close()
            logger.info("[azure] HybridRAG closed")
    except Exception as e:
        logger.warning("[shutdown] Failed to close HybridRAG: %s", e)


def main():
    """Main entry point with graceful shutdown handling."""
    import atexit
    import signal

    from bot.context import ctx
    from bot.discord_bot_v1 import (
        _llm_workers,
        bot,
        setup,
    )
    from bot.tasks import (
        autonomous_agent_loop,
        autonomous_scan_task,
        cron_check_loop,
        ghost_maintenance_loop,
        goal_executor_loop,
        periodic_scan,
        revival_scan_loop,
    )

    def cleanup_llm_workers():
        """Clean up all LLM workers."""
        logger.info("[azure] Cleaning up LLM workers...")
        for llm in _llm_workers:
            try:
                if hasattr(llm, 'stop'):
                    llm.stop()
                    logger.debug("[azure] Stopped LLM worker")
            except Exception as e:
                logger.error(f"[azure] Error stopping LLM worker: {e}")
        _llm_workers.clear()

    shutdown_event = asyncio.Event()
    shutdown_started = False

    async def shutdown_handler():
        """Graceful shutdown: cancel tasks, close connections, save state."""
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        ctx.shutting_down = True
        ctx.ready = False
        ctx.discord_connected = False
        logger.info("[azure] graceful shutdown initiated...")

        # Cancel all background task loops FIRST (before closing Discord)
        bg_loops = [
            ("cron_check_loop", cron_check_loop),
            ("autonomous_agent_loop", autonomous_agent_loop),
            ("goal_executor_loop", goal_executor_loop),
            ("periodic_scan", periodic_scan),
            ("autonomous_scan_task", autonomous_scan_task),
            ("ghost_maintenance_loop", ghost_maintenance_loop),
            ("revival_scan_loop", revival_scan_loop),
        ]
        for name, loop in bg_loops:
            try:
                if loop.is_running():
                    loop.cancel()
                    logger.info(f"[azure] Cancelled {name}")
                    task = getattr(loop, "get_task", lambda: None)()
                    if task is not None:
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            except Exception as e:
                logger.error(f"[azure] Error cancelling {name}: {e}")
        try:
            # Clean up LLM workers FIRST (prevents zombie processes)
            cleanup_llm_workers()

            # Close Discord connection
            if bot and not bot.is_closed():
                await bot.close()
                logger.info("[azure] Discord connection closed")

            # Stop health server
            if ctx.health_server:
                try:
                    ctx.health_server.stop()
                    logger.info("[azure] Health server stopped")
                except Exception as e:
                    logger.error(f"[azure] Error stopping health server: {e}")

            # Shutdown plugins
            if ctx.plugin_manager:
                try:
                    ctx.plugin_manager.shutdown_all()
                    logger.info("[azure] Plugins shut down")
                except Exception as e:
                    logger.error(f"[azure] Error shutting down plugins: {e}")

            # Save pending moderation data
            if ctx.agent and ctx.agent.moderation:
                try:
                    if hasattr(ctx.agent.moderation, 'reporter'):
                        reporter = ctx.agent.moderation.reporter
                        if hasattr(reporter, 'flush'):
                            result = reporter.flush()
                            if asyncio.iscoroutine(result):
                                await result
                    logger.info("[azure] Moderation data saved")
                except Exception as e:
                    logger.error(f"[azure] Error saving moderation data: {e}")

            # Close memory backend and all SQLite connections
            _close_sqlite_connections()

            # Stop cron scheduler
            if ctx.cron_scheduler:
                try:
                    ctx.cron_scheduler.stop()
                    logger.info("[azure] Cron scheduler stopped")
                except Exception as e:
                    logger.error(f"[azure] Error stopping cron: {e}")

            # Close voice system
            if ctx.voice_system:
                try:
                    if hasattr(ctx.voice_system, 'cleanup'):
                        await ctx.voice_system.cleanup()
                    logger.info("[azure] Voice system cleaned up")
                except Exception as e:
                    logger.error(f"[azure] Error cleaning up voice: {e}")

            logger.info("[azure] shutdown complete")
            logger.info("[azure] Shutdown complete. Goodbye!")


        except Exception as e:
            logger.error(f"[azure] Error during shutdown: {e}")
            logger.info(f"[azure] Error during shutdown: {e}")

    def signal_handler(signum, frame):
        """Handle SIGINT (Ctrl+C) and SIGTERM."""
        signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        logger.info(f"\n[azure] Received {signal_name}, initiating graceful shutdown...")

        shutdown_event.set()

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    with contextlib.suppress(OSError, AttributeError):
        signal.signal(signal.SIGTERM, signal_handler)

    # Register atexit handler as fallback
    def atexit_handler():
        """Fallback cleanup if process exits without signals."""
        if not shutdown_event.is_set():
            logger.info("[azure] Process exiting, cleaning up...")

            try:
                import __main__
                loop = getattr(__main__, '_azure_main_loop', None)
            except Exception as e:
                logger.warning("[shutdown] Failed to get main loop: %s", e)
                loop = None
            if loop is None or loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(shutdown_handler())
            finally:
                with contextlib.suppress(Exception):
                    loop.close()

    atexit.register(atexit_handler)

    try:
        runtime_phase = os.environ.get("AZURE_RUNTIME_MODERATION_PHASE", "").strip()
        phase = runtime_phase or (
            _pc.moderation_phase if _pc
            else os.environ.get("AZURE_MODERATION_PHASE", "dry_run")
        )

        # Allow setup() to register LLM workers for cleanup (subprocess backends)
        try:
            import bot.discord_bot_v1 as _dbv1
            from bot.discord_bot_v1 import register_llm_worker
            _dbv1.main._register_llm_worker = register_llm_worker
        except Exception as e:
            logger.debug("[azure] llm worker registration hook skipped: %s", e)

        setup(moderation_phase=phase)
        if not ctx.core_ready():
            logger.error("[azure] setup finished but core path is not ready — check LLM config")
        token = _pc.discord_token if _pc else os.environ.get("AZURE_DISCORD_TOKEN")
        if not token:
            logger.info("=" * 60)
            logger.info("AZURE_DISCORD_TOKEN not set.")
            logger.info("=" * 60)
            logger.info("")
            logger.info("HOW TO FIX:")
            logger.info("  1. Create a .env file from the template:")
            logger.info("     copy .env.example .env")
            logger.info("  2. Open .env in a text editor and replace:")
            logger.info("     AZURE_DISCORD_TOKEN=your-token-here")
            logger.info("     with your real Discord bot token.")
            logger.info("")
            logger.info("  3. Re-run Start Azure.bat or python run_bot.py")
            logger.info("")
            logger.info("Get your token at: https://discord.com/developers/applications")
            logger.info("=" * 60)
            sys.exit(1)

        # Start bot with graceful shutdown support
        logger.info("[azure] Starting bot... (Press Ctrl+C for graceful shutdown)")


        async def run_with_shutdown():
            """Run the bot, web server, and shutdown monitor concurrently.

            Starts the Discord bot, the web dashboard server, and a
            background task that monitors for shutdown signals.  All three
            run as concurrent asyncio tasks; when any one completes, the
            others are cancelled and cleaned up.
            """
            import __main__
            __main__._azure_main_loop = asyncio.get_running_loop()
            """Run bot with shutdown monitoring."""
            async def wait_for_shutdown():
                """Wait for shutdown signal."""
                await shutdown_event.wait()
                await shutdown_handler()

            # Start bot and shutdown monitor concurrently
            bot_task = asyncio.create_task(bot.start(token))
            shutdown_task = asyncio.create_task(wait_for_shutdown())
            tasks = [bot_task, shutdown_task]

            # Web dashboard (feature-flagged, default on)
            web_enabled = True
            if ctx.features is not None:
                web_enabled = bool(ctx.features.web)
            if web_enabled:
                from azure.database import get_shared_db
                from azure.telemetry import set_main_loop, set_telemetry_db
                from web.server import manager, start_web_server
                db = get_shared_db()
                ctx.db = db
                set_telemetry_db(db)
                set_main_loop(asyncio.get_running_loop())
                port = _pc.web_port if _pc else int(os.environ.get("AZURE_WEB_PORT", "8080"))
                web_task = asyncio.create_task(start_web_server(ctx.agent, bot, db, port=port))
                tasks.append(web_task)

                async def web_message_hook(message):
                    """Broadcast Discord messages to the web dashboard via WebSocket."""
                    try:
                        await manager.broadcast({
                            "type": "DISCORD_MESSAGE",
                            "data": {
                                "id": str(message.id),
                                "guild_id": str(message.guild.id) if message.guild else "",
                                "author": message.author.display_name,
                                "author_name": message.author.display_name,
                                "author_id": str(message.author.id),
                                "content": (message.content or "")[:500],
                                "channel": message.channel.name if hasattr(message.channel, "name") else "DM",
                                "channel_name": message.channel.name if hasattr(message.channel, "name") else "DM",
                                "guild_name": message.guild.name if message.guild else "DM",
                                "timestamp": message.created_at.timestamp() if message.created_at else 0,
                            }
                        })
                    except Exception as e:
                        logger.warning("[web] Failed to broadcast message to dashboard: %s", e)

                bot.add_listener(web_message_hook, "on_message")
            else:
                logger.info("[azure] web dashboard disabled (AZURE_FEATURE_WEB=0)")

            # Wait for any to complete
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )

            # Retrieve exceptions from done tasks to avoid "exception was never retrieved"
            for task in done:
                try:
                    exc = task.exception()
                    if exc:
                        logger.error(f"[azure] Task failed: {exc}")
                except (asyncio.CancelledError, asyncio.InvalidStateError):
                    pass

            try:
                # Cancel remaining tasks, then always run the complete cleanup path.
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            finally:
                await shutdown_handler()

        # Run the bot
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run_with_shutdown())

    except Exception as e:
        logger.info(f"\n{'='*60}")

        logger.error(f"FATAL ERROR: {e}")

        logger.info(f"{'='*60}")

        import traceback
        traceback.print_exc()
        sys.exit(1)
