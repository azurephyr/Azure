"""Comprehensive integration test — simulates bot startup and verifies all systems connect."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestBotStartupIntegration:
    """Verify all subsystems initialize and connect properly."""

    def test_all_databases_initialize(self):
        """All 4 feature databases can be created."""
        from azure.case_db import CaseDatabase
        from azure.dead_chat_revival import RevivalDatabase
        from azure.ghost_moderation import GhostDatabase
        from azure.reputation_db import ReputationDatabase

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rep_db = ReputationDatabase(str(tmp_path / "rep.db"))
            case_db = CaseDatabase(str(tmp_path / "case.db"))
            ghost_db = GhostDatabase(str(tmp_path / "ghost.db"))
            revival_db = RevivalDatabase(str(tmp_path / "revival.db"))

            # Verify all have required methods
            assert hasattr(rep_db, "record_event")
            assert hasattr(rep_db, "get_reputation")
            assert hasattr(case_db, "create_case")
            assert hasattr(case_db, "get_case")
            assert hasattr(ghost_db, "log_action")
            assert hasattr(ghost_db, "add_shadow_mute")
            assert hasattr(revival_db, "set_enabled")
            assert hasattr(revival_db, "record_message")

            rep_db.close()
            case_db.close()
            ghost_db.close()
            revival_db.close()

    def test_config_portability_exports_and_imports(self):
        """Config portability can export and import settings."""
        from azure.config_portability import (
            apply_env_settings,
            build_export_package,
            collect_env_settings,
            import_from_package,
            validate_import_package,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / ".env"
            configs_dir = tmp_path / "configs"
            configs_dir.mkdir()
            health_file = tmp_path / "health.json"

            # Create test .env
            env_file.write_text("AZURE_TEST_KEY=test_value\nAZURE_DISCORD_TOKEN=token123")

            # Collect settings
            settings = collect_env_settings(env_file, redact=False)
            assert "AZURE_TEST_KEY" in settings

            # Build export package
            pkg = build_export_package(str(env_file), str(configs_dir), str(health_file))
            assert "version" in pkg
            assert "exported_at" in pkg

            # Validate
            valid, msg = validate_import_package(pkg)
            assert valid, msg

            # Import back
            results = import_from_package(pkg, str(env_file), str(configs_dir), str(health_file), overwrite=True)
            assert "env_keys" in results

    def test_all_background_loops_are_task_loops(self):
        """All 7 background loops are properly decorated discord.ext.tasks loops."""
        from bot.tasks import (
            autonomous_agent_loop,
            autonomous_scan_task,
            cron_check_loop,
            ghost_maintenance_loop,
            goal_executor_loop,
            periodic_scan,
            revival_scan_loop,
        )
        from discord.ext import tasks

        loops = [
            cron_check_loop,
            autonomous_agent_loop,
            goal_executor_loop,
            periodic_scan,
            autonomous_scan_task,
            ghost_maintenance_loop,
            revival_scan_loop,
        ]

        for loop in loops:
            assert isinstance(loop, tasks.Loop), f"{loop.coro.__name__} is not a tasks.Loop"
            assert hasattr(loop, "start"), f"{loop.coro.__name__} missing .start()"
            assert hasattr(loop, "cancel"), f"{loop.coro.__name__} missing .cancel()"

    def test_slash_command_register_functions_exist(self):
        """All 7 slash command handlers have register functions."""
        from bot.handlers.case_handler import register_case_commands
        from bot.handlers.config_handler import register_config_commands
        from bot.handlers.dead_chat_handler import register_revival_commands
        from bot.handlers.ghost_handler import register_ghost_commands
        from bot.handlers.reputation_handler import register_reputation_commands
        from bot.handlers.settings_handler import register_settings
        from bot.handlers.trace_handler import register_trace_commands

        for func in [
            register_case_commands,
            register_ghost_commands,
            register_reputation_commands,
            register_revival_commands,
            register_trace_commands,
            register_config_commands,
            register_settings,
        ]:
            assert callable(func)

    def test_cognitive_pipeline_components_exist(self):
        """Cognitive pipeline has all required components."""
        from azure.cognition import CognitivePipeline, CognitiveState

        # Check class exists and has required methods
        assert hasattr(CognitivePipeline, "process")
        assert hasattr(CognitivePipeline, "__init__")

        # CognitiveState should be a dataclass or similar
        state = CognitiveState()
        assert hasattr(state, "response") or hasattr(state, "__dict__")

    def test_agent_orchestrator_imports(self):
        """AzureAgent and related classes import correctly."""
        from azure.agent import AzureAgent
        from azure.model_selector import ModelSelector

        assert AzureAgent is not None
        assert ModelSelector is not None

    def test_moderation_engines_import(self):
        """All moderation engines import."""
        from azure.ai_moderation.moderation_engine import AIModerationEngine
        from azure.moderation import engine as mod_engine

        assert AIModerationEngine is not None

    def test_web_server_imports(self):
        """Web dashboard server imports."""
        from web.server import app

        assert app is not None

    def test_llm_providers_import(self):
        """All LLM provider modules import."""
        from azure.api_llm import ApiLLM, HybridLLM
        from azure.local_llm import LocalLLM

        assert ApiLLM is not None
        assert HybridLLM is not None
        assert LocalLLM is not None

    @pytest.mark.asyncio
    async def test_message_handler_function_signature(self):
        """Message handler has correct async signature."""
        from bot.handlers.message_handler import on_message

        assert asyncio.iscoroutinefunction(on_message)

    def test_context_singleton_accessible(self):
        """BotContext singleton is accessible and has required fields."""
        from bot.context import ctx

        assert hasattr(ctx, "agent")
        assert hasattr(ctx, "bot")
        assert hasattr(ctx, "cognitive_pipeline")
        assert hasattr(ctx, "cognitive_mode")
        assert hasattr(ctx, "chat_mode")

    def test_all_register_functions_called_in_setup(self):
        """Verify setup() calls all register functions (static analysis)."""
        import inspect

        import bot.discord_bot_v1 as dbv

        setup_source = inspect.getsource(dbv.setup)

        required_calls = [
            "register_case_commands",
            "register_ghost_commands",
            "register_reputation_commands",
            "register_revival_commands",
            "register_trace_commands",
            "register_config_commands",
            "register_settings",
        ]

        for call in required_calls:
            assert call in setup_source, f"setup() missing call to {call}"


class TestDatabaseCrossCompatibility:
    """Verify databases work together."""

    def test_reputation_and_case_databases_coexist(self):
        """Reputation and case databases can track the same user."""
        from azure.case_db import CaseDatabase
        from azure.reputation_db import ReputationDatabase, ReputationEvent

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rep_db = ReputationDatabase(str(tmp_path / "rep.db"))
            case_db = CaseDatabase(str(tmp_path / "case.db"))

            user_id = "user_123"
            guild_id = "guild_456"

            # Record reputation event
            event = ReputationEvent(
                target_id=user_id,
                target_name="TestUser",
                action_type="warn",
                reason="Test warning",
                source_guild_id=guild_id,
                source_guild_name="Test Guild",
                moderator_id="mod_1",
                moderator_name="Mod One",
            )
            rep_db.record_event(event)

            # Create case for same user
            case_id = case_db.create_case(
                target_id=user_id,
                target_name="TestUser",
                guild_id=guild_id,
                guild_name="Test Guild",
                reason="Test case",
            )

            # Both should have data
            rep_summary = rep_db.get_reputation(user_id)
            case = case_db.get_case(case_id)

            assert rep_summary.total_events == 1
            assert case is not None
            assert case["target_id"] == user_id

            rep_db.close()
            case_db.close()


class TestEndToEndFeatureFlow:
    """Test complete feature workflows."""

    def test_scam_trace_to_case_workflow(self):
        """Scam report → reputation event → case creation workflow."""
        import json

        from azure.case_db import CaseDatabase
        from azure.reputation_db import ReputationDatabase, ReputationEvent

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # 1. Write scam report to JSONL
            scam_report = {
                "user_id": "scammer_999",
                "user_name": "Scammer#1234",
                "guild_id": "guild_100",
                "reason": "fake nitro scam",
                "score": 95,
                "ts": 1234567890.0,
            }
            scam_file = tmp_path / "scam_reports.jsonl"
            scam_file.write_text(json.dumps(scam_report) + "\n")

            # 2. Record reputation event
            rep_db = ReputationDatabase(str(tmp_path / "rep.db"))
            event = ReputationEvent(
                target_id=scam_report["user_id"],
                target_name=scam_report["user_name"],
                action_type="ban",
                reason=scam_report["reason"],
                source_guild_id=scam_report["guild_id"],
                source_guild_name="Test Guild",
            )
            rep_db.record_event(event)

            # 3. Create case
            case_db = CaseDatabase(str(tmp_path / "case.db"))
            case_id = case_db.create_case(
                target_id=scam_report["user_id"],
                target_name=scam_report["user_name"],
                guild_id=scam_report["guild_id"],
                guild_name="Test Guild",
                severity="high",
                action_type="ban",
                reason=scam_report["reason"],
            )

            # Verify all 3 systems have the data
            reports = [json.loads(line) for line in scam_file.read_text().strip().split("\n")]
            assert len(reports) == 1
            assert reports[0]["score"] == 95

            rep_summary = rep_db.get_reputation(scam_report["user_id"])
            assert rep_summary.total_events == 1
            assert rep_summary.ban_count == 1

            case = case_db.get_case(case_id)
            assert case["severity"] == "high"

            rep_db.close()
            case_db.close()

    def test_ghost_moderation_to_revival_workflow(self):
        """Ghost moderation actions → dead chat revival can coexist."""
        from azure.dead_chat_revival import RevivalDatabase
        from azure.ghost_moderation import GhostDatabase

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ghost_db = GhostDatabase(str(tmp_path / "ghost.db"))
            revival_db = RevivalDatabase(str(tmp_path / "revival.db"))

            guild_id = "guild_100"
            channel_id = "channel_200"
            user_id = "user_300"

            # Enable ghost mode
            ghost_db.set_enabled(guild_id, True)
            assert ghost_db.is_enabled(guild_id)

            # Log a ghost action
            ghost_db.log_action(
                "warn",
                user_id,
                "TestUser",
                "mod_1",
                "Mod One",
                guild_id,
                reason="Test warning",
            )
            log = ghost_db.get_log(guild_id)
            assert len(log) == 1

            # Enable revival for same channel
            revival_db.set_enabled(guild_id, channel_id, True)
            assert revival_db.is_enabled(guild_id, channel_id)

            # Record message activity
            revival_db.record_message(guild_id, channel_id)
            last_time = revival_db.get_last_message_time(guild_id, channel_id)
            assert last_time > 0

            ghost_db.close()
            revival_db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
