"""Device-associated first-meeting records, stored only on this host."""
import json
import time
from pathlib import Path
from .config import save_json

class MeetingMemory:
    def __init__(self, path: Path):
        self.path = path
        self.people = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, device_id):
        return self.people.get(device_id)

    def complete(self, device_id, name, photo, rehearsal):
        if rehearsal:
            return
        if not device_id:
            raise ValueError("尚未取得稳定设备身份，无法记录初见完成")
        candidate = {**self.people, device_id: {"name": name, "photo": photo, "completed_at": time.time()}}
        save_json(self.path, candidate)
        self.people = candidate
