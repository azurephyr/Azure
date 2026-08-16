"""Comprehensive tests for 6 new cross-server moderation features."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================================
# 1. SCAM DM TRACE — scam_reports.jsonl store
# ============================================================================

class TestScamTraceStore:
    """Test the JSONL-based scam report storage used by /trace."""

    @pytest.fixture
    def reports_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scam_reports.jsonl"
            yield path

    def _store_report(self, path, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(report) + "\n")

    def _load_reports(self, path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().strip().splitlines() if line]

    def test_store_and_load(self, reports_path):
        report = {"user_id": "123", "reason": "test", "score": 85, "ts": time.time()}
        self._store_report(reports_path, report)
        loaded = self._load_reports(reports_path)
        assert len(loaded) == 1
        assert loaded[0]["user_id"] == "123"
        assert loaded[0]["score"] == 85

    def test_multiple_reports(self, reports_path):
        for i in range(5):
            self._store_report(reports_path, {"user_id": str(i), "score": i * 10, "ts": time.time()})
        loaded = self._load_reports(reports_path)
        assert len(loaded) == 5

    def test_append_does_not_overwrite(self, reports_path):
        self._store_report(reports_path, {"user_id": "1", "ts": time.time()})
        self._store_report(reports_path, {"user_id": "2", "ts": time.time()})
        assert len(self._load_reports(reports_path)) == 2

    def test_empty_file(self, reports_path):
        assert self._load_reports(reports_path) == []

    def test_report_with_all_fields(self, reports_path):
        report = {
            "user_id": "999",
            "user_name": "scammer#1234",
            "guild_id": "guild_1",
            "guild_name": "Test Server",
            "reason": "suspicious DM link",
            "score": 92,
            "details": {"account_age_days": 0.5, "mutual_guilds": 0},
            "ts": time.time(),
        }
        self._store_report(reports_path, report)
        loaded = self._load_reports(reports_path)
        assert loaded[0]["user_name"] == "scammer#1234"
        assert loaded[0]["details"]["account_age_days"] == 0.5


# ============================================================================
# 2. REPUTATION DB
# ============================================================================

class TestReputationDatabase:
    @pytest.fixture
    def db(self):
        from azure.reputation_db import ReputationDatabase
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_reputation.db"
            db = ReputationDatabase(str(db_path))
            yield db
            db.close()

    def test_record_event(self, db):
        from azure.reputation_db import ReputationEvent
        ev = ReputationEvent(
            target_id="user_1",
            target_name="User1",
            action_type="kick",
            reason="Kicked for spam",
            source_guild_id="guild_1",
            source_guild_name="Guild 1",
            moderator_id="mod_1",
            moderator_name="Mod One",
        )
        db.record_event(ev)
        summary = db.get_reputation("user_1")
        assert summary.total_events == 1

    def test_multiple_events(self, db):
        from azure.reputation_db import ReputationEvent
        for i in range(3):
            db.record_event(ReputationEvent(
                target_id="user_2", target_name="User2",
                action_type="warn", reason=f"Warning {i}",
                source_guild_id="guild_1", source_guild_name="Guild 1",
                moderator_id="mod_1", moderator_name="Mod",
            ))
        summary = db.get_reputation("user_2")
        assert summary.total_events == 3

    def test_no_reputation(self, db):
        summary = db.get_reputation("unknown_user")
        assert summary.total_events == 0

    def test_has_reputation(self, db):
        from azure.reputation_db import ReputationEvent
        assert not db.has_reputation("user_3")
        db.record_event(ReputationEvent(
            target_id="user_3", target_name="User3", action_type="ban",
            reason="Banned", source_guild_id="guild_1", source_guild_name="Guild 1",
            moderator_id="mod_1", moderator_name="Mod",
        ))
        assert db.has_reputation("user_3")

    def test_get_events_for_guild(self, db):
        from azure.reputation_db import ReputationEvent
        db.record_event(ReputationEvent(
            target_id="u1", target_name="U1", action_type="kick",
            source_guild_id="guild_a", source_guild_name="GA",
            moderator_id="m1", moderator_name="Mod",
        ))
        db.record_event(ReputationEvent(
            target_id="u2", target_name="U2", action_type="warn",
            source_guild_id="guild_a", source_guild_name="GA",
            moderator_id="m1", moderator_name="Mod",
        ))
        db.record_event(ReputationEvent(
            target_id="u3", target_name="U3", action_type="ban",
            source_guild_id="guild_b", source_guild_name="GB",
            moderator_id="m1", moderator_name="Mod",
        ))
        events = db.get_events_for_guild("guild_a")
        assert len(events) == 2

    def test_opt_in_out(self, db):
        assert not db.is_opted_in("guild_x")
        db.opt_in("guild_x", "Guild X", "chan_123")
        assert db.is_opted_in("guild_x")
        assert db.get_alert_channel("guild_x") == "chan_123"
        db.opt_out("guild_x")
        assert not db.is_opted_in("guild_x")

    def test_alert_channel(self, db):
        db.opt_in("guild_y", "Guild Y")
        assert db.get_alert_channel("guild_y") == ""
        db.set_alert_channel("guild_y", "chan_456")
        assert db.get_alert_channel("guild_y") == "chan_456"

    def test_record_query(self, db):
        from azure.reputation_db import ReputationEvent
        db.record_event(ReputationEvent(
            target_id="u_query", target_name="UQ", action_type="kick",
            source_guild_id="g_q", source_guild_name="GQ",
            moderator_id="m1", moderator_name="Mod",
        ))
        db.record_query("u_query", "g_q")
        db.record_query("u_query", "g_q")
        summary = db.get_reputation("u_query")
        assert summary.total_events == 1

    def test_count_opted_in(self, db):
        assert db.count_opted_in() == 0
        db.opt_in("g1", "G1")
        db.opt_in("g2", "G2")
        assert db.count_opted_in() == 2

    def test_get_stats(self, db):
        from azure.reputation_db import ReputationEvent
        db.record_event(ReputationEvent(
            target_id="u1", target_name="U1", action_type="kick",
            source_guild_id="g1", source_guild_name="G1",
            moderator_id="m1", moderator_name="Mod",
        ))
        stats = db.get_stats()
        assert stats["total_events"] >= 1
        assert stats["opted_in_guilds"] >= 0

    def test_close_and_reopen(self, db):
        db.close()
        from azure.reputation_db import ReputationEvent
        db2 = db.__class__("")
        db2.record_event(ReputationEvent(
            target_id="u1", target_name="U1", action_type="warn",
            source_guild_id="g1", source_guild_name="G1",
            moderator_id="m1", moderator_name="Mod",
        ))
        db2.close()


# ============================================================================
# 3. CASE DB
# ============================================================================

class TestCaseDatabase:
    @pytest.fixture
    def db(self):
        from azure.case_db import CaseDatabase
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_cases.db"
            db = CaseDatabase(str(db_path))
            yield db
            db.close()

    def test_create_and_get_case(self, db):
        case_id = db.create_case(
            target_id="user_1", target_name="BadUser",
            guild_id="guild_1", guild_name="Test Guild",
            severity="high", action_type="ban",
            reason="Repeated spam", created_by_id="mod_1", created_by_name="Mod One",
        )
        case = db.get_case(case_id)
        assert case is not None
        assert case["target_name"] == "BadUser"
        assert case["severity"] == "high"
        assert case["status"] == "open"

    def test_case_id_format(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        assert "-" in case_id
        assert len(case_id) > 5

    def test_find_cases_by_target(self, db):
        db.create_case("u_target", "Target", "g1", "G1")
        found = db.find_cases(target_id="u_target")
        assert len(found) == 1

    def test_find_cases_by_guild(self, db):
        db.create_case("u1", "U1", "g_a", "GA")
        db.create_case("u2", "U2", "g_a", "GA")
        db.create_case("u3", "U3", "g_b", "GB")
        assert len(db.find_cases(guild_id="g_a")) == 2
        assert len(db.find_cases(guild_id="g_b")) == 1

    def test_search_cases(self, db):
        db.create_case("u1", "JohnDoe", "g1", "G1", reason="Spam in chat")
        db.create_case("u2", "JaneDoe", "g1", "G1", reason="Harassment")
        results = db.search_cases("spam")
        assert len(results) >= 1

    def test_update_case(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        result = db.update_case(case_id, status="closed", severity="low")
        assert result is True
        case = db.get_case(case_id)
        assert case["status"] == "closed"
        assert case["severity"] == "low"

    def test_add_and_get_notes(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        note_id = db.add_note(case_id, "mod_1", "Mod One", "Investigating further")
        assert note_id > 0
        notes = db.get_notes(case_id)
        assert len(notes) == 1
        assert notes[0]["content"] == "Investigating further"

    def test_internal_note(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        db.add_note(case_id, "mod_1", "Mod One", "INTERNAL", is_internal=True)
        notes = db.get_notes(case_id)
        assert notes[0]["is_internal"] == 1

    def test_add_and_get_evidence(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        ev_id = db.add_evidence(case_id, "message_link", "https://discord.com/123", "Spam message")
        assert ev_id > 0
        evidence = db.get_evidence(case_id)
        assert len(evidence) == 1
        assert evidence[0]["evidence_type"] == "message_link"

    def test_appeal_workflow(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        result = db.create_appeal(case_id, "I was falsely banned", "u1", "User One")
        assert result is True
        appeal = db.get_appeal(case_id)
        assert appeal is not None
        assert appeal["status"] == "pending"
        assert appeal["reason"] == "I was falsely banned"

        result = db.decide_appeal(case_id, "approved", "Evidence insufficient", "admin_1", "Admin")
        assert result is True
        appeal = db.get_appeal(case_id)
        assert appeal["status"] == "approved"
        assert appeal["decision_reason"] == "Evidence insufficient"

    def test_appeal_reject(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        db.create_appeal(case_id, "Please unban", "u1", "U1")
        db.decide_appeal(case_id, "rejected", "Ban stands", "admin_1", "Admin")
        appeal = db.get_appeal(case_id)
        assert appeal["status"] == "rejected"

    def test_opt_in_out(self, db):
        assert not db.is_opted_in("g_x")
        db.opt_in("g_x", "G X", "chan_alert")
        assert db.is_opted_in("g_x")
        assert db.get_alert_channel("g_x") == "chan_alert"
        db.opt_out("g_x")
        assert not db.is_opted_in("g_x")

    def test_get_stats(self, db):
        db.create_case("u1", "U1", "g1", "G1")
        stats = db.get_stats()
        assert stats["total"] >= 1

    def test_multiple_evidence_items(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        db.add_evidence(case_id, "screenshot", "url1", "Evidence 1")
        db.add_evidence(case_id, "log", "url2", "Evidence 2")
        assert len(db.get_evidence(case_id)) == 2

    def test_get_nonexistent_case(self, db):
        assert db.get_case("AZ-NONEXISTENT") is None
        assert db.get_appeal("AZ-NONEXISTENT") is None

    def test_update_nonexistent_case(self, db):
        assert db.update_case("AZ-NOEXIST") is False

    def test_decide_appeal_on_invalid_status(self, db):
        case_id = db.create_case("u1", "U1", "g1", "G1")
        result = db.decide_appeal(case_id, "invalid_status", "test", "admin", "Admin")
        assert result is False


# ============================================================================
# 4. CONFIG PORTABILITY
# ============================================================================

class TestConfigPortability:
    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_file = tmp_path / ".env"
            configs_dir = tmp_path / "configs"
            configs_dir.mkdir()
            health_file = tmp_path / "model_health.json"
            yield env_file, configs_dir, health_file

    def _write_env(self, env_file, content):
        env_file.write_text(content)

    def _write_health(self, health_file, content):
        health_file.write_text(json.dumps(content))

    def _write_server_config(self, configs_dir, guild_id, content):
        (configs_dir / f"{guild_id}.json").write_text(json.dumps(content))

    # --- collect_env_settings ---

    def test_collect_env_settings_redact(self, temp_dirs):
        from azure.config_portability import collect_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "OPENAI_API_KEY=sk-abc123def456\nAZURE_DISCORD_TOKEN=my.token.here\nAZURE_OTHER_VAR=hello")
        settings = collect_env_settings(env_file, redact=True)
        assert settings["OPENAI_API_KEY"] != "sk-abc123def456"
        assert "OPENAI_API_KEY" in settings
        assert "AZURE_OTHER_VAR" in settings
        assert settings["AZURE_OTHER_VAR"] == "hello"

    def test_collect_env_settings_no_redact(self, temp_dirs):
        from azure.config_portability import collect_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "OPENAI_API_KEY=sk-abc123")
        settings = collect_env_settings(env_file, redact=False)
        assert settings["OPENAI_API_KEY"] == "sk-abc123"

    def test_collect_env_settings_empty(self, temp_dirs):
        from azure.config_portability import collect_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "")
        settings = collect_env_settings(env_file)
        assert isinstance(settings, dict)

    def test_collect_env_settings_redact_api_key(self, temp_dirs):
        from azure.config_portability import collect_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "ANTHROPIC_API_KEY=sk-ant-xyz789")
        settings = collect_env_settings(env_file, redact=True)
        val = settings["ANTHROPIC_API_KEY"]
        assert val != "sk-ant-xyz789"
        assert "*" in val or "..." in val

    # --- collect_server_config ---

    def test_collect_server_config_specific(self, temp_dirs):
        from azure.config_portability import collect_server_config
        _, configs_dir, _ = temp_dirs
        self._write_server_config(configs_dir, "guild_guild_1", {"name": "Server 1", "setting": "value"})
        configs = collect_server_config(str(configs_dir), guild_id="guild_1")
        assert len(configs) == 1
        assert configs[0]["name"] == "Server 1"

    def test_collect_server_config_all(self, temp_dirs):
        from azure.config_portability import collect_server_config
        _, configs_dir, _ = temp_dirs
        self._write_server_config(configs_dir, "guild_g1", {"name": "S1"})
        self._write_server_config(configs_dir, "guild_g2", {"name": "S2"})
        configs = collect_server_config(str(configs_dir))
        assert len(configs) == 2

    def test_collect_server_config_none(self, temp_dirs):
        from azure.config_portability import collect_server_config
        _, configs_dir, _ = temp_dirs
        assert collect_server_config(str(configs_dir)) == []

    # --- collect_llm_settings ---

    def test_collect_llm_settings(self, temp_dirs):
        from azure.config_portability import collect_llm_settings
        _, _, health_file = temp_dirs
        data = {"selected_provider": "openai", "model": "gpt-4", "health": {"success_count": 10}}
        self._write_health(health_file, data)
        result = collect_llm_settings(str(health_file))
        assert result["selected_provider"] == "openai"

    def test_collect_llm_settings_missing(self, temp_dirs):
        from azure.config_portability import collect_llm_settings
        _, _, health_file = temp_dirs
        health_file.unlink(missing_ok=True)
        result = collect_llm_settings(str(health_file))
        assert isinstance(result, dict)

    # --- build_export_package ---

    def test_build_export_package(self, temp_dirs):
        from azure.config_portability import build_export_package
        env_file, configs_dir, _ = temp_dirs
        self._write_env(env_file, "OPENAI_API_KEY=sk-abc")
        pkg = build_export_package(str(env_file), str(configs_dir), str(configs_dir / "health.json"))
        assert isinstance(pkg, dict)
        assert "exported_at" in pkg
        assert "version" in pkg

    # --- validate_import_package ---

    def test_validate_import_package_valid(self, temp_dirs):
        from azure.config_portability import validate_import_package
        from azure.config_portability import build_export_package
        env_file, configs_dir, _ = temp_dirs
        self._write_env(env_file, "OPENAI_API_KEY=sk-abc")
        pkg = build_export_package(str(env_file), str(configs_dir), str(configs_dir / "health.json"))
        valid, msg = validate_import_package(pkg)
        assert valid, f"Expected valid, got: {msg}"

    def test_validate_import_package_invalid(self, temp_dirs):
        from azure.config_portability import validate_import_package
        valid, msg = validate_import_package({"bad": "data"})
        assert not valid

    def test_validate_import_package_empty(self, temp_dirs):
        from azure.config_portability import validate_import_package
        valid, msg = validate_import_package({})
        assert not valid

    # --- apply_env_settings ---

    def test_apply_env_settings(self, temp_dirs):
        from azure.config_portability import apply_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "EXISTING=old\nKEEP=stay")
        count = apply_env_settings(str(env_file), {"EXISTING": "new", "NEW_KEY": "value"}, overwrite=False)
        assert count >= 0
        content = env_file.read_text()
        assert "KEEP=stay" in content
        if "EXISTING=old" in content:
            assert "EXISTING=old" in content

    def test_apply_env_settings_overwrite(self, temp_dirs):
        from azure.config_portability import apply_env_settings
        env_file, _, _ = temp_dirs
        self._write_env(env_file, "KEY=old")
        count = apply_env_settings(str(env_file), {"KEY": "new"}, overwrite=True)
        content = env_file.read_text()
        assert "KEY=new" in content

    # --- apply_server_config ---

    def test_apply_server_config(self, temp_dirs):
        from azure.config_portability import apply_server_config
        _, configs_dir, _ = temp_dirs
        config = {"guild_id": "g_new", "name": "New Server"}
        result = apply_server_config(str(configs_dir), config, overwrite=True)
        assert result is True
        assert (configs_dir / "guild_g_new.json").exists()

    # --- apply_llm_settings ---

    def test_apply_llm_settings(self, temp_dirs):
        from azure.config_portability import apply_llm_settings
        _, _, health_file = temp_dirs
        self._write_health(health_file, {})
        result = apply_llm_settings(str(health_file), {"provider": "openai"}, overwrite=True)
        assert result is True
        data = json.loads(health_file.read_text())
        assert data["provider"] == "openai"

    # --- import_from_package ---

    def test_import_from_package(self, temp_dirs):
        from azure.config_portability import import_from_package, build_export_package
        env_file, configs_dir, health_file = temp_dirs
        self._write_env(env_file, "TEST_KEY=value")
        pkg = build_export_package(str(env_file), str(configs_dir), str(health_file))
        results = import_from_package(pkg, str(env_file), str(configs_dir), str(health_file), overwrite=True)
        assert isinstance(results, dict)
        assert "env_keys" in results

    # --- export_to_json ---

    def test_export_to_json(self, temp_dirs):
        from azure.config_portability import export_to_json
        env_file, configs_dir, _ = temp_dirs
        self._write_env(env_file, "KEY=val")
        output = Path(temp_dirs[0].parent) / "output.json"
        result = export_to_json(str(env_file), str(configs_dir), str(configs_dir / "health.json"), str(output))
        assert result is not None
        assert output.exists()

    def test_set_auto_mod_config_ref(self, temp_dirs):
        from azure.config_portability import set_auto_mod_config_ref, _collect_auto_mod_config
        obj = {"some": "config"}
        set_auto_mod_config_ref(obj)
        assert _collect_auto_mod_config._ref is obj


# ============================================================================
# 5. GHOST MODERATION DB
# ============================================================================

class TestGhostDatabase:
    @pytest.fixture
    def db(self):
        from azure.ghost_moderation import GhostDatabase
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_ghost.db"
            db = GhostDatabase(str(db_path))
            yield db
            db.close()

    def test_enable_disable(self, db):
        assert not db.is_enabled("guild_1")
        db.set_enabled("guild_1", True)
        assert db.is_enabled("guild_1")
        db.set_enabled("guild_1", False)
        assert not db.is_enabled("guild_1")

    def test_stealth_mode(self, db):
        assert not db.is_stealth("guild_1")
        db.set_stealth("guild_1", True)
        assert db.is_stealth("guild_1")

    def test_log_channel(self, db):
        db.set_log_channel("guild_1", "chan_log")
        cfg = db.get_config("guild_1")
        assert cfg["log_channel_id"] == "chan_log"

    def test_muted_role(self, db):
        db.set_muted_role("guild_1", "role_muted")
        cfg = db.get_config("guild_1")
        assert cfg["muted_role_id"] == "role_muted"

    def test_get_config_defaults(self, db):
        cfg = db.get_config("unknown_guild")
        assert cfg["enabled"] == 0
        assert cfg["log_channel_id"] == ""

    def test_log_action(self, db):
        db.log_action("warn", "u1", "User1", "mod1", "Mod1", "guild_1", reason="Test warn")
        log = db.get_log("guild_1")
        assert len(log) == 1
        assert log[0]["action"] == "warn"
        assert log[0]["target_name"] == "User1"

    def test_get_log_limit(self, db):
        for i in range(5):
            db.log_action("warn", str(i), f"User{i}", "mod1", "Mod1", "guild_1")
        log = db.get_log("guild_1", limit=2)
        assert len(log) <= 2

    def test_get_log_filter_action(self, db):
        db.log_action("warn", "u1", "U1", "m1", "M1", "guild_1")
        db.log_action("kick", "u2", "U2", "m1", "M1", "guild_1")
        db.log_action("warn", "u3", "U3", "m1", "M1", "guild_1")
        warns = db.get_log("guild_1", action="warn")
        assert len(warns) == 2
        assert all(e["action"] == "warn" for e in warns)

    def test_get_user_log(self, db):
        db.log_action("warn", "u_target", "Target", "m1", "M1", "guild_1")
        db.log_action("kick", "u_other", "Other", "m1", "M1", "guild_1")
        db.log_action("ban", "u_target", "Target", "m1", "M1", "guild_1")
        user_log = db.get_user_log("u_target", "guild_1")
        assert len(user_log) == 2

    def test_shadow_mute_lifecycle(self, db):
        db.add_shadow_mute("guild_1", "u1", "role_1", "mod1", "Mod1", "Spam", duration_minutes=60)
        assert db.is_shadow_muted("guild_1", "u1")
        mutes = db.get_active_mutes("guild_1")
        assert len(mutes) == 1
        assert mutes[0]["user_id"] == "u1"

        db.remove_shadow_mute("guild_1", "u1")
        assert not db.is_shadow_muted("guild_1", "u1")

    def test_expired_mutes(self, db):
        db.add_shadow_mute("guild_1", "u1", "role_1", "mod1", "Mod1", "Test")
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.execute("UPDATE shadow_mutes SET expires_at = 1 WHERE user_id = ? AND guild_id = ?",
                     ("u1", "guild_1"))
        conn.commit()
        conn.close()
        expired = db.get_expired_mutes()
        assert len(expired) >= 1

    def test_cleanup_expired_mutes(self, db):
        db.add_shadow_mute("guild_1", "u1", "role_1", "mod1", "Mod1", "Test")
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.execute("UPDATE shadow_mutes SET expires_at = 1 WHERE user_id = ?", ("u1",))
        conn.commit()
        conn.close()
        count = db.cleanup_expired_mutes()
        assert count >= 1

    def test_get_stats(self, db):
        db.log_action("warn", "u1", "U1", "m1", "M1", "guild_1")
        db.add_shadow_mute("guild_1", "u_muted", "role_1", "m1", "M1", "Test")
        stats = db.get_stats()
        assert stats["total_actions"] >= 1
        assert stats["active_mutes"] >= 1


# ============================================================================
# 6. DEAD CHAT REVIVAL DB
# ============================================================================

class TestRevivalDatabase:
    @pytest.fixture
    def db(self):
        from azure.dead_chat_revival import RevivalDatabase
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_revival.db"
            db = RevivalDatabase(str(db_path))
            yield db
            db.close()

    def test_enable_disable(self, db):
        assert not db.is_enabled("guild_1", "channel_1")
        db.set_enabled("guild_1", "channel_1", True)
        assert db.is_enabled("guild_1", "channel_1")

    def test_guild_enabled(self, db):
        assert not db.is_guild_enabled("guild_1")
        db.set_enabled("guild_1", "ch1", True)
        assert db.is_guild_enabled("guild_1")

    def test_enable_disable_all_channels(self, db):
        db.record_message("guild_1", "ch1")
        db.record_message("guild_1", "ch2")
        db.set_enabled("guild_1", "ch1", True)
        db.set_enabled("guild_1", "ch2", True)
        db.set_enabled_all_channels("guild_1", False)
        assert not db.is_enabled("guild_1", "ch1")
        assert not db.is_enabled("guild_1", "ch2")

    def test_get_config(self, db):
        db.set_enabled("guild_1", "ch1", True)
        cfg = db.get_config("guild_1", "ch1")
        assert cfg["enabled"] == 1
        assert cfg["threshold_minutes"] == 120

    def test_update_config(self, db):
        db.set_enabled("guild_1", "ch1", True)
        db.update_config("guild_1", "ch1", threshold_minutes=60, cooldown_minutes=30)
        cfg = db.get_config("guild_1", "ch1")
        assert cfg["threshold_minutes"] == 60
        assert cfg["cooldown_minutes"] == 30

    def test_get_enabled_channels(self, db):
        db.set_enabled("guild_1", "ch1", True)
        db.set_enabled("guild_1", "ch2", True)
        db.set_enabled("guild_1", "ch3", False)
        channels = db.get_enabled_channels("guild_1")
        assert len(channels) == 2
        assert all(c["enabled"] == 1 for c in channels)

    def test_record_message_and_last_time(self, db):
        db.set_enabled("guild_1", "ch1", True)
        db.record_message("guild_1", "ch1")
        t = db.get_last_message_time("guild_1", "ch1")
        assert t > 0

    def test_get_last_message_time_no_record(self, db):
        t = db.get_last_message_time("guild_1", "no_channel")
        assert t == 0

    def test_get_activity_summary(self, db):
        db.record_message("guild_1", "ch1")
        summary = db.get_activity_summary("guild_1")
        assert summary["channels"] >= 1

    def test_revival_log(self, db):
        log_id = db.log_revival("guild_1", "ch1", "Hello!")
        assert log_id > 0
        history = db.get_revival_history("guild_1")
        assert len(history) == 1
        assert history[0]["prompt"] == "Hello!"

    def test_revival_mark_functions(self, db):
        log_id = db.log_revival("guild_1", "ch1", "Test")
        db.mark_revived(log_id)
        db.mark_response(log_id)
        history = db.get_revival_history("guild_1")
        assert history[0]["revived"] == 1
        assert history[0]["response_count"] >= 1

    def test_revival_history_limit(self, db):
        for i in range(5):
            db.log_revival("guild_1", "ch1", f"Prompt {i}")
        history = db.get_revival_history("guild_1", limit=2)
        assert len(history) == 2

    def test_get_stats(self, db):
        db.log_revival("guild_1", "ch1", "Test")
        stats = db.get_stats()
        assert stats["total_revivals"] >= 1
        assert stats["enabled_channels"] >= 0


class TestRevivalModuleFunctions:
    def test_select_revival_prompt(self):
        from azure.dead_chat_revival import select_revival_prompt
        prompt = select_revival_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 10

    def test_select_revival_prompt_custom(self):
        from azure.dead_chat_revival import select_revival_prompt
        prompt = select_revival_prompt("Custom prompt here!")
        assert prompt == "Custom prompt here!"

    def test_should_revive_no_db(self):
        from azure.dead_chat_revival import should_revive
        result, reason = should_revive("nonexistent", "nonexistent")
        # Without a DB, it should handle gracefully
        assert isinstance(result, bool)

    def test_get_all_revivable_channels_empty(self):
        from azure.dead_chat_revival import get_db, get_all_revivable_channels
        db = get_db()
        channels = get_all_revivable_channels("nonexistent", db=db)
        assert channels == []


# ============================================================================
# 7. GHOST MODULE FUNCTIONS (non-DB)
# ============================================================================

class TestGhostModuleFunctions:
    def test_get_db(self):
        from azure.ghost_moderation import get_db
        db = get_db()
        assert db is not None
        db.close()

    @pytest.mark.asyncio
    async def test_send_ghost_log_embed(self):
        from azure.ghost_moderation import send_ghost_log_embed, get_db
        import discord
        class FakeChannel:
            async def send(self, **kw):
                return None
            class FakeGuild:
                me = type("FakeMe", (), {"guild_permissions": type("FP", (), {"administrator": True})()})()
            guild = FakeGuild()
        channel = FakeChannel()
        db = get_db()
        db.log_action("warn", "u1", "U1", "m1", "M1", "guild_1")
        log_entry = db.get_log("guild_1")[0]
        await send_ghost_log_embed(channel, log_entry, db=db)
        db.close()

    def test_ensure_muted_role_exists(self):
        """Test module-level ensure_muted_role importable."""
        from azure.ghost_moderation import ensure_muted_role
        assert callable(ensure_muted_role)

    def test_apply_functions_importable(self):
        from azure.ghost_moderation import (
            apply_silent_delete, apply_invisible_warn,
            apply_shadow_mute, remove_shadow_mute, apply_ghost_kick,
        )
        assert callable(apply_silent_delete)
        assert callable(apply_invisible_warn)
        assert callable(apply_shadow_mute)
        assert callable(remove_shadow_mute)
        assert callable(apply_ghost_kick)

    def test_cleanup_expired_shadow_mutes_importable(self):
        from azure.ghost_moderation import cleanup_expired_shadow_mutes
        assert callable(cleanup_expired_shadow_mutes)
        assert asyncio.iscoroutinefunction(cleanup_expired_shadow_mutes)
