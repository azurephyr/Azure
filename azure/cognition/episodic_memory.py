import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class EpisodicMemory:
    """
    Upgrade 15: Episodic Memory with Summaries
    Maintains a log of past conversational episodes (summaries of blocks of 20 messages)
    to keep context windows clean while maintaining long-term conversational awareness.
    """
    def __init__(self, path: Path, llm=None):
        self.path = Path(path)
        self.llm = llm
        self.episodes = []
        self.message_counter = 0
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.episodes = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load episodic memory: {e}")
                self.episodes = []

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.episodes, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"Failed to save episodic memory: {e}")

    def add_message(self, role: str, name: str, content: str, history: list[dict]):
        self.message_counter += 1
        if self.message_counter >= 20:
            self.message_counter = 0
            self.summarize_and_store(history[-20:])

    def summarize_and_store(self, messages_block: list[dict]):
        if not self.llm or not messages_block:
            return

        chat_history_str = "\n".join([
            f"<{m.get('name') or m['role']}> {m['content']}"
            for m in messages_block
        ])

        prompt = (
            "Summarize the following Discord conversation block in exactly 2 or 3 sentences. "
            "Focus on the main topics, requests made, and any decisions resolved.\n\n"
            f"Conversation:\n{chat_history_str}\n\n"
            "Summary:"
        )

        try:
            summary = self.llm.chat([
                {"role": "system", "content": "You are a helpful AI summarizer."},
                {"role": "user", "content": prompt}
            ], max_tokens=150, temperature=0.3)

            episode = {
                "timestamp": time.time(),
                "summary": summary.strip(),
                "participants": list(set(m.get("name") for m in messages_block if m.get("name")))
            }
            self.episodes.append(episode)
            self._save()
            logger.info(f"Stored episodic memory: {episode['summary']}")
        except Exception as e:
            logger.error(f"Failed to generate conversation summary: {e}")

    def get_recent_episodes(self, limit: int = 5) -> list[str]:
        return [ep["summary"] for ep in self.episodes[-limit:]]
