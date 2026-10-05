# Proof of concept: the Action Gateway

The riskiest part of the design is the code between the model and Helia's money, so this builds it for real, against fake billing and ticketing systems. There's deliberately no LLM here. The tests and the demo play the model, including a manipulated one, because the gateway must be safe whatever the model proposes.

## Run it

You need Python 3.11 or later, and only the standard library.

```
cd poc
python3 demo.py                  # walks through the design's scenarios
python3 -m unittest -v           # 79 tests
python3 check_tests_can_fail.py  # switches off each protection and confirms a test fails
```

## What it proves

| Design claim | Tests |
|---|---|
| **The model can't set values.** It supplies references; the owning team's handler computes the amount, and billing data must confirm the model's reason (a real duplicate, or a charge within the 14-day guarantee). Otherwise a person decides. | `test_attacks`, `test_policy` |
| **Rules live in config and are enforced in code.** An incomplete tool, or an auto limit above a Finance ceiling, never loads, and a ceiling always wins. | `test_registry`, `test_policy` |
| **Approvals are bound.** They carry a person's identity and role, apply only to the exact action, are void if anything (including the rules) changes, expire, and need two different people above $1,000. | `test_approvals` |
| **A person's decision binds the agent.** A decline, an expiry (caught by a scheduled `sweep()`), a failure or an unknown outcome hands the ticket to a person. The kill switch stops new and waiting actions. | `test_approvals`, `test_execution` |
| **A timeout means "unknown".** Billing is asked before any retry, the refund counts against the hourly cap, and nothing is paid twice. | `test_execution` |
| **The audit log is tamper-evident.** It's a hash chain, rejected model output is logged only as a hash, and published anchors catch a rewritten log. | `test_audit` |
| **Replies only commit through templates.** Amounts, deadlines and refund wording are blocked in free text, and graders can hold a reply but never release one. | `test_replies` |

`check_tests_can_fail.py` switches off each of 28 protections in a scratch copy and confirms the suite fails every time.

**Changing a rule.** Limits and ceilings live in `policy/*.toml`. A change is a reviewed edit plus a test, with no code or prompt change. Refunds already waiting are re-checked against the new rules before they run.

## Layout

```
policy/        tools.toml (the registry), ceilings.toml (Finance and Legal), CODEOWNERS.example
helia_gateway/ gateway.py (the pipeline), policy.py, registry.py, handlers.py (Payments' refund
               handler), replies.py, audit.py, fakes.py (billing, ticketing, help centre)
tests/         one file per design claim
```

## What it leaves out

- **The rest of the agent.** There's no LLM, router, orchestrator, queue or console. They sit outside the trust boundary.
- **Production plumbing.** It has one money tool and in-memory state. In production there'd be a database with a lock per ticket, a real anchor store, and identity from SSO.
  - Requesters are assumed to be verified already.
  - CODEOWNERS is an example file, so it doesn't change this repository's review rules.
- **Reply checks are strict regular expressions.** They miss paraphrases such as "the charge is reversed" (a grader model's job), and they block "refund" even in an honest policy answer (quoting the help article would fix that).
- **Refund simplifications:**
  - The duplicate check (same amount, within 3 days) is a heuristic.
  - A refund returns all that's left on the charge.
  - Limits are in one currency.
