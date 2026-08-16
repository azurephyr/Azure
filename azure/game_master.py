"""
Azure Game Master Mode

Text-based RPG adventures, trivia nights, murder mysteries, and escape rooms.
Persistent state stored in the memory backend. Multiple concurrent games supported.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GameSession:
    """A single game session."""
    game_id: str
    game_type: str  # "rpg", "trivia", "mystery", "escape"
    channel_id: str
    state: dict[str, Any] = field(default_factory=dict)
    players: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_activity: float = 0.0
    active: bool = True


class GameMaster:
    """
    Game master for Discord channels.

    Usage:
        gm = GameMaster()
        session = gm.start_game("#general", "rpg", ["Alice", "Bob"])
        response = gm.process_input(session.game_id, "look around")
    """

    def __init__(self):
        self.sessions: dict[str, GameSession] = {}

    # ------------------------------------------------------------------
    # Game lifecycle
    # ------------------------------------------------------------------

    def start_game(self, channel_id: str, game_type: str, players: list[str]) -> GameSession:
        """Start a new game in a channel."""
        game_id = f"{channel_id}_{game_type}_{int(time.time())}"
        session = GameSession(
            game_id=game_id,
            game_type=game_type,
            channel_id=channel_id,
            players=players,
            created_at=time.time(),
            last_activity=time.time(),
        )

        # Initialize game state
        if game_type == "rpg":
            session.state = self._init_rpg(players)
        elif game_type == "trivia":
            session.state = self._init_trivia(players)
        elif game_type == "mystery":
            session.state = self._init_mystery(players)
        elif game_type == "escape":
            session.state = self._init_escape(players)

        self.sessions[game_id] = session
        return session

    def end_game(self, game_id: str) -> str:
        """End a game session."""
        session = self.sessions.pop(game_id, None)
        if not session:
            return "No active game found."
        return f"🎮 **Game Over** — {session.game_type.title()} session ended. Thanks for playing!"

    def get_active_game(self, channel_id: str) -> GameSession | None:
        """Get the active game for a channel."""
        for session in self.sessions.values():
            if session.channel_id == channel_id and session.active:
                return session
        return None

    # ------------------------------------------------------------------
    # Input processing
    # ------------------------------------------------------------------

    def process_input(self, game_id: str, player_input: str, player_name: str = "") -> str:
        """Process player input and return response."""
        session = self.sessions.get(game_id)
        if not session:
            return "No active game. Start one with `!azure_rpg`, `!azure_trivia`, etc."

        session.last_activity = time.time()

        if session.game_type == "rpg":
            return self._process_rpg(session, player_input, player_name)
        elif session.game_type == "trivia":
            return self._process_trivia(session, player_input, player_name)
        elif session.game_type == "mystery":
            return self._process_mystery(session, player_input, player_name)
        elif session.game_type == "escape":
            return self._process_escape(session, player_input, player_name)

        return "Unknown game type."

    # ------------------------------------------------------------------
    # RPG
    # ------------------------------------------------------------------

    def _init_rpg(self, players: list[str]) -> dict:
        return {
            "location": "Tavern",
            "inventory": {p: ["sword", "potion"] for p in players},
            "health": {p: 100 for p in players},
            "story": "You find yourselves in a dimly lit tavern. A hooded figure watches from the corner.",
            "history": [],
        }

    def _process_rpg(self, session: GameSession, cmd: str, player: str) -> str:
        state = session.state
        cmd_lower = cmd.lower().strip()

        if cmd_lower in ["look", "look around", "examine"]:
            return f"🏰 **{state['location']}**\n{state['story']}"

        if cmd_lower in ["inventory", "inv", "items"]:
            inv = state["inventory"].get(player, [])
            return f"🎒 **Inventory:** {', '.join(inv) if inv else 'Empty'}"

        if cmd_lower in ["health", "hp", "status"]:
            hp = state["health"].get(player, 0)
            return f"❤️ **Health:** {hp}/100"

        if cmd_lower.startswith("go ") or cmd_lower.startswith("move "):
            dest = cmd_lower.split(" ", 1)[1] if " " in cmd_lower else ""
            locations = {
                "forest": "The ancient forest looms before you. Strange lights flicker between the trees.",
                "cave": "A dark cave entrance. The air is cold and smells of sulfur.",
                "village": "A bustling village. Merchants hawk their wares and children play in the streets.",
                "castle": "The castle gates stand tall. Guards eye you suspiciously.",
            }
            for key, desc in locations.items():
                if key in dest:
                    state["location"] = key.title()
                    state["story"] = desc
                    return f"🚶 You travel to the **{state['location']}**.\n\n{desc}"
            return f"You can't go to '{dest}'. Try: forest, cave, village, castle."

        # Generic narrative response
        responses = [
            f"You {cmd_lower}. The world around you shifts subtly.",
            f"Interesting approach. {state['story'][:50]}...",
            f"The DM considers your action... '{cmd_lower}' — noted.",
        ]
        state["history"].append(f"{player}: {cmd}")
        return random.choice(responses)

    # ------------------------------------------------------------------
    # Trivia
    # ------------------------------------------------------------------

    TRIVIA_QUESTIONS = [
        {"q": "What is the capital of France?", "a": "paris", "category": "Geography"},
        {"q": "In Python, what does 'len()' do?", "a": "returns length", "category": "Programming"},
        {"q": "What year did the first moon landing happen?", "a": "1969", "category": "History"},
        {"q": "What is the largest planet in our solar system?", "a": "jupiter", "category": "Science"},
        {"q": "Who wrote 'Romeo and Juliet'?", "a": "shakespeare", "category": "Literature"},
        {"q": "What does CPU stand for?", "a": "central processing unit", "category": "Technology"},
        {"q": "What is the chemical symbol for gold?", "a": "au", "category": "Science"},
        {"q": "How many bits are in a byte?", "a": "8", "category": "Technology"},
    ]

    def _init_trivia(self, players: list[str]) -> dict:
        questions = random.sample(self.TRIVIA_QUESTIONS, min(5, len(self.TRIVIA_QUESTIONS)))
        return {
            "questions": questions,
            "current_index": 0,
            "scores": {p: 0 for p in players},
            "active": True,
        }

    def _process_trivia(self, session: GameSession, answer: str, player: str) -> str:
        state = session.state
        if not state["active"]:
            return "Trivia game has ended."

        idx = state["current_index"]
        if idx >= len(state["questions"]):
            # Game over
            winner = max(state["scores"], key=state["scores"].get)
            state["active"] = False
            lines = ["🎉 **Trivia Complete!**"]
            for p, score in sorted(state["scores"].items(), key=lambda x: -x[1]):
                lines.append(f"{p}: {score} points")
            lines.append(f"\n🏆 Winner: {winner}")
            return "\n".join(lines)

        question = state["questions"][idx]
        norm_answer = answer.lower().strip()
        norm_correct = question["a"].lower().strip()
        correct_words = set(norm_correct.split())
        user_words = set(norm_answer.split())
        correct = norm_answer == norm_correct or (len(correct_words) > 1 and correct_words.issubset(user_words))

        if correct:
            state["scores"][player] = state["scores"].get(player, 0) + 1
            result = "✅ Correct!"
        else:
            result = f"❌ Wrong! The answer was: **{question['a']}**"

        state["current_index"] += 1
        next_idx = state["current_index"]

        if next_idx < len(state["questions"]):
            next_q = state["questions"][next_idx]
            return f"{result}\n\n**Question {next_idx + 1}/{len(state['questions'])}** ({next_q['category']})\n{next_q['q']}"
        else:
            return result + "\n\nType `next` to see final scores."

    # ------------------------------------------------------------------
    # Murder Mystery
    # ------------------------------------------------------------------

    def _init_mystery(self, players: list[str]) -> dict:
        suspects = ["The Butler", "The Chef", "The Gardener", "The Guest"]
        killer = random.choice(suspects)
        return {
            "victim": "Lord Blackwood",
            "killer": killer,
            "suspects": suspects,
            "clues_given": 0,
            "accusations": {},
            "solved": False,
        }

    def _process_mystery(self, session: GameSession, cmd: str, player: str) -> str:
        state = session.state
        cmd_lower = cmd.lower().strip()

        if cmd_lower in ["clue", "hint", "investigate"]:
            state["clues_given"] += 1
            clues = [
                f"A broken wine glass was found near the body. The victim was **{state['victim']}**.",
                "The butler claims he was polishing silver in the pantry.",
                "The chef was heard arguing with the victim about the menu earlier.",
                "The gardener's boots had fresh mud — but it hasn't rained in days.",
                "A guest was seen leaving the study shortly before the scream.",
            ]
            idx = min(state["clues_given"] - 1, len(clues) - 1)
            return f"🔍 **Clue #{state['clues_given']}:** {clues[idx]}"

        if cmd_lower.startswith("accuse "):
            accused = cmd_lower.split(" ", 1)[1] if " " in cmd_lower else ""
            state["accusations"][player] = accused
            if accused.lower() in state["killer"].lower():
                state["solved"] = True
                return f"🎉 **{player} solved the mystery!** The killer was indeed **{state['killer']}**!"
            else:
                return f"❌ {player} accused **{accused}**, but that doesn't seem right... Keep investigating!"

        if cmd_lower in ["suspects", "who"]:
            return f"🕵️ **Suspects:** {', '.join(state['suspects'])}\nUse `accuse <name>` to make your accusation."

        return f"🕵️ **Mystery of {state['victim']}**\nInvestigate with `clue`, `suspects`, or `accuse <name>`."

    # ------------------------------------------------------------------
    # Escape Room
    # ------------------------------------------------------------------

    def _init_escape(self, players: list[str]) -> dict:
        return {
            "room": "The Locked Library",
            "stage": 0,
            "puzzles": [
                {"question": "I have cities but no houses, forests but no trees. What am I?", "answer": "map", "hint": "You use it to navigate."},
                {"question": "What has keys but no locks, space but no room?", "answer": "keyboard", "hint": "You type on it."},
                {"question": "The code is the sum of the first 3 prime numbers.", "answer": "10", "hint": "2 + 3 + 5 = ?"},
            ],
        }

    def _process_escape(self, session: GameSession, cmd: str, player: str) -> str:
        state = session.state
        cmd_lower = cmd.lower().strip()
        stage = state["stage"]

        if stage >= len(state["puzzles"]):
            return "🎉 **Escape Complete!** You've solved all puzzles and escaped!"

        puzzle = state["puzzles"][stage]

        if cmd_lower == "hint":
            return f"💡 **Hint:** {puzzle['hint']}"

        if cmd_lower == puzzle["answer"].lower():
            state["stage"] += 1
            if state["stage"] >= len(state["puzzles"]):
                return f"🎉 **Correct!** That's the final puzzle! You've escaped **{state['room']}**!"
            next_p = state["puzzles"][state["stage"]]
            return f"✅ **Correct!** Door unlocked.\n\n**Puzzle {state['stage'] + 1}:** {next_p['question']}"

        if cmd_lower in ["look", "status", "room"]:
            return f"🔒 **{state['room']}** — Puzzle {stage + 1}/{len(state['puzzles'])}\n{puzzle['question']}"

        return f"❌ Not quite. Try `hint` or `look`.\n\n**Puzzle {stage + 1}:** {puzzle['question']}"

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "active_games": len(self.sessions),
            "by_type": {
                "rpg": sum(1 for s in self.sessions.values() if s.game_type == "rpg"),
                "trivia": sum(1 for s in self.sessions.values() if s.game_type == "trivia"),
                "mystery": sum(1 for s in self.sessions.values() if s.game_type == "mystery"),
                "escape": sum(1 for s in self.sessions.values() if s.game_type == "escape"),
            }
        }
