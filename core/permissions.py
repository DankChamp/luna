from __future__ import annotations
from dataclasses import dataclass, field
import fnmatch


@dataclass
class PermissionRule:
    action: str  # "allow", "ask", "deny"
    command_globs: list[str] | None = None
    is_catchall: bool = False


class PermissionEvaluator:
    def __init__(self, rules: dict[str, str | dict] | None = None):
        self._rules: dict[str, list[PermissionRule]] = {}
        if rules:
            self._parse(rules)

    def _parse(self, rules: dict):
        for tool_key, raw in rules.items():
            if isinstance(raw, str):
                self._rules[tool_key] = [PermissionRule(action=raw, is_catchall=True)]
            elif isinstance(raw, dict):
                parsed: list[PermissionRule] = []
                for pattern, action in raw.items():
                    if not isinstance(action, str):
                        continue
                    if pattern == "*":
                        parsed.append(PermissionRule(action=action, is_catchall=True))
                    else:
                        parsed.append(PermissionRule(action=action, command_globs=[pattern]))
                self._rules[tool_key] = parsed

    def evaluate(self, tool_name: str, *, command: str | None = None) -> str:
        rules = self._rules.get(tool_name)
        if not rules:
            return "allow"

        if command is not None:
            glob_match = None
            catchall = None
            for rule in rules:
                if rule.command_globs:
                    if any(fnmatch.fnmatch(command, g) for g in rule.command_globs):
                        glob_match = rule
                if rule.is_catchall:
                    catchall = rule
            if glob_match:
                return glob_match.action
            if catchall:
                return catchall.action
            return "allow"

        for rule in rules:
            if rule.is_catchall:
                return rule.action
            if not rule.command_globs:
                return rule.action

        return "allow"

    def add_rules(self, rules: dict):
        self._parse(rules)

    def merge(self, other: PermissionEvaluator | None):
        if other is None:
            return
        for tool, rules in other._rules.items():
            self._rules[tool] = rules

    def as_dict(self) -> dict:
        result: dict = {}
        for tool, rules in self._rules.items():
            if len(rules) == 1 and rules[0].is_catchall:
                result[tool] = rules[0].action
            else:
                tool_dict = {}
                for r in rules:
                    if r.is_catchall:
                        tool_dict["*"] = r.action
                    elif r.command_globs:
                        for g in r.command_globs:
                            tool_dict[g] = r.action
                result[tool] = tool_dict if tool_dict else rules[0].action
        return result
