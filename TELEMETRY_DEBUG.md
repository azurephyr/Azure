# Telemetry Debugging Guide

## Test Procedure

1. **Start Azure bot with logging visible**
   ```bash
   cd Azure
   python run_bot.py
   ```

2. **Send test message in Discord**
   ```
   Hello Azure
   ```

3. **Check the console logs for `[TELEMETRY_TRACE]` lines**

## Expected Log Sequence

If telemetry is working correctly, you should see:

```
[TELEMETRY_TRACE] Created tracker: <execution_id>
[TELEMETRY_TRACE] Sent initial progress message: <message_id>
[TELEMETRY_TRACE] Callback registered
[TELEMETRY_TRACE] do_agent_response() starting
[TELEMETRY_TRACE] Calling AGENT.handle() with tracker=<execution_id>
[TELEMETRY_TRACE] agent.handle() called: tracker=<ExecutionTracker object>, tracker_id=<execution_id>
[TELEMETRY_TRACE] Stored tracker in agent: <ExecutionTracker object>
[TELEMETRY_TRACE] Emitting AGENT_START
[TELEMETRY_TRACE] emit() called: AGENT_START - Processing message from USER (total events: 2, callbacks: 1)
[TELEMETRY_TRACE] Calling callback #1
[TELEMETRY_TRACE] Callback #1 fired: AGENT_START - Processing message from USER
[TELEMETRY_TRACE] Editing Discord message: ⏳ Processing request...
[TELEMETRY_TRACE] Successfully edited Discord message
[TELEMETRY_TRACE] Callback #1 completed
[TELEMETRY_TRACE] Emitting RAG_SEARCH
[TELEMETRY_TRACE] emit() called: RAG_SEARCH - Searching conversation history... (total events: 3, callbacks: 1)
[TELEMETRY_TRACE] Calling callback #1
[TELEMETRY_TRACE] Callback #2 fired: RAG_SEARCH - Searching conversation history...
[TELEMETRY_TRACE] Editing Discord message: ⏳ Processing request...\n⏳ Searching conversation history...
[TELEMETRY_TRACE] Successfully edited Discord message
[TELEMETRY_TRACE] Callback #1 completed
... (more events) ...
```

## Diagnostic Checklist

### ✅ Tracker Created?
Look for: `[TELEMETRY_TRACE] Created tracker: <id>`
- **YES** → Tracker creation works
- **NO** → ExecutionTracker import or instantiation failed

### ✅ Tracker Passed to Agent?
Look for: `[TELEMETRY_TRACE] agent.handle() called: tracker=<object>`
- **YES** → Parameter passing works
- **NO** → Check lambda in run_in_executor

### ✅ Tracker Stored?
Look for: `[TELEMETRY_TRACE] Stored tracker in agent: <object>`
- **YES** → Assignment to self._tracker works
- **NO** → Agent initialization issue

### ✅ Emit Called?
Look for: `[TELEMETRY_TRACE] emit() called: <action>`
- **YES** → Emit calls are happening
- **NO** → Check if self._tracker is None in agent

### ✅ Callbacks Fire?
Look for: `[TELEMETRY_TRACE] Calling callback #<n>`
- **YES** → Callback list is populated
- **NO** → add_callback() not called or callbacks cleared

### ✅ Discord Edits?
Look for: `[TELEMETRY_TRACE] Editing Discord message: <text>`
- **YES** → Edits are attempted
- **NO** → Rate limiting or pending flag blocking

### ✅ Edits Succeed?
Look for: `[TELEMETRY_TRACE] Successfully edited Discord message`
- **YES** → Discord API accepts edits
- **NO** → Check error message, permissions, or message deletion

## Common Failure Modes

### Symptom: No emit() logs after AGENT_START
**Cause:** Execution takes a different code path (e.g., discord_action branch)
**Fix:** Add telemetry to all branches in agent.handle()

### Symptom: Callbacks fire but no edits
**Cause:** Rate limiting (too fast) or pending flag stuck
**Fix:** Check `[TELEMETRY_TRACE] Skipped edit (rate limit)` messages

### Symptom: Edits fail silently
**Cause:** Message deleted, bot lost permissions, or Discord API error
**Fix:** Check error logs from exception handler

### Symptom: No callbacks fire at all
**Cause:** add_callback() not called or called on wrong tracker instance
**Fix:** Verify callback registration happens before agent call

### Symptom: emit() called but callback not firing
**Cause:** Callbacks list is empty or cleared
**Fix:** Check if tracker is being recreated or replaced

## Quick Verification

Grep the logs for key markers:

```bash
# Should see 1 line
grep "Created tracker" logs/azure.log

# Should see 1 line
grep "Callback registered" logs/azure.log

# Should see multiple lines (one per emit)
grep "emit() called" logs/azure.log

# Should see multiple lines (one per callback)
grep "Calling callback" logs/azure.log

# Should see multiple lines (one per successful edit)
grep "Successfully edited" logs/azure.log
```

If any of these return 0 results, that's where the flow breaks.

## Next Steps Based on Results

Share the complete log output containing all `[TELEMETRY_TRACE]` lines.

This will pinpoint the exact failure point.
