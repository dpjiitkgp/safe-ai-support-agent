# 3. Team standard: building an agent action

**The test:** if your change lets model output reach a Helia API unchecked by the gateway, it doesn't ship.

**1. The path**
- **1.1** Every agent action is a registered tool, called only through the Action Gateway.
- **1.2** One tool, one business action. Its handler takes only gateway calls, with scoped credentials.

**2. Inputs and data**
- **2.1** The model gives only IDs and fixed-list values. Free text is only for replies and handover notes.
- **2.2** Amounts, eligibility and dates come from Helia's data, through a function your team declares.
- **2.3** The gateway checks that the requester is signed in, with the account role your tool needs.
- **2.4** Declare request types, fields written and returned (masked), idempotency key, undo path, limits, kill switch and reply templates.

**3. What needs a person**

| Tier | Example | Runs alone | Approver |
|---|---|---|---|
| **0** Read | Lookup | Always | — |
| **1** Low risk | Rename | Within its rule | Support agent |
| **2** Billing | Downgrade | Narrow Support Ops rule | Support agent |
| **3** Money | Refund | Finance-approved rule, below ceilings | Lead (+ Finance > $1k) |

- **3.1** Approval rules live in the tool definition, and the gateway enforces them.
- **3.2** Finance and Legal own the ceilings, and approvals above them. Ceilings beat any auto rule.
- **3.3** **Never, even with approval:** changing login email, owner, payment method, password or 2FA; custom prices or discounts; over-refunding, or refunding to another payment method; showing another customer's data or a full card number.
- **3.4** Charging a customer more needs their own confirmation.
- **3.5** A reply's amounts, dates and outcomes come only from the tool's Legal-reviewed template.
- **3.6** Sensitive topics go to a person, and every reply names the AI assistant.

**4. What must be logged**
- **4.1** The gateway logs every read, proposal, decision, approval, execution, failure and reply.
- **4.2** Each event records **who** (agent and release, or person and role), **what**, **why** (rule version), **the approval** (approver, time, fingerprint; for automatic actions, the rule's approver) and **the result**.
- **4.3** Handlers never report an unknown outcome as success. Personal data is logged by reference.

**5. Before it ships**
- **5.1** Policy tests at every rule edge, and no auto rule above a ceiling.
- **5.2** The attack suite, with zero actions executed.
- **5.3** Offline evaluation with your cases: zero "never" proposals, no regression, within budget.
- **5.4** New tools, model swaps and major prompt changes: 48 hours in shadow, then a canary on 10% of agent traffic.
- **5.5** Reviewers: your team, the platform team, Finance or Security (tier 3), and Legal (templates).
- **5.6** An on-call runbook that covers the off switch.

**Owner:** the agent platform team; changes by RFC, reviewed by both teams' leads. **Exceptions:** none to sections 1 and 4; we fix the platform, not the rule.
