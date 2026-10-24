"""In-process FIFO of queued job ids (one per server; the worker picks the first runnable one)."""
from __future__ import annotations

import threading


class InProcessJobQueue:
    def __init__(self):
        self._items: list[str] = []
        self._lock = threading.Lock()

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            if job_id not in self._items:
                self._items.append(job_id)

    def position(self, job_id: str) -> int | None:
        with self._lock:
            return self._items.index(job_id) if job_id in self._items else None

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._items:
                self._items.remove(job_id)
                return True
            return False

    def items(self) -> list[str]:
        with self._lock:
            return list(self._items)
