import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class ServerKnowledgeBase:
    """
    Upgrade 13: Server Knowledge Base
    Caches server metadata (channels, roles, rules) persistently and dynamically
    updates on changes. Injected into LLM context for real server grounding.
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.knowledge = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.knowledge = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load server knowledge: {e}")
                self.knowledge = {}

    def _save(self):
        try:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.knowledge, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"Failed to save server knowledge: {e}")

    def update_server_state(self, server_name: str, channels: list[dict], roles: list[dict], member_count: int):
        self.knowledge[server_name] = {
            "channels": channels,  # list of {"name": name, "id": id, "type": type, "purpose": purpose}
            "roles": roles,        # list of {"name": name, "id": id, "position": pos}
            "member_count": member_count,
            "rules": self.knowledge.get(server_name, {}).get("rules", []),
            "key_members": self.knowledge.get(server_name, {}).get("key_members", [])
        }
        self._save()

    def get_summary(self, server_name: str) -> str:
        data = self.knowledge.get(server_name)
        if not data:
            return ""

        channels_str = ", ".join([f"#{c['name']}" for c in data.get("channels", [])[:10]])
        roles_str = ", ".join([f"@{r['name']}" for r in data.get("roles", [])[:5]])

        return (
            f"Server: {server_name}\n"
            f"Member count: {data.get('member_count', 0)}\n"
            f"Key Channels: {channels_str}\n"
            f"Key Roles: {roles_str}\n"
        )
