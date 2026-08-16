
# Adaptive Goal Recovery Engine (AGRE) Guide

**Making Azure Resilient, Persistent, and Goal-Oriented**

---

## Overview

The Adaptive Goal Recovery Engine (AGRE) is Azure's intelligent failure recovery system. Instead of giving up at the first error, AGRE:

1. **Persists the original goal** throughout execution
2. **Classifies failures** into specific types
3. **Analyzes root causes** using structured reasoning
4. **Generates recovery hypotheses** with confidence scores
5. **Attempts recoveries** in order of confidence
6. **Learns from successes** to improve over time
7. **Retries execution** after successful recovery

---

## Architecture

```
User Goal
    ↓
┌─────────────────────────────────────┐
│   Adaptive Goal Recovery Engine    │
│  (Orchestrates entire process)      │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│   1. Execute Original Task          │
└─────────────────────────────────────┘
    ↓ (if failure)
┌─────────────────────────────────────┐
│   2. Failure Classifier             │
│   (Categorizes the error)           │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│   3. Root Cause Analyzer            │
│   (Determines why it failed)        │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│   4. Recovery Strategy Generator    │
│   (Creates recovery plan)           │
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│   5. Recovery Executor              │
│   (Executes recovery actions)       │
└─────────────────────────────────────┘
    ↓ (if recovery successful)
┌─────────────────────────────────────┐
│   6. Retry Original Task            │
└─────────────────────────────────────┘
    ↓ (if still fails, repeat 2-6)
┌─────────────────────────────────────┐
│   7. Recovery Learner               │
│   (Stores successful patterns)      │
└─────────────────────────────────────┘
```

---

## Components

### 1. Failure Classifier
**Purpose:** Categorizes exceptions into actionable failure types.

**Failure Types:**
- Environment: `MISSING_DEPENDENCY`, `MISSING_FILE`, `PERMISSION_DENIED`
- Configuration: `MISSING_CONFIG`, `INVALID_CONFIG`, `MISSING_API_KEY`
- Resources: `OUT_OF_MEMORY`, `DISK_FULL`, `TIMEOUT`
- Network: `NETWORK_ERROR`, `RATE_LIMIT`, `SERVICE_UNAVAILABLE`
- Code: `TYPE_ERROR`, `VALUE_ERROR`, `ATTRIBUTE_ERROR`, `KEY_ERROR`

### 2. Root Cause Analyzer
**Purpose:** Performs structured analysis to determine WHY the failure occurred.

**Output:** List of `RootCause` objects with:
- Category (e.g., "missing_package")
- Description (human-readable)
- Confidence score (0.0 to 1.0)
- Evidence (error messages, stack traces)
- Suggested fix

### 3. Recovery Strategy Generator
**Purpose:** Generates concrete recovery plans.

**Strategy Properties:**
- Name and description
- Confidence score
- List of actions to execute
- Safety flags (`requires_approval`, `destructive`, `safe`)

**Action Types:**
- `INSTALL_PACKAGE`: Auto-install missing Python packages
- `CREATE_FILE`: Create missing files
- `CREATE_DIRECTORY`: Create missing directories
- `SET_ENV_VAR`: Set environment variables
- `RETRY_WITH_BACKOFF`: Exponential backoff retry
- `WAIT_AND_RETRY`: Wait before retrying (for rate limits)
- `USE_FALLBACK`: Use default/fallback values
- `CLEAR_CACHE`: Clear memory caches

### 4. Recovery Executor
**Purpose:** Safely executes recovery actions.

**Features:**
- Validates actions before execution
- Never modifies production source code
- Requires approval for destructive actions
- Returns detailed results

### 5. Recovery Learner
**Purpose:** Learns from successful recoveries.

**Capabilities:**
- Stores anonymized recovery patterns
- Tracks success rates per strategy
- Provides insights into best strategies
- Improves future recovery attempts

---

## Usage

### Basic Usage

```python
from azure.recovery import AdaptiveGoalRecoveryEngine, RecoveryConfig

# Create engine
agre = AdaptiveGoalRecoveryEngine(RecoveryConfig(
    max_retries=3,
    require_approval_for_destructive=True,
    learn_from_recoveries=True
))

# Define your task
def my_task(context):
    # Your code here
    import some_module  # Might not be installed
    return some_module.do_something()

# Execute with recovery
success, result, trace = agre.execute_with_recovery(
    goal="Process user data",
    execution_func=my_task,
    context={}
)

if success:
    print(f"Success: {result}")
else:
    print(f"Failed after {trace.total_retries} retries")
    print(f"Trace: {trace.to_dict()}")
```

### Decorator Usage

```python
from azure.recovery.integration import with_agre_recovery

@with_agre_recovery("Send Discord message")
def send_message(channel_id, content):
    # Your Discord message code
    return bot.send_message(channel_id, content)

# Now automatically recovers from failures
send_message("12345", "Hello!")
```

### Integration with Azure Agent

```python
from azure.recovery.integration import get_agre

# In Azure's execution pipeline
agre = get_agre()

success, result, trace = agre.execute_goal(
    goal="Execute user command",
    execution_func=lambda ctx: execute_command(ctx["command"]),
    context={"command": user_input}
)
```

---

## Configuration

```python
from azure.recovery import RecoveryConfig

config = RecoveryConfig(
    # Maximum number of execution retries
    max_retries=3,
    
    # Maximum recovery attempts per retry
    max_recovery_attempts_per_retry=5,
    
    # Learn from successful recoveries
    learn_from_recoveries=True,
    
    # Require approval for destructive actions
    require_approval_for_destructive=True,
    
    # Verbose logging
    verbose_logging=True,
    
    # Timeout for entire goal execution
    timeout_seconds=300
)
```

---

## Examples

### Example 1: Missing Dependency

```python
# Task fails with ImportError
def import_requests(ctx):
    import requests
    return requests.__version__

# AGRE automatically:
# 1. Classifies as MISSING_DEPENDENCY
# 2. Analyzes: "Package 'requests' not installed"
# 3. Generates: "Install with pip install requests"
# 4. Executes: subprocess.run(["pip", "install", "requests"])
# 5. Retries: import requests (SUCCESS!)
```

### Example 2: Missing Configuration

```python
# Task fails with KeyError
def load_config(ctx):
    api_key = os.environ["API_KEY"]
    return api_key

# AGRE automatically:
# 1. Classifies as MISSING_CONFIG
# 2. Analyzes: "Environment variable 'API_KEY' not set"
# 3. Generates: "Prompt user for API_KEY" (cannot auto-fix)
# 4. Reports: "Manual intervention required"
```

### Example 3: Network Timeout

```python
# Task fails with TimeoutError
def call_api(ctx):
    response = requests.get(url, timeout=1)
    return response.json()

# AGRE automatically:
# 1. Classifies as TIMEOUT
# 2. Analyzes: "Request timed out after 1 second"
# 3. Generates: "Retry with exponential backoff"
# 4. Executes: Wait 1s, 2s, 4s between retries
# 5. Retries: (eventually succeeds or gives up)
```

### Example 4: Rate Limit

```python
# Task fails with 429 Too Many Requests
def make_api_request(ctx):
    response = api.call()
    return response

# AGRE automatically:
# 1. Classifies as RATE_LIMIT
# 2. Analyzes: "API rate limit exceeded"
# 3. Generates: "Wait 60 seconds for rate limit reset"
# 4. Executes: time.sleep(60)
# 5. Retries: (succeeds after cooldown)
```

---

## Execution Trace

Every goal execution produces a complete trace:

```json
{
  "goal": "Process user message",
  "final_success": true,
  "total_retries": 2,
  "total_recoveries_attempted": 3,
  "total_recoveries_successful": 2,
  "duration_seconds": 15.3,
  "attempts": [
    {
      "attempt": 1,
      "success": false,
      "error_type": "MISSING_DEPENDENCY",
      "root_causes": ["missing_package"],
      "strategies_tried": 2,
      "recoveries_successful": 1
    },
    {
      "attempt": 2,
      "success": true,
      "error_type": null,
      "root_causes": [],
      "strategies_tried": 0,
      "recoveries_successful": 0
    }
  ]
}
```

---

## Safety Guarantees

### What AGRE Will Do

✅ Install Python packages (non-destructive)  
✅ Create missing files/directories (with approval)  
✅ Set environment variables (runtime only)  
✅ Retry with backoff  
✅ Wait for rate limits  
✅ Clear caches  
✅ Use fallback values  

### What AGRE Will NOT Do

❌ Modify production source code  
❌ Delete files or data  
❌ Execute arbitrary shell commands  
❌ Modify git repository  
❌ Change system configuration  
❌ Perform destructive actions without approval  

---

## Learning

AGRE learns from successful recoveries:

```python
# Get insights
insights = agre.get_recovery_insights()

print(f"Total patterns learned: {insights['total_patterns']}")
print(f"Overall success rate: {insights['overall_success_rate']:.1%}")
print(f"Best strategies: {insights['best_strategies']}")
```

Patterns are stored in `logs/recovery_patterns.json` and persist across sessions.

---

## Best Practices

### 1. Start Conservative

```python
config = RecoveryConfig(
    max_retries=2,  # Start low
    require_approval_for_destructive=True,  # Require approval
    verbose_logging=True  # See what happens
)
```

### 2. Use Specific Goals

```python
# ❌ Bad: Vague goal
agre.execute_with_recovery("Do something", func, {})

# ✅ Good: Specific goal
agre.execute_with_recovery(
    "Send welcome message to user 12345",
    func,
    {"user_id": "12345"}
)
```

### 3. Provide Context

```python
# Context helps recovery
success, result, trace = agre.execute_with_recovery(
    goal="Load user profile",
    execution_func=load_profile,
    context={
        "user_id": user_id,
        "database": db,
        "cache": cache
    }
)
```

### 4. Handle Unrecoverable Failures

```python
success, result, trace = agre.execute_with_recovery(...)

if not success:
    # Log detailed trace
    logger.error(f"Goal failed: {trace.to_dict()}")
    
    # Notify user
    await ctx.send("Sorry, I couldn't complete that request.")
    
    # Report to admins
    report_failure(trace)
```

---

## Integration Points

### In Azure's Planning System

```python
# In planner.py
from azure.recovery.integration import get_agre

agre = get_agre()

def execute_plan(plan):
    for step in plan.steps:
        success, result, trace = agre.execute_goal(
            goal=step.description,
            execution_func=lambda ctx: execute_step(step),
            context={"step": step, "plan": plan}
        )
        
        if not success:
            logger.error(f"Plan step failed: {trace.to_dict()}")
            return False, trace
    
    return True, None
```

### In Discord Message Handler

```python
# In discord_bot_v1.py
from azure.recovery.integration import with_agre_recovery

@with_agre_recovery("Process Discord message")
async def process_message(message):
    response = await agent.process(message.content)
    await message.channel.send(response)
```

### In Cognitive Pipeline

```python
# In cognitive_pipeline.py
from azure.recovery.integration import get_agre

agre = get_agre()

def run_phase(phase, context):
    success, result, trace = agre.execute_goal(
        goal=f"Execute phase: {phase.name}",
        execution_func=lambda ctx: phase.execute(ctx),
        context=context
    )
    
    if not success:
        # Try fallback reasoning
        result = phase.fallback_execute(context)
    
    return result
```

---

## Monitoring

### View Recovery Patterns

```bash
# Check learned patterns
cat logs/recovery_patterns.json
```

### Get Insights Programmatically

```python
insights = agre.get_recovery_insights()

print(f"Success rate: {insights['overall_success_rate']:.1%}")
print("Top 5 strategies:")
for strategy in insights['best_strategies']:
    print(f"  - {strategy['strategy_name']}: "
          f"{strategy['success_rate']:.1%} "
          f"({strategy['success_count']}/{strategy['total_attempts']})")
```

---

## Limitations

1. **Cannot fix logic errors**: AGRE recovers from environment/resource issues, not code bugs
2. **Requires retry budget**: Limited by `max_retries` configuration
3. **No time travel**: Cannot undo destructive user actions
4. **Depends on classifiable errors**: Unknown error types get generic handling
5. **Learning requires data**: Pattern recognition improves over time

---

## Future Enhancements

- Multi-step recovery plans
- Predictive failure prevention
- Cross-session pattern sharing
- User-defined recovery strategies
- Visual recovery dashboard
- Integration with telemetry

---

## Conclusion

AGRE makes Azure **resilient** by automatically recovering from common failures, **persistent** by never giving up on the user's goal, and **goal-oriented** by always remembering what the user wanted to achieve.

Instead of crashing with cryptic errors, Azure now:
1. Understands what went wrong
2. Figures out how to fix it
3. Fixes it automatically (when safe)
4. Tries again
5. Learns from success

This transforms Azure from a brittle chatbot into a robust, production-ready AI agent.

---

**See also:**
- `examples/agre_demo.py` - Working examples
- `azure/recovery/` - Source code
- `logs/recovery_patterns.json` - Learned patterns
