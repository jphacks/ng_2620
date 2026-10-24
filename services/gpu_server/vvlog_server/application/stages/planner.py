"""Which stages each job type runs. The real stages are added one by one (ingest, track+mesh, occlusion, ...)."""
from __future__ import annotations

from .base import Stage


class Planner:
    def __init__(self, table: dict[str, list[Stage]]):
        self.table = table

    def stages(self, job_type: str) -> list[Stage]:
        return list(self.table.get(job_type, []))


def planner_for(settings) -> Planner:
    return Planner({"analyze": [], "render": [], "export": []})
