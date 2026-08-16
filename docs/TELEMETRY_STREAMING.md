# Telemetry & Streaming Architecture

## Current Implementation

### Telemetry Updates

**Strategy: Intelligent Debouncing**

Events that occur within 0.3 seconds are automatically debounced:
- **First event** → Updates Discord message immediately
- **Rapid events** → Waits 0.3s, then shows all accumulated events
- **Spaced events** → Each shows individually

This prevents message "bursts" while maintaining responsiveness.

**Example Flow:**
```
0.0s: "Thinking..." → Discord edit #1
0.1s: "Greeting detected." → Debounced
0.2s: "Generating response..." → Debounced
0.5s: → Discord edit #2 (shows both lines)
```

Result: Smooth progression without overwhelming Discord's API.

### LLM Streaming

**Current Status: NOT SUPPORTED**

Azure's LLM backends do NOT support token streaming:

1. **Local LLM (llama-cpp-python / ctransformers)**
   - Uses synchronous `self._llm()` call
   - Returns complete text, not a generator
   - No streaming API available

2. **API LLM (OpenAI / Anthropic / Google)**
   - Uses `urllib.request.urlopen()`
   - Synchronous HTTP request
   - Returns complete JSON response
   - No streaming implementation

3. **Subprocess LLM**
   - Wraps Local LLM
   - Same limitations apply

**Why No Streaming?**

The current implementations prioritize:
- ✅ Simplicity (no complex async state management)
- ✅ Compatibility (works with all backends)
- ✅ Reliability (no partial response handling)

**Future Implementation Path:**

To add streaming, would need:

1. **For Local LLM:**
   ```python
   # llama-cpp-python supports streaming:
   for chunk in self._llm.create_completion(prompt, stream=True):
       token = chunk['choices'][0]['text']
       yield token
   ```

2. **For API LLM:**
   ```python
   # OpenAI supports SSE streaming:
   for line in response:
       if line.startswith('data: '):
           chunk = json.loads(line[6:])
           token = chunk['choices'][0]['delta'].get('content', '')
           if token:
               yield token
   ```

3. **Discord Integration:**
   ```python
   # Buffer tokens, edit every N tokens or M seconds
   buffer = []
   last_edit = time.time()
   
   for token in llm.generate_stream(prompt):
       buffer.append(token)
       if len(buffer) >= 10 or time.time() - last_edit > 1.0:
           await message.edit(content=''.join(buffer))
           last_edit = time.time()
   ```

**Why Not Implemented Yet:**

1. **Complexity:** Requires async/generator refactoring across entire LLM layer
2. **Discord Rate Limits:** Edits limited to 5/5s, streaming would hit limits
3. **User Experience:** Debounced events already provide good feedback
4. **Local LLM Speed:** 5-15 tok/s means full response in 2-5 seconds anyway

## Current User Experience

**What Users See:**

```
**Thinking...**
⏳ Greeting detected.
⏳ Generating response...
[0.3s pause for debounce]
[Response appears]
```

**Total time:** ~1-3 seconds for simple requests
**Updates:** 2-3 Discord edits (respects rate limits)
**Smoothness:** Good - events appear in logical progression

## Future Enhancements (v1.1+)

### If Streaming is Implemented:

**Benefits:**
- Real-time token-by-token generation visible
- Better UX for long responses (>500 tokens)
- Matches ChatGPT/Claude UI expectations

**Challenges:**
- Discord rate limits (5 edits per 5 seconds)
- Requires buffering (10-20 tokens per edit)
- Adds complexity to error handling
- Needs async refactoring

**Recommendation:**
Keep current implementation for v1.0, revisit for v1.1 if users request it.

## Summary

**Telemetry:** ✅ Working well with intelligent debouncing
**Streaming:** ❌ Not supported, not critical for v1.0
**User Experience:** ✅ Good - responsive without overwhelming
**Future Work:** Optional enhancement, not a blocker

---

*Last Updated: 2026-07-10*
