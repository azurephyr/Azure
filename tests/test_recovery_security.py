from azure.recovery.executor import RecoveryExecutor
from azure.recovery.strategy import ActionType, RecoveryAction, RecoveryStrategy


def _strategy(*, requires_approval=False, destructive=False, action_type=ActionType.USE_FALLBACK):
    return RecoveryStrategy(
        name="test-recovery",
        description="test",
        confidence=1.0,
        actions=[RecoveryAction(action_type, {"key": "mode", "default": "safe"}, "test")],
        requires_approval=requires_approval,
        destructive=destructive,
    )


def test_executor_rejects_unapproved_strategy():
    result = RecoveryExecutor().execute(_strategy(requires_approval=True), {})

    assert result.success is False
    assert "requires explicit approval" in result.message


def test_executor_allows_explicitly_approved_strategy():
    result = RecoveryExecutor().execute(_strategy(requires_approval=True), {"approved": True})

    assert result.success is True


def test_executor_redacts_environment_values():
    action = RecoveryAction(
        ActionType.SET_ENV_VAR,
        {"key": "TEST_SECRET", "value": "do-not-persist"},
        "set test variable",
    )
    strategy = RecoveryStrategy("set-env", "test", 1.0, [action])

    result = RecoveryExecutor().execute(strategy, {})

    assert result.success is True
    assert result.context_updates == {"env_vars_set": {"TEST_SECRET": "[REDACTED]"}}
