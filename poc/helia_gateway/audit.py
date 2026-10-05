"""Append-only, hash-chained audit log.

Each event carries the hash of the event before it, so editing or deleting an event breaks the
chain from that point on, unless every later hash is recomputed; published anchors catch that.
Events hold references (case, charge, customer IDs) and hashes, not message text or contact
details, so a deletion request never has to touch this log."""

from __future__ import annotations

import copy
import json

from .common import canonical_json, sha256_hex

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, clock):
        self._clock = clock
        self._events: list[dict] = []

    def append(self, event_type: str, *, case_id: str, actor: str, **data) -> dict:
        event = {
            "seq": len(self._events),
            "at": self._clock.now.isoformat(),
            "type": event_type,
            "case_id": case_id,
            "actor": actor,
            **data,
            "prev_hash": self._events[-1]["hash"] if self._events else GENESIS,
        }
        event = json.loads(canonical_json(event))  # plain JSON values: nothing can change it later
        event["hash"] = sha256_hex(event)
        self._events.append(event)
        return copy.deepcopy(event)

    def events(self, case_id: str | None = None, event_type: str | None = None) -> list[dict]:
        return [
            copy.deepcopy(e)
            for e in self._events
            if (case_id is None or e["case_id"] == case_id)
            and (event_type is None or e["type"] == event_type)
        ]

    def anchor(self) -> tuple[int, str]:
        """The latest position and hash. Publishing it somewhere the log's writers can't change
        (a daily anchor) is what makes a fully rewritten log detectable."""
        return self._events[-1]["seq"], self._events[-1]["hash"]

    def verify(self, anchors=()) -> int | None:
        """None if the log is intact, otherwise the position of the first problem.

        The chain alone catches edits that don't recompute the hashes after them. Someone who can
        write the log can recompute them all, so anchors published earlier are checked too."""
        prev = GENESIS
        for position, event in enumerate(self._events):
            body = {k: v for k, v in event.items() if k != "hash"}
            if (
                body.get("seq") != position
                or body.get("prev_hash") != prev
                or sha256_hex(body) != event["hash"]
            ):
                return position
            prev = event["hash"]
        for position, expected in anchors:
            if position >= len(self._events) or self._events[position]["hash"] != expected:
                return position
        return None
