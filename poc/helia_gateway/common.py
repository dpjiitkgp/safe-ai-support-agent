"""Shared types and helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

AGENT_ID = "helia-agent"


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(data) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who is calling the gateway. In production this comes from service identity or SSO."""

    id: str
    kind: str  # "agent" or "human"
    roles: frozenset[str] = frozenset()

    def has_role(self, role: str) -> bool:
        return role in self.roles


AGENT = Principal(AGENT_ID, "agent")


def human(user_id: str, *roles: str) -> Principal:
    return Principal(user_id, "human", frozenset(roles))
