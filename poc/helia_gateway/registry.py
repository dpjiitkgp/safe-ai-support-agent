"""Loads the tool registry and the Finance/Legal ceilings, and refuses anything incomplete or
contradictory. This is what makes the team standard a property of the platform: a tool that
skips a required field, or an auto-approve limit set above a ceiling, never loads."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .common import money, sha256_hex

REQUIRED_FIELDS = (
    "owner",
    "wraps",
    "risk_tier",
    "request_types",
    "inputs",
    "idempotency_key",
    "undo",
    "kill_switch",
    "returns",
    "approval_roles",
    "templates",
)
INPUT_TYPES = {"charge_ref", "enum"}
AUTO_CONDITIONS = {"max_amount", "reasons", "max_charge_age_days", "no_refund_within_days", "hourly_cap"}
MONEY_CONDITIONS = {"max_amount", "hourly_cap"}
CEILING_FIELDS = ("id", "tool", "owner", "amount_above", "require_roles")
CEILING_OWNERS = {"finance", "legal"}


class RegistryError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("invalid policy: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ToolDef:
    name: str
    owner: str
    risk_tier: int
    request_types: tuple[str, ...]
    inputs: dict
    idempotency_key: tuple[str, ...]
    returns: tuple[str, ...]
    approval_roles: tuple[str, ...]
    auto_approve: dict | None
    templates: dict
    kill_switch: str
    undo: str


@dataclass(frozen=True)
class Ceiling:
    id: str
    tool: str
    owner: str
    amount_above: Decimal
    require_roles: tuple[str, ...]


class Registry:
    def __init__(self, tools: dict[str, ToolDef], ceilings: list[Ceiling], version: str):
        self.tools = tools
        self.ceilings = ceilings
        self.version = version  # recorded on every decision, so the audit log shows which rules applied

    @classmethod
    def load(cls, policy_dir) -> Registry:
        policy_dir = Path(policy_dir)
        return cls.from_dicts(_read_toml(policy_dir / "tools.toml"), _read_toml(policy_dir / "ceilings.toml"))

    @classmethod
    def from_dicts(cls, tools_raw: dict, ceilings_raw: dict) -> Registry:
        problems: list[str] = []
        tools = {}
        for name, raw in tools_raw.items():
            tool = _parse_tool(name, raw, problems)
            if tool:
                tools[name] = tool
        ceilings = [
            c for raw in ceilings_raw.get("ceiling", []) if (c := _parse_ceiling(raw, tools_raw, problems))
        ]
        for ceiling in ceilings:
            tool = tools.get(ceiling.tool)
            limit = (tool.auto_approve or {}).get("max_amount") if tool else None
            if limit is not None and limit > ceiling.amount_above:
                problems.append(
                    f"{ceiling.tool}: auto-approve limit {limit} is above Finance/Legal ceiling "
                    f"'{ceiling.id}' ({ceiling.amount_above})"
                )
        if problems:
            raise RegistryError(problems)
        return cls(tools, ceilings, sha256_hex({"tools": tools_raw, "ceilings": ceilings_raw})[:12])

    def tool(self, name: str) -> ToolDef | None:
        return self.tools.get(name)

    def ceilings_for(self, tool_name: str) -> list[Ceiling]:
        return [c for c in self.ceilings if c.tool == tool_name]


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_tool(name: str, raw: dict, problems: list[str]) -> ToolDef | None:
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        problems.append(f"{name}: missing {', '.join(missing)}")
        return None
    found_before = len(problems)
    if raw["risk_tier"] not in (0, 1, 2, 3):
        problems.append(f"{name}: risk_tier must be 0-3")
    for field_name, spec in raw["inputs"].items():
        if spec.get("type") not in INPUT_TYPES:
            problems.append(f"{name}.{field_name}: unknown input type {spec.get('type')!r}")
        elif spec["type"] == "enum" and not spec.get("values"):
            problems.append(f"{name}.{field_name}: an enum needs values")
    unknown_key_parts = set(raw["idempotency_key"]) - {"case_id"} - set(raw["inputs"])
    if unknown_key_parts:
        problems.append(f"{name}: idempotency_key uses unknown field(s) {sorted(unknown_key_parts)}")
    auto = raw.get("auto_approve")
    if auto is not None:
        unknown = set(auto) - AUTO_CONDITIONS
        if unknown:
            problems.append(f"{name}: unknown auto-approve condition(s) {sorted(unknown)}")
        if raw["risk_tier"] == 3 and "max_amount" not in auto:
            problems.append(f"{name}: a tier 3 auto-approve rule must set max_amount")
        auto = {k: money(v) if k in MONEY_CONDITIONS else v for k, v in auto.items()}
    if raw["risk_tier"] >= 2 and not raw["approval_roles"]:
        problems.append(f"{name}: tier {raw['risk_tier']} tools need approval_roles")
    if "executed" not in raw["templates"]:
        problems.append(f"{name}: needs an 'executed' reply template")
    if len(problems) > found_before:
        return None
    return ToolDef(
        name=name,
        owner=raw["owner"],
        risk_tier=raw["risk_tier"],
        request_types=tuple(raw["request_types"]),
        inputs=raw["inputs"],
        idempotency_key=tuple(raw["idempotency_key"]),
        returns=tuple(raw["returns"]),
        approval_roles=tuple(raw["approval_roles"]),
        auto_approve=auto,
        templates=raw["templates"],
        kill_switch=raw["kill_switch"],
        undo=raw["undo"],
    )


def _parse_ceiling(raw: dict, tools_raw: dict, problems: list[str]) -> Ceiling | None:
    label = raw.get("id", "?")
    missing = [f for f in CEILING_FIELDS if f not in raw]
    if missing:
        problems.append(f"ceiling {label}: missing {', '.join(missing)}")
        return None
    if raw["tool"] not in tools_raw:
        problems.append(f"ceiling {label}: unknown tool {raw['tool']!r}")
    if raw["owner"] not in CEILING_OWNERS:
        problems.append(f"ceiling {label}: owner must be finance or legal, not {raw['owner']!r}")
    return Ceiling(
        raw["id"], raw["tool"], raw["owner"], money(raw["amount_above"]), tuple(raw["require_roles"])
    )
