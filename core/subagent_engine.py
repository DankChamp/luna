from __future__ import annotations
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from core.subagents import SubagentManager
from core.providers.base import AIProvider
from core.router import AIRouter


@dataclass
class SubagentResult:
    """Result of subagent execution."""
    success: bool
    output: str
    error: str | None = None


class SubagentEngine:
    """
    Subagent loading, isolation, and execution engine.
    
    Separated from SubagentManager for cleaner Agent architecture.
    """
    
    def __init__(self, router: AIRouter, search_dirs: list[str] | None = None):
        self.manager = SubagentManager(router, *(search_dirs or []))
        self._model_overrides: dict[str, str] = {}
    
    def load_subagents(self) -> list[str]:
        """Load all available subagents. Returns list of subagent names."""
        return self.manager.list_subagents()
    
    def get_subagent(self, name: str):
        """Get a subagent by name."""
        return self.manager.get(name)
    
    def set_model_override(self, subagent_name: str, model: str):
        """Set model override for a specific subagent."""
        self._model_overrides[subagent_name] = model
    
    def get_model_override(self, subagent_name: str) -> Optional[str]:
        """Get model override for a subagent."""
        return self._model_overrides.get(subagent_name)
    
    async def run(
        self,
        name: str,
        prompt: str,
        provider: AIProvider,
        tools_subset: list[str] | None = None,
    ) -> SubagentResult:
        """
        Run a subagent with the given prompt.
        
        Args:
            name: Subagent name (or @alias)
            prompt: Task prompt
            provider: AI provider to use
            tools_subset: Optional subset of tools to allow
        
        Returns:
            SubagentResult with output or error
        """
        subagent = self.manager.get(name)
        if not subagent:
            return SubagentResult(
                success=False,
                output="",
                error=f"Subagent '{name}' not found"
            )
        
        try:
            # Build subagent messages
            system_prompt = subagent.build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            
            # Get tools for subagent
            tool_defs = subagent.get_tools(tools_subset)
            
            # Run with provider
            full_text = ""
            async for event in provider.complete(messages, tool_defs):
                if hasattr(event, 'text'):
                    full_text += event.text
                elif isinstance(event, str):
                    full_text = event
            
            return SubagentResult(success=True, output=full_text)
            
        except Exception as e:
            return SubagentResult(
                success=False,
                output="",
                error=f"Subagent '{name}' failed: {e}"
            )
    
    def get_task_description(self) -> str:
        """Get formatted description of all subagents for system prompt."""
        return self.manager.task_description()