from __future__ import annotations
import fnmatch


class PolicyEvaluator:
    def __init__(self, rules: list[dict] | None = None):
        self._rules: list[dict] = rules or []

    def load(self, rules: list[dict]):
        self._rules = rules

    def evaluate(self, action: str, resource: str) -> str:
        matching = None
        for rule in self._rules:
            r_action = rule.get("action", "")
            r_resource = rule.get("resource", "")
            if r_action == action and fnmatch.fnmatch(resource, r_resource):
                matching = rule
        if matching:
            return matching.get("effect", "allow")
        return "allow"
