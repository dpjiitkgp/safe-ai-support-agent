# Designing a safe AI support agent

How to let an AI agent issue refunds, change plans and reply to customers without trusting the model. That means an architecture, guardrails, a rollout plan, and a one-page standard for the teams building it, plus a working proof of concept of the riskiest part. It was written as a take-home design exercise for an AI Principal Engineer role, by Dhanraj Purbiya.

**The idea in one line:** the model proposes; an Action Gateway written in ordinary code decides and executes. Approval rules, amount calculations and audit records live in code, not in the prompt. The agent works alongside the existing support console, and earns autonomy one request type at a time.

## The problem

Helia is a fictional SaaS company. Its support team handles thousands of requests a day (refunds, plan changes, account fixes and questions), and it's falling behind on mostly routine work. Leadership wants an AI agent that reads a request and the customer's account, decides what to do, acts through Helia's existing APIs, and replies to the customer, with a person in the loop where it matters.

The agent can use six existing APIs, and they differ in how much harm they can do:

| API | Risk |
|---|---|
| Look up account | Read-only |
| Reply to customer | Hard to undo |
| Issue refund | Can't be undone |
| Change plan | Affects billing |
| Update account | Risk to the customer's data |
| Escalate to a person | Safety and credibility |

**The constraints:**
- **Speed and cost:** most cases should be handled in a few seconds, within a per-case cost limit.
- **Accountability:** Finance and Legal need a clear record of who approved each refund.
- **The existing console:** the current human support console must keep working, with the agent alongside it.
- **One approach:** two teams, Payments and Accounts, have already started building agent helpers in different ways, and they need to converge on one.

**A teammate's first draft** proposed letting the agent call the APIs directly and refund on its own:
- retrying up to 10 times when unsure
- taking refund amounts and account fields straight from model output
- letting each team wire up its own tools
- logging only the final reply
- launching to every customer on day one

**The exercise:**
1. Review that draft.
2. Design the system properly: the architecture, the agent loop, where humans approve, failure and rollback, guardrails, and the rollout.
3. Write a one-page standard both teams build to.
4. Build a small proof of concept of the riskiest part.

## What's here

1. [Review of the draft](docs/01-draft-review.md): what's wrong or risky in it, and what I'd change.
2. [Design](docs/02-design.md): architecture (with a diagram), the agent loop, the human-in-the-loop line, failure and rollback, guardrails, rollout, and the key trade-offs.
3. [Team standard](docs/03-team-standard.md): the one-page rules both teams build to.
4. [Proof of concept](poc/README.md): the Action Gateway for refunds and replies, built and tested.

## Run the proof of concept

You need Python 3.11 or later, and only the standard library.

```
cd poc
python3 demo.py                  # walks through the design's scenarios
python3 -m unittest -v           # 79 tests
python3 check_tests_can_fail.py  # switches off each protection and confirms a test fails
```

The diagram in the design is written in Mermaid, which GitHub renders inline.
