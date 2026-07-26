from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Task:
    id: str
    agent: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    result: str = ""
    status: str = "pending"


class Orchestrator:
    def __init__(self, subagent_manager):
        self._subagents = subagent_manager
        self._tasks: dict[str, Task] = {}

    async def execute(self, plan: str) -> dict[str, Task]:
        tasks = self._parse_plan(plan)
        self._tasks = {t.id: t for t in tasks}
        completed: dict[str, str] = {}

        while True:
            ready = [t for t in tasks if t.status == "pending"
                     and all(d in completed for d in t.depends_on)]
            if not ready:
                break

            async def _run_one(task: Task) -> tuple[str, str, str, str]:
                task.status = "running"
                enriched = task.prompt
                for dep_id in task.depends_on:
                    if dep_id in completed:
                        enriched += f"\n\nPrevious result ({dep_id}):\n{completed[dep_id]}"
                try:
                    result = await self._subagents.run(task.agent, enriched)
                    return task.id, result or "(no output)", "done", ""
                except Exception as e:
                    return task.id, f"Error: {e}", "failed", ""

            results = await asyncio.gather(*[_run_one(t) for t in ready])
            for tid, result, status, _ in results:
                task = self._tasks[tid]
                task.result = result
                task.status = status
                completed[tid] = result

        return dict(self._tasks)

    def _parse_plan(self, plan: str) -> list[Task]:
        tasks: list[Task] = []
        current: Optional[Task] = None
        for line in plan.strip().split("\n"):
            line = line.strip()
            if line.startswith("### "):
                if current:
                    tasks.append(current)
                name = line[4:].strip()
                current = Task(id=name, agent="general", prompt="")
            elif line.startswith("- agent:"):
                if current:
                    current.agent = line.split(":", 1)[1].strip()
            elif line.startswith("- depends:"):
                if current:
                    deps = line.split(":", 1)[1].strip()
                    current.depends_on = [d.strip() for d in deps.split(",") if d.strip()]
            elif line.startswith("- prompt:"):
                if current:
                    current.prompt = line.split(":", 1)[1].strip()
            elif current and line and not line.startswith("-"):
                current.prompt += " " + line
        if current:
            tasks.append(current)
        return tasks

    def status_report(self) -> str:
        if not self._tasks:
            return "No tasks executed."
        lines = ["## Orchestration Results"]
        for t in self._tasks.values():
            icon = {"done": "✓", "failed": "✗", "running": "…", "pending": "○"}.get(t.status, "?")
            lines.append(f"{icon} **{t.id}** ({t.agent}): {t.status}")
            if t.result:
                preview = t.result[:100].replace("\n", " ")
                lines.append(f"  {preview}…")
        return "\n".join(lines)
