from azure.core import Decision, Event, EventBus, PolicyEngine


class Deny:
    name = "deny"

    def evaluate(self, context):
        return Decision(False, "blocked", self.name)


def test_event_bus_supports_async_handlers():
    bus = EventBus()
    seen = []

    async def handler(event):
        seen.append(event.payload["id"])

    bus.subscribe("message", handler)
    import asyncio
    asyncio.run(bus.publish(Event("message", {"id": 42})))
    assert seen == [42]


def test_policy_engine_stops_on_first_denial():
    decision = PolicyEngine([Deny()]).evaluate({"user_id": 1})
    assert decision.allowed is False
    assert decision.rule == "deny"
