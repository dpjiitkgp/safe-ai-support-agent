# 1. Review of the draft design

A teammate proposed a first design; its six points are summarised in the left column below.

**The core problem:** the draft trusts the model completely. It decides, picks the values and calls irreversible APIs itself, and nothing records why. My design reverses that: **the model proposes; a gateway written in ordinary code decides and executes.**

| The draft says | What's wrong | What I'd change |
|---|---|---|
| The agent calls APIs directly and refunds on its own | Nothing in code stands between the model and the money, so an injected ticket can reach the refund API. There's no approval record, and the agent and a person can act on the same case. | The agent only *proposes* actions. A gateway holds the credentials, applies approval rules in code, and locks the ticket. |
| Retry up to 10 times when unsure | Retrying picks a confident answer, not a correct one. Writes without idempotency refund twice, and the retries break the speed and cost limits. | If unsure: ask the customer or escalate. Tool errors get at most 2 retries with an idempotency key, inside a hard per-case budget. |
| The amount and fields come from model output | A made-up or injected number moves money that can't be recovered, and a free-text email change enables account takeover. | The model gives references; the owning team's code computes the amount. Identity fields are never editable by the agent. |
| Each team wires its own tools | Two permission models and audit formats, with cross-team cases falling between them. | One tool contract now: a registry the gateway loads. Teams keep their own logic. |
| Log only the final reply | It records the result, not the decision: who approved what, and why. | An append-only record of every proposal, decision, approval and result. |
| Launch to everyone on day one | Maximum blast radius, no baseline and no kill switch. | Offline, then shadow, then human-approved, then automatic, one request type and cohort step at a time. |

**What else is missing:**
- replies treated as commitments (Air Canada was held liable in 2024 for its chatbot's answer)
- customer text treated as untrusted input
- checking that the requester owns the account
- partial failure
- when to escalate
- any test set

**What's worth keeping:** reuse of the existing APIs, and the wish to show impact quickly. We can do that safely by automating questions, duplicate-charge refunds and simple downgrades first.
