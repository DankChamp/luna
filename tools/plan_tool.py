from __future__ import annotations
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone
from dataclasses import dataclass, field

from tools.registry import ToolDef, ToolRegistry
from core.errors import ToolError

import anyio


@dataclass
class PlanData:
    """Plan data structure."""
    title: str
    description: str
    steps: list[dict]
    created_at: str
    updated_at: str
    status: str = "active"  # active, completed, abandoned


class PlanManager:
    """Manages plan files in .luna/plans/ directory."""

    def __init__(self, plans_dir: Path | None = None):
        self.plans_dir = plans_dir or Path.home() / ".luna" / "plans"
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        self._current_plan: Optional[PlanData] = None
        self._current_plan_path: Optional[Path] = None

    def _get_plan_path(self, title: str) -> Path:
        """Get plan file path from title."""
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title.lower())
        return self.plans_dir / f"{safe_title}.md"

    def create_plan(
        self,
        title: str,
        description: str = "",
        steps: list[dict] | None = None,
    ) -> PlanData:
        """Create a new plan."""
        now = datetime.now(timezone.utc).isoformat()
        plan = PlanData(
            title=title,
            description=description,
            steps=steps or [],
            created_at=now,
            updated_at=now,
            status="active",
        )
        self._current_plan = plan
        self._current_plan_path = self._get_plan_path(title)
        self._save_plan()
        return plan

    def load_plan(self, title: str) -> Optional[PlanData]:
        """Load a plan by title."""
        path = self._get_plan_path(title)
        if not path.exists():
            return None

        content = path.read_text()
        plan = self._parse_plan_file(content, title)
        self._current_plan = plan
        self._current_plan_path = path
        return plan

    def list_plans(self) -> list[dict]:
        """List all plans."""
        plans = []
        for path in sorted(self.plans_dir.glob("*.md")):
            try:
                content = path.read_text()
                plan = self._parse_plan_file(content, path.stem)
                plans.append({
                    "title": plan.title,
                    "description": plan.description[:100] if plan.description else "",
                    "steps": len(plan.steps),
                    "status": plan.status,
                    "updated": plan.updated_at,
                })
            except Exception:
                continue
        return sorted(plans, key=lambda p: p["updated"], reverse=True)

    def _parse_plan_file(self, content: str, title: str) -> PlanData:
        """Parse plan markdown file."""
        lines = content.split("\n")
        description = ""
        steps = []
        status = "active"
        created_at = datetime.now(timezone.utc).isoformat()
        updated_at = created_at

        in_description = False
        in_steps = False

        for line in lines:
            if line.startswith("# "):
                continue
            elif line.startswith("## Description"):
                in_description = True
                in_steps = False
            elif line.startswith("## Steps"):
                in_steps = True
                in_description = False
            elif line.startswith("## Status"):
                in_description = False
                in_steps = False
            elif line.startswith("## Created"):
                in_description = False
                in_steps = False
            elif line.startswith("## Updated"):
                in_description = False
                in_steps = False
            elif line.startswith("- [ ]") or line.startswith("- [x]"):
                if in_steps:
                    done = line.startswith("- [x]")
                    step_text = line[5:].strip()
                    steps.append({"done": done, "text": step_text})
            elif in_description:
                description += line + "\n"
            elif line.startswith("Status: "):
                status = line[8:].strip()
            elif line.startswith("Created: "):
                created_at = line[9:].strip()
            elif line.startswith("Updated: "):
                updated_at = line[9:].strip()

        return PlanData(
            title=title,
            description=description.strip(),
            steps=steps,
            created_at=created_at,
            updated_at=updated_at,
            status=status,
        )

    def _save_plan(self) -> None:
        if not self._current_plan or not self._current_plan_path:
            return

        plan = self._current_plan
        plan.updated_at = datetime.now(timezone.utc).isoformat()

        lines = [
            f"# {plan.title}",
            "",
            "## Description",
            plan.description or "",
            "",
            "## Steps",
        ]

        for i, step in enumerate(plan.steps):
            checkbox = "- [x]" if step.get("done") else "- [ ]"
            lines.append(f"{checkbox} {step.get('text', '')}")

        lines.extend([
            "",
            "## Status",
            plan.status,
            "",
            "## Created",
            plan.created_at,
            "",
            "## Updated",
            plan.updated_at,
        ])

        self._current_plan_path.write_text("\n".join(lines))

    def add_step(self, text: str) -> None:
        if self._current_plan:
            self._current_plan.steps.append({"done": False, "text": text})
            self._save_plan()

    def complete_step(self, index: int) -> bool:
        if self._current_plan and 0 <= index < len(self._current_plan.steps):
            self._current_plan.steps[index]["done"] = True
            self._save_plan()
            return True
        return False

    def uncomplete_step(self, index: int) -> bool:
        if self._current_plan and 0 <= index < len(self._current_plan.steps):
            self._current_plan.steps[index]["done"] = False
            self._save_plan()
            return True
        return False

    def remove_step(self, index: int) -> bool:
        if self._current_plan and 0 <= index < len(self._current_plan.steps):
            self._current_plan.steps.pop(index)
            self._save_plan()
            return True
        return False

    def get_current_plan(self) -> Optional[PlanData]:
        return self._current_plan

    def close_plan(self, status: str = "completed") -> None:
        if self._current_plan:
            self._current_plan.status = status
            self._save_plan()
            self._current_plan = None
            self._current_plan_path = None


def create_plan_enter_tool(plans_dir: Path | None = None) -> ToolDef:
    """Create the plan_enter tool for starting plan mode."""

    plan_manager = PlanManager(plans_dir)

    async def plan_enter(
        title: str,
        description: str = "",
        steps: list[str] | None = None,
    ) -> str:
        """
        Enter plan mode by creating a new plan.
        
        Args:
            title: Plan title (used as filename)
            description: Plan description
            steps: Optional initial steps
            
        Returns:
            Confirmation message with plan details
        """
        step_dicts = [{"done": False, "text": s} for s in (steps or [])]
        plan = plan_manager.create_plan(title, description, step_dicts)
        
        return f"Plan created: {plan.title}\nStatus: {plan.status}\nSteps: {len(plan.steps)}"

    return ToolDef(
        name="plan_enter",
        description="Enter plan mode by creating a new plan. Plans are saved as markdown files in .luna/plans/.",
        parameters={
            "title": {
                "type": "string",
                "description": "Plan title (used as filename)",
            },
            "description": {
                "type": "string",
                "description": "Plan description",
                "default": "",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Initial steps for the plan",
            },
        },
        required=["title"],
        handler=plan_enter,
    )


def create_plan_exit_tool(plans_dir: Path | None = None) -> ToolDef:
    """Create the plan_exit tool for exiting plan mode."""

    plan_manager = PlanManager(plans_dir)

    async def plan_exit(
        status: str = "completed",
    ) -> str:
        """
        Exit plan mode and save the plan.
        
        Args:
            status: Final status - 'completed', 'abandoned', or 'active'
            
        Returns:
            Confirmation message
        """
        plan_manager.close_plan(status)
        return f"Plan exited with status: {status}"

    return ToolDef(
        name="plan_exit",
        description="Exit plan mode and save the plan with final status.",
        parameters={
            "status": {
                "type": "string",
                "description": "Final plan status",
                "enum": ["completed", "abandoned", "active"],
                "default": "completed",
            },
        },
        required=[],
        handler=plan_exit,
    )


def create_plan_tools(plans_dir: Path | None = None) -> list[ToolDef]:
    """Create all plan-related tools."""
    return [
        create_plan_enter_tool(plans_dir),
        create_plan_exit_tool(plans_dir),
    ]


def create_plan_manage_tool(plans_dir: Path | None = None) -> ToolDef:
    """Create tool for managing plans (list, load, delete)."""

    plan_manager = PlanManager(plans_dir)

    async def plan_manage(
        action: str,
        title: str | None = None,
    ) -> str:
        """
        Manage plans.
        
        Args:
            action: 'list', 'load', 'delete', 'show'
            title: Plan title (required for load, delete, show)
        """
        if action == "list":
            plans = plan_manager.list_plans()
            if not plans:
                return "No plans found."
            
            result = ["Plans:"]
            for p in plans:
                status_icon = "✓" if p["status"] == "completed" else "○"
                result.append(f"  {status_icon} {p['title']} ({p['steps']} steps) - {p['status']} - {p['updated'][:10]}")
            return "\n".join(result)

        elif action == "load":
            if not title:
                raise ToolError("Title required for load action", "plan_manage")
            plan = plan_manager.load_plan(title)
            if not plan:
                return f"Plan not found: {title}"
            return f"Loaded plan: {plan.title}\nStatus: {plan.status}\nSteps: {len(plan.steps)}"

        elif action == "delete":
            if not title:
                raise ToolError("Title required for delete action", "plan_manage")
            path = plan_manager.plans_dir / f"{title.lower().replace(' ', '_')}.md"
            if path.exists():
                path.unlink()
                return f"Deleted plan: {title}"
            return f"Plan not found: {title}"

        elif action == "show":
            if not title:
                raise ToolError("Title required for show action", "plan_manage")
            plan = plan_manager.load_plan(title)
            if not plan:
                return f"Plan not found: {title}"
            
            lines = [f"# {plan.title}", f"Status: {plan.status}"]
            if plan.description:
                lines.extend(["", "Description:", plan.description])
            if plan.steps:
                lines.extend(["", "Steps:"])
                for i, step in enumerate(plan.steps):
                    status = "✓" if step.get("done") else "○"
                    lines.append(f"  {i+1}. [{status}] {step['text']}")
            return "\n".join(lines)

        else:
            raise ToolError(f"Unknown action: {action}", "plan_manage")

    return ToolDef(
        name="plan",
        description="Manage plans: list, load, show, or delete plans",
        parameters={
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": ["list", "load", "delete", "show"],
            },
            "title": {
                "type": "string",
                "description": "Plan title (required for load, delete, show)",
            },
        },
        required=["action"],
        handler=plan_manage,
    )


def create_plan_step_tool(plans_dir: Path | None = None) -> ToolDef:
    """Create tool for managing plan steps."""

    plan_manager = PlanManager(plans_dir)

    async def plan_step(
        action: str,
        text: str | None = None,
        index: int | None = None,
    ) -> str:
        """
        Manage plan steps.
        
        Args:
            action: 'add', 'complete', 'uncomplete', 'remove', 'list'
            text: Step text (for 'add')
            index: Step index (for 'complete', 'uncomplete', 'remove')
        """
        if not plan_manager._current_plan:
            raise ToolError("No active plan. Use plan_enter first.", "plan_step")

        if action == "add":
            if not text:
                raise ToolError("Text required for add action", "plan_step")
            plan_manager.add_step(text)
            return f"Added step: {text}"

        elif action == "complete":
            if index is None:
                raise ToolError("Index required for complete action", "plan_step")
            if plan_manager.complete_step(index):
                return f"Completed step {index + 1}"
            return "Invalid step index"

        elif action == "uncomplete":
            if index is None:
                raise ToolError("Index required for uncomplete action", "plan_step")
            if plan_manager.uncomplete_step(index):
                return f"Uncompleted step {index + 1}"
            return "Invalid step index"

        elif action == "remove":
            if index is None:
                raise ToolError("Index required for remove action", "plan_step")
            if plan_manager.remove_step(index):
                return f"Removed step {index + 1}"
            return "Invalid step index"

        elif action == "list":
            plan = plan_manager.get_current_plan()
            if not plan or not plan.steps:
                return "No steps in current plan."
            
            lines = ["Current plan steps:"]
            for i, step in enumerate(plan.steps):
                status = "✓" if step.get("done") else "○"
                lines.append(f"  {i+1}. [{status}] {step['text']}")
            return "\n".join(lines)

        else:
            raise ToolError(f"Unknown action: {action}", "plan_step")

    return ToolDef(
        name="plan_step",
        description="Manage steps in the current plan",
        parameters={
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": ["add", "complete", "uncomplete", "remove", "list"],
            },
            "text": {
                "type": "string",
                "description": "Step text (for add action)",
            },
            "index": {
                "type": "integer",
                "description": "Step index (0-based, for complete/uncomplete/remove)",
            },
        },
        required=["action"],
        handler=plan_step,
    )