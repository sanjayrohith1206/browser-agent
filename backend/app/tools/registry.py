"""Browser tool definitions.

The canonical definitions live in shared/tools.json so the extension and the
backend agree on names, parameters and risk levels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

ToolRisk = Literal["read", "interact", "high_impact"]
# Where a tool executes: in the extension, or in the agent loop itself
# (ask_user, finish_task).
ToolRunner = Literal["browser", "agent"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: ToolRisk
    parameters: dict[str, Any]
    runs_in: ToolRunner = "browser"


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        self._validators = {s.name: Draft202012Validator(s.parameters) for s in specs}

    @classmethod
    def from_file(cls, path: Path) -> ToolRegistry:
        data = json.loads(path.read_text(encoding="utf-8"))
        specs = []
        for raw in data["tools"]:
            Draft202012Validator.check_schema(raw["parameters"])
            specs.append(
                ToolSpec(
                    name=raw["name"],
                    description=raw["description"],
                    risk=raw["risk"],
                    parameters=raw["parameters"],
                    runs_in=raw.get("runs_in", "browser"),
                )
            )
        return cls(specs)

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def validate(self, name: str, args: Any) -> list[str]:
        """Return human-readable validation errors; empty when args are valid."""
        validator = self._validators.get(name)
        if validator is None:
            return [f"unknown tool {name!r}"]
        errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
        return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]

    def as_langchain_tools(self) -> list[dict[str, Any]]:
        """OpenAI-style function tool dicts, accepted by every LangChain chat
        model's bind_tools()."""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                },
            }
            for s in self._specs.values()
        ]
