import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class UserProfileManager:
    """
    Upgrade 11: Long-Term User Profiles
    Manages persistent profiles for server members, storing communication
    style preferences, facts, past requests, and activity metadata.
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.profiles = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.profiles = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load user profiles: {e}")
                self.profiles = {}

    def _save(self):
        try:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.profiles, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"Failed to save user profiles: {e}")

    def get_profile(self, user_name: str) -> dict:
        if user_name not in self.profiles:
            self.profiles[user_name] = {
                "communication_style": "neutral",
                "facts": [],
                "topics_of_interest": [],
                "activity_level": 0,
                "last_seen": time.time(),
                "past_requests": []
            }
        return self.profiles[user_name]

    def record_interaction(self, user_name: str, message: str):
        profile = self.get_profile(user_name)
        profile["activity_level"] += 1
        profile["last_seen"] = time.time()

        # Maintain history of recent requests
        if "past_requests" not in profile:
            profile["past_requests"] = []
        profile["past_requests"].append(message)
        if len(profile["past_requests"]) > 10:
            profile["past_requests"].pop(0)

        self._save()

    def add_fact(self, user_name: str, fact: str):
        profile = self.get_profile(user_name)
        if "facts" not in profile:
            profile["facts"] = []
        if fact not in profile["facts"]:
            profile["facts"].append(fact)
            self._save()

    def set_preference(self, user_name: str, key: str, value: str):
        profile = self.get_profile(user_name)
        profile[key] = value
        self._save()
