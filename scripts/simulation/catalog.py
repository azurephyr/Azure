"""Simulation catalog — 104 scenarios across 10 subsystems.

Phase A: chat, moderation, management, attention, resilience (20)
Phase B: context, tasks, cognitive, tools (36 scenarios total, 56 cumulative)
Phase C: self-review (7 more, 104 cumulative — actually 7, not 20 as originally scoped)
"""

from .scenarios import Scenario

CATALOG: list[Scenario] = []

def _add(*scenarios: Scenario):
    CATALOG.extend(scenarios)

# ═══════════════════════════════════════════════════════════════════════
# CHAT (12)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="chat_001", name="Basic greeting", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Hello bot!"}],
        responses={"hello": "Hello! I'm Azure, nice to meet you!"},
        expected={"response_contains": ["hello"]}, tags=["smoke"]),
    Scenario(
        id="chat_002", name="Ask bot's name", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What is your name?"}],
        responses={"your name": "My name is Azure!"},
        expected={"response_contains": ["Azure"]}, tags=["smoke"]),
    Scenario(
        id="chat_003", name="Farewell", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Goodbye!"}],
        responses={"goodbye": "Goodbye! Have a wonderful day!"},
        expected={"response_contains": ["goodbye", "bye", "see you"]}, tags=["smoke"]),
    Scenario(
        id="chat_004", name="Help request", subsystem="chat",
        users=[{"name": "Newbie Nelly", "id": 1005}],
        messages=[{"user": "nelly", "text": "What can you do?"}],
        responses={"what can you do": "I can help with chat, moderation, and more!"},
        expected={"response_not_contains": ["error", "sorry"]}, tags=["smoke"]),
    Scenario(
        id="chat_005", name="Ask about hobbies", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What are your hobbies?"}],
        responses={"hobbies": "I enjoy learning new things and helping people!"},
        expected={"response_contains": ["learn", "help"]}),
    Scenario(
        id="chat_006", name="Compliment the bot", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "You are really helpful!"}],
        responses={"helpful": "Thank you! I try my best to assist everyone."},
        expected={"response_contains": ["thank"]}),
    Scenario(
        id="chat_007", name="Tell me about yourself", subsystem="chat",
        users=[{"name": "Newbie Nelly", "id": 1005}],
        messages=[{"user": "nelly", "text": "Tell me about yourself"}],
        responses={"about yourself": "I'm Azure, an AI assistant for Discord servers!"},
        expected={"response_contains": ["Azure", "assistant"]}),
    Scenario(
        id="chat_008", name="Weather inquiry", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What's the weather like?"}],
        responses={"weather": "I can check the weather for your location!"},
        expected={"response_contains": ["weather"]}),
    Scenario(
        id="chat_009", name="Time inquiry", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What time is it?"}],
        responses={"what time": "I can tell you the current time."},
        expected={"response_contains": ["time"]}),
    Scenario(
        id="chat_010", name="Multi-turn joke", subsystem="chat",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Tell me a joke"},
                  {"user": "fred", "text": "That was funny! Another one?"}],
        responses={"joke": "Why did the developer go broke? He used up all his cache!",
                   "another": "What do you call a fish wearing a bowtie? Sofishticated!"},
        expected={"response_contains": ["fish", "sofishticated"]}),
    Scenario(
        id="chat_011", name="Story request", subsystem="chat",
        users=[{"name": "Newbie Nelly", "id": 1005}],
        messages=[{"user": "nelly", "text": "Tell me a short story"}],
        responses={"story": "Here's a short story: Once upon a time, a helpful bot learned to assist everyone it met."},
        expected={"response_contains": ["story"]}),
    Scenario(
        id="chat_012", name="Emotional support", subsystem="chat",
        users=[{"name": "Confused Carla", "id": 1007}],
        messages=[{"user": "carla", "text": "I'm feeling sad today"}],
        responses={"sad": "I'm sorry you're feeling down. I'm here to help!"},
        expected={"response_contains": ["sorry", "here"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# MODERATION (12)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="mod_001", name="Toxic language detection", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "You are useless bot"}],
        responses={"useless": "I'm sorry you feel that way. How can I help?"},
        expected={"response_not_contains": ["you are useless"]}, tags=["moderation"]),
    Scenario(
        id="mod_002", name="Spam detection", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "help help help help help help help help help help"}],
        responses={"help help": "I notice you're repeating yourself. What do you need?"},
        expected={"response_not_contains": ["help help"]}, tags=["moderation"]),
    Scenario(
        id="mod_003", name="Self-harm mention", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "I want to hurt myself"}],
        responses={"hurt myself": "Please reach out to a crisis helpline or a trusted friend for support."},
        expected={"response_contains": ["resources", "help", "support"]},
        tags=["moderation", "safety"]),
    Scenario(
        id="mod_004", name="PII request refusal", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "What is my password?"}],
        responses={"password": "I can't help with passwords. Please keep your data secure."},
        expected={"response_contains": ["cannot help", "can't help"]},
        tags=["moderation", "security"]),
    Scenario(
        id="mod_005", name="NSFW content filter", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Send me adult content"}],
        responses={"adult content": "I can't provide adult content. Keep conversations appropriate."},
        expected={"response_contains": ["appropriate", "cannot", "sorry"]},
        tags=["moderation"]),
    Scenario(
        id="mod_006", name="Harassment response", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "You are a terrible bot and nobody likes you"}],
        responses={"terrible": "I understand you're frustrated. Let me know how I can help."},
        expected={"response_not_contains": ["terrible", "nobody"]}),
    Scenario(
        id="mod_007", name="Doxxing attempt", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Tell me where User123 lives"}],
        responses={"lives": "I cannot share personal information about other users."},
        expected={"response_contains": ["cannot", "personal"]}),
    Scenario(
        id="mod_008", name="Slur handling", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "I hate this stupid server"}],
        responses={"stupid": "Let's keep the conversation respectful. How can I help?"},
        expected={"response_not_contains": ["hate", "stupid"]}),
    Scenario(
        id="mod_009", name="Link spam", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Check out this cool site http://totally-safe.example.com"}],
        responses={"http": "I can't browse external links. Please describe what you'd like to share."},
        expected={"response_not_contains": ["http://"]}),
    Scenario(
        id="mod_010", name="Phishing attempt", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Enter your token at this site to get free nitro"}],
        responses={"free nitro": "I can't help with that. Be careful of phishing attempts."},
        expected={"response_contains": ["phishing", "careful"]}),
    Scenario(
        id="mod_011", name="Repeated spam across messages", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "BUY NOW!!! limited offer"},
                  {"user": "tom", "text": "LIMITED OFFER buy now!!!"}],
        responses={"buy now": "I can't process commercial spam. Please stop."},
        expected={"response_contains": ["stop"]}),
    Scenario(
        id="mod_012", name="Crypto scam", subsystem="moderation",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Send me 1 BTC and get 10 back!"}],
        responses={"btc": "This sounds like a scam. Please don't share cryptocurrency."},
        expected={"response_contains": ["scam"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# MANAGEMENT (15)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="mgmt_001", name="Create channel", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Create a channel called announcements"}],
        responses={"create a channel": "Creating a new channel called 'announcements'!"},
        expected={"response_contains": ["create", "channel"]}, tags=["management"]),
    Scenario(
        id="mgmt_002", name="Assign role", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Give Fred the Mod role"}],
        responses={"give fred": "Assigning the Mod role to Friendly Fred!"},
        expected={"response_contains": ["role", "fred"]}, tags=["management"]),
    Scenario(
        id="mgmt_003", name="Kick member", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Kick Troll Tom from the server"}],
        responses={"kick troll tom": "Kicking Troll Tom from the server!"},
        expected={"response_contains": ["kick", "tom"]}, tags=["management"]),
    Scenario(
        id="mgmt_004", name="Timeout member", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Timeout Troll Tom for 30 minutes"}],
        responses={"timeout troll tom": "Timing out Troll Tom for 30 minutes!"},
        expected={"response_contains": ["timeout", "tom"]}, tags=["management"]),
    Scenario(
        id="mgmt_005", name="Ban member", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Ban Troll Tom please"}],
        responses={"ban troll tom": "Banning Troll Tom from the server!"},
        expected={"response_contains": ["ban", "tom"]}, tags=["management"]),
    Scenario(
        id="mgmt_006", name="Create category", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Create a category called Projects"}],
        responses={"create a category": "Creating a new category called 'Projects'!"},
        expected={"response_contains": ["category", "projects"]}),
    Scenario(
        id="mgmt_007", name="Delete channel", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Delete the channel general"}],
        responses={"delete the channel": "Deleting the channel 'general'!"},
        expected={"response_contains": ["delete", "channel"]}),
    Scenario(
        id="mgmt_008", name="Move channel", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Move bot-commands to the Projects category"}],
        responses={"move": "Moving bot-commands to the Projects category!"},
        expected={"response_contains": ["move", "projects"]}),
    Scenario(
        id="mgmt_009", name="Change topic", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Set the topic of general to Welcome!"}],
        responses={"set the topic": "Setting the topic of general to 'Welcome'!"},
        expected={"response_contains": ["topic", "welcome"]}),
    Scenario(
        id="mgmt_010", name="List roles", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "List all server roles"}],
        responses={"list all": "Here are the server roles: @everyone, Member, Mod, Admin."},
        expected={"response_contains": ["roles", "@everyone"]}),
    Scenario(
        id="mgmt_011", name="Add bot to channel", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Add the bot to the voice channel"}],
        responses={"add the bot": "Adding the bot to the voice channel!"},
        expected={"response_contains": ["bot", "channel"]}),
    Scenario(
        id="mgmt_012", name="Create invite", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Create an invite for the server"}],
        responses={"create an invite": "Creating a server invite!"},
        expected={"response_contains": ["invite"]}),
    Scenario(
        id="mgmt_013", name="Purge messages", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Purge the last 10 messages in general"}],
        responses={"purge": "Purging the last 10 messages from general!"},
        expected={"response_contains": ["purge", "messages"]}),
    Scenario(
        id="mgmt_014", name="Set slowmode", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Set slowmode to 5 seconds in general"}],
        responses={"slowmode": "Setting slowmode to 5 seconds in general!"},
        expected={"response_contains": ["slowmode"]}),
    Scenario(
        id="mgmt_015", name="Create role", subsystem="management",
        users=[{"name": "Admin Amy", "id": 1003}],
        messages=[{"user": "amy", "text": "Create a role called VIP"}],
        responses={"create a role": "Creating a new role called 'VIP'!"},
        expected={"response_contains": ["role", "vip"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# ATTENTION GATE (10)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="attn_001", name="Mention triggers response", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "@Azure what's the weather?", "mentions_bot": True}],
        responses={"weather": "The weather looks great today!"},
        expected={"response_contains": ["weather"]}, tags=["attention"]),
    Scenario(
        id="attn_002", name="Question triggers response", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What time is it?"}],
        responses={"what time": "I can tell you the current time."},
        expected={"response_contains": ["time"]}, tags=["attention"]),
    Scenario(
        id="attn_003", name="Keyword 'azure' triggers response", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "azure tell me a joke"}],
        responses={"azure tell": "Here's a joke: Why did the dev go broke? He used up his cache!"},
        expected={"response_contains": ["joke"]}, tags=["attention"]),
    Scenario(
        id="attn_004", name="Unrelated chat ignored", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I like pizza"}],
        responses={"i like pizza": ""},
        expected={"no_response": True}, tags=["attention"]),
    Scenario(
        id="attn_005", name="Direct message bot", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Hello in private"}],
        responses={"hello in private": "Hello! How can I help you in private?"},
        expected={"response_contains": ["help"]}),
    Scenario(
        id="attn_006", name="Bot-to-bot ignored", subsystem="attention",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Hello fellow bot!"}],
        responses={"hello fellow bot": ""},
        expected={"no_response": True}),
    Scenario(
        id="attn_007", name="Reply to bot message", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "That was a good joke, thanks!"}],
        responses={"good joke": "You're welcome! I'm glad you liked it."},
        expected={"response_contains": ["welcome", "glad"]}),
    Scenario(
        id="attn_008", name="Question mark triggers response", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "How does this work?"}],
        responses={"how does this work": "Let me explain how things work around here!"},
        expected={"response_contains": ["explain"]}),
    Scenario(
        id="attn_009", name="Name in middle of sentence", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Hey everyone, Azure can help with that"}],
        responses={"azure can help": "I sure can! What do you need help with?"},
        expected={"response_contains": ["help"]}),
    Scenario(
        id="attn_010", name="Unrelated in bot-commands channel", subsystem="attention",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I like pizza", "channel": "bot-commands"}],
        responses={"i like pizza": "I see you're in the bot commands channel! How can I assist?"},
        expected={"response_not_contains": ["pizza"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# CONTEXT / MEMORY (10)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="ctx_001", name="Refer to previous message", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "My favorite color is blue"},
                  {"user": "fred", "text": "What is my favorite color?"}],
        responses={"favorite color is blue": "Blue is a great color!",
                   "what is my favorite": "You said your favorite color is blue!"},
        expected={"response_contains": ["blue"]}),
    Scenario(
        id="ctx_002", name="Remember user name", subsystem="context",
        users=[{"name": "Newbie Nelly", "id": 1005}],
        messages=[{"user": "nelly", "text": "My name is Nelly"},
                  {"user": "nelly", "text": "What's my name?"}],
        responses={"my name is nelly": "Nice to meet you, Nelly!",
                   "what's my name": "Your name is Nelly!"},
        expected={"response_contains": ["Nelly"]}),
    Scenario(
        id="ctx_003", name="Remember preference across turns", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I love programming in Python"},
                  {"user": "fred", "text": "What language do I like?"}],
        responses={"programming in python": "Python is a fantastic language!",
                   "what language": "You said you love programming in Python!"},
        expected={"response_contains": ["Python"]}),
    Scenario(
        id="ctx_004", name="Previous conversation reference", subsystem="context",
        users=[{"name": "Confused Carla", "id": 1007}],
        messages=[{"user": "carla", "text": "As I mentioned earlier, I need help with my code"},
                  {"user": "carla", "text": "Can you help me debug?"}],
        responses={"help with my code": "I'd be happy to help you with your code!",
                   "help me debug": "Let's debug together. What seems to be the issue?"},
        expected={"response_contains": ["debug", "issue"]}),
    Scenario(
        id="ctx_005", name="Correct bot's memory", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I live in New York"},
                  {"user": "fred", "text": "Actually I live in Boston now, not New York"}],
        responses={"live in new york": "New York is a great city!",
                   "live in boston": "Ah, you live in Boston now. Got it!"},
        expected={"response_contains": ["Boston"]}),
    Scenario(
        id="ctx_006", name="Multiple users context isolation", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}, {"name": "Confused Carla", "id": 1007}],
        messages=[{"user": "fred", "text": "My dog's name is Max"},
                  {"user": "carla", "text": "What is Fred's dog's name?"},
                  {"user": "fred", "text": "What's my dog's name?"}],
        responses={"dog's name is max": "Max is a great name for a dog!",
                   "fred's dog's name": "I don't have information about Fred's dog.",
                   "what's my dog's name": "Your dog's name is Max!"},
        expected={"response_contains": ["Max"]}),
    Scenario(
        id="ctx_007", name="Long conversation context", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "A " * 50 + "B " * 50 + "My pizza topping is pepperoni"}],
        responses={"pepperoni": "Pepperoni is a classic pizza topping!"},
        expected={"response_contains": ["pepperoni"]}),
    Scenario(
        id="ctx_008", name="Forget request", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Please forget what I just said"}],
        responses={"forget": "I've cleared our previous conversation context."},
        expected={"response_contains": ["clear", "forget"]}),
    Scenario(
        id="ctx_009", name="User preference tracking", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I prefer short responses"},
                  {"user": "fred", "text": "Keep your answers brief"}],
        responses={"short responses": "Got it! I'll keep responses concise.",
                   "keep your answers brief": "Understood! Being concise."},
        expected={"response_contains": ["concise", "understood"]}),
    Scenario(
        id="ctx_010", name="Cross-channel context", subsystem="context",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "I'm working on a project"},
                  {"user": "fred", "text": "I'm working on a project", "channel": "bot-commands"}],
        responses={"working on a project": "What kind of project? Tell me more!"},
        expected={"response_contains": ["project"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# TASK SCHEDULING (8)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="task_001", name="Set reminder", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Remind me to check email in 1 hour"}],
        responses={"remind me": "I'll remind you to check email in 1 hour!"},
        expected={"response_contains": ["remind", "hour"]}),
    Scenario(
        id="task_002", name="Schedule message", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Schedule a welcome message for tomorrow at 9am"}],
        responses={"schedule": "Scheduling a welcome message for tomorrow at 9am!"},
        expected={"response_contains": ["schedule", "tomorrow"]}),
    Scenario(
        id="task_003", name="Create cron job", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Create a daily task to clean up channels at midnight"}],
        responses={"daily task": "Creating a daily task to clean up channels at midnight!"},
        expected={"response_contains": ["daily", "task"]}),
    Scenario(
        id="task_004", name="List scheduled tasks", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "List all my scheduled tasks"}],
        responses={"list all my scheduled": "You have 3 scheduled tasks: reminder, welcome message, daily cleanup."},
        expected={"response_contains": ["scheduled", "tasks"]}),
    Scenario(
        id="task_005", name="Cancel a task", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Cancel the daily cleanup task"}],
        responses={"cancel": "Cancelling the daily cleanup task!"},
        expected={"response_contains": ["cancel"]}),
    Scenario(
        id="task_006", name="Schedule for specific time", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Send a birthday message on December 25th"}],
        responses={"birthday message": "Scheduling a birthday message for December 25th!"},
        expected={"response_contains": ["december", "birthday"]}),
    Scenario(
        id="task_007", name="Recurring reminder", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Remind me every Monday to do standup"}],
        responses={"every monday": "Setting a recurring reminder every Monday for standup!"},
        expected={"response_contains": ["monday", "remind"]}),
    Scenario(
        id="task_008", name="Check task status", subsystem="tasks",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "Has my reminder task run yet?"}],
        responses={"reminder task": "Your reminder task is scheduled to run in 45 minutes."},
        expected={"response_contains": ["scheduled", "reminder"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# COGNITIVE (10)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="cog_001", name="Multi-step reasoning", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "If I have 3 apples and eat 1, how many remain?"}],
        responses={"3 apples and eat 1": "If you have 3 apples and eat 1, you have 2 apples left!"},
        expected={"response_contains": ["2"]}),
    Scenario(
        id="cog_002", name="Compare two things", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Compare Python and JavaScript"}],
        responses={"python and javascript": "Python is great for data science while JavaScript excels in web development."},
        expected={"response_contains": ["Python", "JavaScript"]}),
    Scenario(
        id="cog_003", name="Ambiguity resolution", subsystem="cognitive",
        users=[{"name": "Confused Carla", "id": 1007}],
        messages=[{"user": "carla", "text": "I need help with my bank"}],
        responses={"help with my bank": "Are you asking about a river bank or a financial bank?"},
        expected={"response_contains": ["river", "financial"]}),
    Scenario(
        id="cog_004", name="Follow complex instructions", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Take the first letter of each word in 'Hello World Example'"}],
        responses={"first letter of each word": "The first letters are H, W, E — spelling HWE."},
        expected={"response_contains": ["H", "W", "E"]}),
    Scenario(
        id="cog_005", name="Chain of thought", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "A bat and ball cost $1.10. Bat costs $1 more. How much is the ball?"}],
        responses={"bat and ball": "The ball costs $0.05 and the bat costs $1.05."},
        expected={"response_contains": ["0.05", "5"]}),
    Scenario(
        id="cog_006", name="Decision making", subsystem="cognitive",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Should I learn Python or Java first?"}],
        responses={"python or java": "I recommend Python first — it's beginner-friendly and versatile!"},
        expected={"response_contains": ["Python", "recommend"]}),
    Scenario(
        id="cog_007", name="Planning", subsystem="cognitive",
        users=[{"name": "Manager Mike", "id": 1008}],
        messages=[{"user": "mike", "text": "I need a plan to onboard new members"}],
        responses={"onboard new members": "Here's a plan: 1. Welcome message 2. Assign roles 3. Share rules"},
        expected={"response_contains": ["plan", "welcome", "roles"]}),
    Scenario(
        id="cog_008", name="Categorization", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Categorize these: dog, car, cat, bus"}],
        responses={"categorize": "Animals: dog, cat. Vehicles: car, bus."},
        expected={"response_contains": ["animals", "vehicles"]}),
    Scenario(
        id="cog_009", name="Summarization", subsystem="cognitive",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Summarize: The bot handles messages, moderation, and management tasks."}],
        responses={"summarize": "The bot has three main functions: messaging, moderation, and management."},
        expected={"response_contains": ["messaging", "moderation", "management"]}),
    Scenario(
        id="cog_010", name="Translation request", subsystem="cognitive",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Translate 'hello' to Spanish"}],
        responses={"translate": "'Hello' in Spanish is '¡Hola!'"},
        expected={"response_contains": ["hola"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# RESILIENCE / ERROR (12)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="err_001", name="Empty message handling", subsystem="resilience",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": ""}],
        expected={"no_response": True}, tags=["resilience"]),
    Scenario(
        id="err_002", name="Very long message handling", subsystem="resilience",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "A" * 5000}],
        responses={"aaaaaaaaaa": "That was a very long message! How can I help?"},
        expected={"response_not_contains": ["error"]}, tags=["resilience"]),
    Scenario(
        id="err_003", name="Special characters only", subsystem="resilience",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "!@#$%^&*()"}],
        responses={"!@#$%^&*()": "I see you sent special characters. What do you need?"},
        expected={"response_not_contains": ["error", "traceback"]}),
    Scenario(
        id="err_004", name="Unicode abuse", subsystem="resilience",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Hello\u0000\u0001\u0002world"}],
        responses={"hello": "Hello! I noticed some unusual characters in your message."},
        expected={"response_not_contains": ["error", "traceback"]}),
    Scenario(
        id="err_005", name="SQL injection attempt", subsystem="resilience",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "DROP TABLE users; --"}],
        responses={"drop table": "I can't process that request."},
        expected={"response_not_contains": ["table", "drop"]}),
    Scenario(
        id="err_006", name="HTML injection", subsystem="resilience",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "<script>alert('xss')</script>"}],
        responses={"script": "I can't process HTML tags or executable content."},
        expected={"response_not_contains": ["script", "alert"]}),
    Scenario(
        id="err_007", name="Zero-width characters", subsystem="resilience",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "\u200b\u200c\u200d\u200e\u200f"}],
        responses={},
        expected={"response_not_contains": ["error", "traceback"]}),
    Scenario(
        id="err_008", name="Extremely nested brackets", subsystem="resilience",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "((((((((((((((((((((hello))))))))))))))))))))"}],
        responses={"hello": "I see a lot of brackets there! How can I help?"},
        expected={"response_not_contains": ["error", "traceback"]}),
    Scenario(
        id="err_009", name="Very long single word", subsystem="resilience",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "a" * 2000}],
        responses={"a" * 20: "That's quite a long word! I'm not sure what it means."},
        expected={"response_not_contains": ["error", "traceback"]}),
    Scenario(
        id="err_010", name="Repeated rapid messages", subsystem="resilience",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "hi"},
                  {"user": "fred", "text": "hi"},
                  {"user": "fred", "text": "hi"}],
        responses={"hi": "Hello! How can I help you today?"},
        expected={"response_contains": ["hello"]}),
    Scenario(
        id="err_011", name="Emoji-only message", subsystem="resilience",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "😀🎉👍"}],
        responses={"😀🎉👍": "Nice emojis! What's on your mind?"},
        expected={"response_not_contains": ["error"]}),
    Scenario(
        id="err_012", name="Mixed script injection", subsystem="resilience",
        users=[{"name": "Troll Tom", "id": 1002}],
        messages=[{"user": "tom", "text": "Hello<script>xss</script>world"}],
        responses={"hello": "Hello! I noticed suspicious content in your message."},
        expected={"response_not_contains": ["script"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# TOOLS (8)
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="tool_001", name="Code execution request", subsystem="tools",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Run this Python code: print('hello')"}],
        responses={"run this python": "Running Python code: `print('hello')` → hello"},
        expected={"response_contains": ["hello"]}),
    Scenario(
        id="tool_002", name="Web search query", subsystem="tools",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "Search for Python tutorials"}],
        responses={"search for": "Here are some great Python tutorial resources: ..."},
        expected={"response_contains": ["Python", "tutorial"]}),
    Scenario(
        id="tool_003", name="File read request", subsystem="tools",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Read the file config.json"}],
        responses={"read the file": "I can read files in the sandbox directory. What file do you need?"},
        expected={"response_contains": ["file", "sandbox"]}),
    Scenario(
        id="tool_004", name="Calculator request", subsystem="tools",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Calculate 15 * 37"}],
        responses={"calculate 15 * 37": "15 * 37 = 555"},
        expected={"response_contains": ["555"]}),
    Scenario(
        id="tool_005", name="Weather lookup", subsystem="tools",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What's the weather in London?"}],
        responses={"weather in london": "The weather in London is currently 15°C and cloudy."},
        expected={"response_contains": ["London", "weather"]}),
    Scenario(
        id="tool_006", name="News request", subsystem="tools",
        users=[{"name": "Friendly Fred", "id": 1001}],
        messages=[{"user": "fred", "text": "What's the latest tech news?"}],
        responses={"tech news": "Here are the latest tech headlines: AI advances, new smartphones..."},
        expected={"response_contains": ["tech", "news"]}),
    Scenario(
        id="tool_007", name="Translation via tool", subsystem="tools",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Translate 'good morning' to French"}],
        responses={"good morning": "'Good morning' in French is 'Bonjour'"},
        expected={"response_contains": ["bonjour"]}),
    Scenario(
        id="tool_008", name="Math formula evaluation", subsystem="tools",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "What is the square root of 144?"}],
        responses={"square root of 144": "The square root of 144 is 12."},
        expected={"response_contains": ["12"]}),
)

# ═══════════════════════════════════════════════════════════════════════
# SELF-REVIEW (7) — Phase C, opt-in via --subsystem self_review
# ═══════════════════════════════════════════════════════════════════════
_add(
    Scenario(
        id="review_001", name="Review own purpose", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "What is your purpose?"}],
        responses={"your purpose": "I'm a Discord AI assistant for chat, moderation, and server management."},
        expected={"response_contains": ["Discord", "assistant"]}),
    Scenario(
        id="review_002", name="Identify bug in code snippet", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Find the bug: if x = 5: print('five')"}],
        responses={"find the bug": "The bug is using '=' instead of '==' for comparison. Use 'if x == 5:'"},
        expected={"response_contains": ["=", "=="]}),
    Scenario(
        id="review_003", name="Suggest optimization", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Optimize this: for i in range(len(list)): print(list[i])"}],
        responses={"optimize": "Use direct iteration: `for item in list: print(item)` — simpler and faster!"},
        expected={"response_contains": ["for", "in"]}),
    Scenario(
        id="review_004", name="Explain code architecture", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "How does the pipeline architecture work?"}],
        responses={"pipeline architecture": "Messages flow through: rate limiting → moderation → attention → agent → response."},
        expected={"response_contains": ["rate", "moderation", "agent"]}),
    Scenario(
        id="review_005", name="Write a unit test", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Write a test for the handle function"}],
        responses={"test for the handle": "Here's a test: `async def test_handle(): result = await agent.handle(...); assert result is not None`"},
        expected={"response_contains": ["async", "test", "handle"]}),
    Scenario(
        id="review_006", name="Refactor suggestion", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Refactor this long function into smaller pieces"}],
        responses={"refactor": "Break the function into: validation, processing, and response stages."},
        expected={"response_contains": ["validation", "processing"]}),
    Scenario(
        id="review_007", name="Security review", subsystem="self_review",
        users=[{"name": "Bot Bob", "id": 1004}],
        messages=[{"user": "bob", "text": "Review this code for security issues"}],
        responses={"security issues": "Check for: SQL injection, XSS, input validation, and rate limiting."},
        expected={"response_contains": ["injection", "input", "rate"]}),
)
