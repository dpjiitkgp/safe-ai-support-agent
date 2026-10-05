"""Checks a reply before it reaches the customer.

Code can't tell whether free text is *correct*, so the gateway controls what a reply can *commit
to*. Amounts, dates and outcomes come only from tool templates, filled from confirmed actions.
The free text around them is checked for anything that looks like a commitment, for links, and
for other customers' data. Graders (such as a small model checking claims against the cited
articles) can only hold a reply for a person; they can never release one the checks blocked.

The patterns are deliberately strict. A false alarm sends the model back to the template or the
reply to a person, which is cheap; a missed promise is not."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOWED_LINK_HOSTS = {"help.helia.example", "app.helia.example"}

MONEY = re.compile(
    r"[$€£]\s?\d[\d,]*(?:\.\d+)?|\b\d+(?:[.,]\d+)?\s?(?:usd|eur|gbp|dollars?|euros?|pounds?)\b", re.I
)
TIMEFRAME = re.compile(
    r"\bwithin\s+(?:\d+|a|an|one|two|three)\s+(?:business\s+)?(?:hours?|days?|weeks?)\b"
    r"|\b\d+\s*(?:-|to)\s*\d+\s+(?:business\s+)?(?:hours?|days?|weeks?)\b"
    r"|\b(?:by|before)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|end\s+of)\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.I,
)
PROMISE = re.compile(
    r"\b(?:refund\w*|credit(?!\s+card)\w*|reimburs\w*|compensat\w*|guarantee\w*|waiv\w*|discount\w*"
    r"|free\s+of\s+charge|will\s+be\s+(?:processed|resolved|fixed|applied|returned))\b",
    re.I,
)
URL = re.compile(r"\b(https?)://([^/\s]+)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CUSTOMER_REF = re.compile(r"\bcus_\w+")
CHARGE_REF = re.compile(r"\bch_\w+")


@dataclass
class ReplyCheck:
    text: str
    problems: list[str] = field(default_factory=list)  # blocked: the model must redraft
    for_human: list[str] = field(default_factory=list)  # held: a person reviews before it's sent
    action_ids: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)


def check_reply(
    blocks: list[dict], *, ticket, actions: dict, registry, billing, kb, graders=()
) -> ReplyCheck:
    """`blocks` is what the model proposes: {"text": ..., "cites": [...]} or {"commitment": action_id}."""
    parts, problems, action_ids, citations = [], [], [], []
    for block in blocks:
        text = ""
        if "commitment" in block:
            text, problem = _render_commitment(block["commitment"], ticket, actions, registry)
            if problem:
                problems.append(problem)
            else:
                action_ids.append(block["commitment"])
        elif "text" in block:
            text = block["text"]
            problems.extend(_free_text_problems(text, ticket, billing))
            for article in block.get("cites", []):
                if kb.exists(article):
                    citations.append(article)
                else:
                    problems.append(f"cites an article that doesn't exist: {article}")
        else:
            problems.append(f"unknown block with keys {sorted(block)}")
        parts.append(text)

    check = ReplyCheck("\n\n".join(p for p in parts if p), problems, [], action_ids, citations)
    if not problems:
        for grader in graders:
            concern = grader(check.text)
            if concern:
                check.for_human.append(concern)
    return check


def _render_commitment(action_id, ticket, actions, registry) -> tuple[str, str | None]:
    action = actions.get(action_id)
    if action is None or action.case_id != ticket.id:
        return "", f"commitment refers to an action that isn't on this case: {action_id}"
    template = registry.tool(action.tool).templates.get(action.status)
    if template is None:
        return "", f"can't tell the customer about {action_id}: it is {action.status}, not confirmed"
    return template.format(**{**action.computed, **action.result}), None


def _free_text_problems(text: str, ticket, billing) -> list[str]:
    problems = []
    for pattern, what in (
        (MONEY, "an amount"),
        (TIMEFRAME, "a date or deadline"),
        (PROMISE, "a promise or outcome"),
    ):
        match = pattern.search(text)
        if match:
            problems.append(f"free text contains {what} ('{match.group(0)}'); only a template may say that")
    for scheme, host in URL.findall(text):
        if scheme.lower() != "https" or host.lower() not in ALLOWED_LINK_HOSTS:
            problems.append(f"links to a domain that isn't approved: {host}")
    if any(email.lower() != ticket.customer_email.lower() for email in EMAIL.findall(text)):
        problems.append("mentions an email address that isn't this customer's")
    if any(ref != ticket.customer_id for ref in CUSTOMER_REF.findall(text)):
        problems.append("mentions another customer's account")
    for ref in CHARGE_REF.findall(text):
        charge = billing.get_charge(ref)
        if charge is None or charge.customer_id != ticket.customer_id:
            problems.append("mentions a charge that isn't this customer's")
    return problems
