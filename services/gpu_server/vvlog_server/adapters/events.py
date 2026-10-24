"""Job events appended to jobs/<jobId>/events.jsonl, with live delivery to SSE subscribers (contracts §3.3)."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import AsyncIterator

from ..domain.job import now_iso

TERMINAL_EVENTS = ("job_finished", "job_failed")


class JsonlEventBus:
    def __init__(self, root: Path):
        self.root = root / "jobs"
        self._lock = threading.Lock()
        self._last: dict[str, int] = {}
        self._waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    def _path(self, job_id: str) -> Path:
        return self.root / job_id / "events.jsonl"

    def _last_id(self, job_id: str) -> int:
        if job_id not in self._last:
            h = self.history(job_id)
            self._last[job_id] = h[-1]["id"] if h else 0
        return self._last[job_id]

    def publish(self, job_id: str, event: str, data: dict) -> int:
        with self._lock:
            eid = self._last_id(job_id) + 1
            self._last[job_id] = eid
            p = self._path(job_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": eid, "event": event, "data": data, "ts": now_iso()}, ensure_ascii=False)
                        + "\n")
            waiters = self._waiters.get(job_id, [])
        for loop, ev in waiters:
            loop.call_soon_threadsafe(ev.set)
        return eid

    def history(self, job_id: str, after_id: int = 0) -> list[dict]:
        p = self._path(job_id)
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                if e["id"] > after_id:
                    out.append(e)
        return out

    async def subscribe(self, job_id: str, after_id: int = 0) -> AsyncIterator[dict]:
        """Events after after_id, then live ones, until the job's terminal event has been delivered."""
        ev = asyncio.Event()
        entry = (asyncio.get_running_loop(), ev)
        with self._lock:
            self._waiters.setdefault(job_id, []).append(entry)
        try:
            last = after_id
            while True:
                ev.clear()
                for e in self.history(job_id, last):
                    last = e["id"]
                    yield e
                    if e["event"] in TERMINAL_EVENTS:
                        return
                await ev.wait()
        finally:
            with self._lock:
                self._waiters[job_id].remove(entry)
