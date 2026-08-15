from __future__ import annotations
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from core.skills import SkillManager


@dataclass
class SkillActivation:
    """Result of skill activation check."""
    should_activate: bool
    skill_name: str
    reason: str


class SkillEngine:
    """
    Skill loading, trigger matching, and activation engine.
    
    Separated from SkillManager to keep Agent focused on orchestration.
    """
    
    def __init__(self, search_dirs: list[str] | None = None):
        self.manager = SkillManager(*(search_dirs or []))
        self._active_skills: set[str] = set()
    
    def load_skills(self) -> list[str]:
        """Load all available skills. Returns list of skill names."""
        return self.manager.list_skills()
    
    def get_skill(self, name: str):
        """Get a skill by name."""
        return self.manager.get(name)
    
    def check_triggers(self, user_input: str) -> list[SkillActivation]:
        """
        Check if any skill triggers match the user input.
        Returns list of skills that should be suggested/activated.
        """
        return self.manager.check_triggers(user_input)
    
    def activate(self, skill_name: str) -> bool:
        """Activate a skill for the current session."""
        skill = self.manager.get(skill_name)
        if skill:
            self._active_skills.add(skill_name)
            return True
        return False
    
    def deactivate(self, skill_name: str) -> bool:
        """Deactivate a skill."""
        return self._active_skills.discard(skill_name)
    
    def get_active(self) -> set[str]:
        """Get currently active skills."""
        return self._active_skills.copy()
    
    def get_system_prompt_additions(self) -> str:
        """Get system prompt additions for active skills."""
        if not self._active_skills:
            return ""
        
        parts = []
        for name in sorted(self._active_skills):
            skill = self.manager.get(name)
            if skill:
                parts.append(f"\n\n## Loaded Skill: {skill.name}\n{skill.instructions}")
        
        return "".join(parts)
    
    def suggest_skills(self, user_input: str) -> list[str]:
        """Get skill suggestions for the current input (for UI)."""
        activations = self.check_triggers(user_input)
        return [a.skill_name for a in activations if a.should_activate]