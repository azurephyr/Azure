from azure.moderation import Rule, RuleEngine


def test_matching_rule_returns_action():
    engine = RuleEngine([
        Rule("contains-badword", lambda text: "badword" in text.lower(), "flag", "matched blocked term")
    ])
    results = engine.evaluate("This contains BADWORD")
    assert len(results) == 1
    assert results[0].action == "flag"


def test_non_matching_rule_is_ignored():
    engine = RuleEngine([
        Rule("contains-badword", lambda text: "badword" in text.lower(), "flag", "matched blocked term")
    ])
    assert engine.evaluate("safe message") == []
