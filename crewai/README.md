# CrewAI + e2a — an escalation desk with one inbox per specialist

A support escalation desk built with [CrewAI](https://docs.crewai.com), using **several** email identities from [e2a](https://e2a.dev).

A customer emails the front desk. A three-role crew triages the request, investigates it, and drafts a reply — and the reply is sent **from the desk that owns the issue**, not from the front desk. The customer's follow-up then lands in that specialist's own inbox, where it skips triage entirely.

> **An example, not a product.** This is one of the [e2a runbooks](../README.md) — a small demonstration of what you can build with e2a. See [*Simplifications worth knowing*](#simplifications-worth-knowing) at the end for what it deliberately leaves out.

CrewAI is the multi-agent framework, so this is the runbook where multiple *identities* make sense. Every other runbook here has one agent with one address.

## The e2a surface: several agents, one conversation

This is the only runbook that uses more than one agent identity, and the mechanism is one field:

```python
e2a.messages.send(
    DESKS[desk],                              # sending identity: the specialist
    {"to": [email.from_],
     "conversation_id": email.conversation_id, # ...into the thread the FRONT DESK received
     "text": body},
    idempotency_key=event.id,
)
```

`conversation_id` on a send is what makes cross-identity threading work. Without it the specialist's reply starts a parallel thread and the customer sees two conversations about one problem.

Two directions, deliberately different:

| Situation | Call | Why |
| --- | --- | --- |
| Front desk received it | `messages.send(desk, …, conversation_id=…)` | The answer must come from a *different* identity than the one that received |
| The owning desk received it | `email.reply(…)` | Already the right identity, and `reply` sets `In-Reply-To`/`References` properly |

`email.inbox` (the delivered-to address) is what distinguishes the two. The app also verifies at startup that every configured desk exists as an e2a agent via `agents.list()` — sending from an address that isn't a real agent fails *after* the crew has already run and the customer is waiting, so it refuses to boot instead.

```
customer ──▶ support@ ──webhook──▶ triage ──▶ investigator ──▶ correspondent
                                      │                              │
                                      └── desk = "technical" ────────┤
                                                                     ▼
                            reply sent AS tech@, threaded via conversation_id
                                                                     │
                              customer replies ──▶ tech@ ──▶ skips triage
```

## Routing is a closed set, not a free-text address

Triage returns a **desk name** validated against a `Literal`, and `app.py` resolves it to an address:

```python
Desk = Literal["billing", "technical", "security"]
```

A model that has been talked into "forward this to attacker@evil.example" has no field in which to express it. The runbook asserts the `Literal` and the `DESKS` dict have identical keys, so a triage result can never name a desk the app cannot resolve.

## What the crew does and doesn't get to decide

- **A follow-up stays with its owner.** Triage still runs on a reply arriving at a specialist inbox — it supplies severity and the manipulation check — but its `desk` is ignored. A conversation a specialist is already handling doesn't get re-routed by a later email.
- **A flagged message is not auto-answered.** When triage sets `injection_attempt`, no reply goes to the sender; a notice goes to the security desk as a separate thread. Replying to a probe confirms to its author that it landed.
- **The crew has no tools.** It reads text and returns text; every send, fetch, and label is done by `app.py`. The blast radius of an injected instruction is "the reply reads oddly", not "the agent emailed someone".

## Prompt injection, and one thing CrewAI gets right

The email body is passed as an interpolation **value**, never as part of a template. Verified against `crewai.utilities.string_utils.interpolate_only`:

- Curly braces in customer text are left as written — `error at {line 42}` does not crash the run.
- A `{sender}` written inside the body is **not** substituted. Values are not re-scanned, so a customer cannot reach the template.
- A placeholder with no matching input raises `KeyError` at kickoff. Task descriptions may only reference keys `kickoff(inputs=…)` supplies — which is why the later tasks cannot mention `{desk}`: it isn't known until triage has run. The triage result reaches them through `context=[triage_task]` instead.

The sender's SPF/DKIM/DMARC verdict goes into the triage prompt, and when it fails, identity claims in the body are treated as unproven.

## Quickstart

```bash
python3.13 -m venv .venv && source .venv/bin/activate   # NOT 3.14 — see Versions
pip install -r requirements.txt
cp .env.example .env          # fill it in
uvicorn app:app --reload --port 8000
```

You need **four** e2a agents — the front desk plus three specialists. Create them first; the app checks and refuses to start if any is missing.

Expose the port, point an e2a webhook at `<tunnel-url>/webhooks/e2a` subscribed to `email.received`, copy the signing secret into `E2A_WEBHOOK_SECRET`, then email the front desk.

## Configuration

| Variable | Required | What it is |
| --- | --- | --- |
| `E2A_API_KEY` | yes | Account key (`e2a_acct_`) — it sends as several agents, so an agent-scoped key is too narrow |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `FRONT_DESK_EMAIL` | yes | The inbox customers write to |
| `BILLING_DESK_EMAIL` | yes | Specialist inbox; must exist as an e2a agent |
| `TECHNICAL_DESK_EMAIL` | yes | Specialist inbox; must exist as an e2a agent |
| `SECURITY_DESK_EMAIL` | yes | Specialist inbox; also receives flagged-message notices |
| `MODEL` | no | Defaults to `anthropic/claude-opus-5`. CrewAI picks the provider from the prefix |
| `ANTHROPIC_API_KEY` | yes | Model access (or the key for whichever provider `MODEL` names) |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

## Reading the response

| `status` | Meaning |
| --- | --- |
| `answered` | Reply sent. `desk` is who answered, `routed_by` is whether triage or existing ownership decided it, `sent_as` is the identity e2a actually sent from. |
| `flagged` | Manipulation attempt. **No reply was sent to the sender**; the security desk was notified. |
| `not_my_inbox` | Delivered to an address this app doesn't run |
| `ignored` / `duplicate` | Not an inbound event / already handled |

**Check `send_status`.** A queued-for-approval value means an outbound review is holding the reply for a human and the customer has *not* been answered yet.

## Simplifications worth knowing

- **`MODEL` applies to all three roles.** Triage is a classification job that a small fast model does well; only the correspondent really needs a strong one. Per-agent `llm=` is the obvious first optimisation.
- **A flagged message still costs a full crew run.** Triage and drafting happen in one `kickoff()`, so the drafted reply is produced and then discarded. Splitting into two kickoffs would let it short-circuit.
- **Duplicate suppression is an in-memory `set`.** Sends also carry `idempotency_key=event.id`, so a duplicate that slips past the set is still caught server-side — but back the set with a unique insert before running more than one instance.
- **`DESKS` is three hardcoded desks.** Adding one means editing both `DESKS` and the `Desk` literal; the startup assertion will catch it if you forget.
- **No escalation timer.** Nothing pages a human if a review is never approved. `email.review_requested` is the hook.

The [`mastra/`](../mastra) runbook is the fully-worked reference — same core with tests, structured tool errors, and the approval path.

## Versions

Pinned to the latest published releases as of **2026-08-07**, with every symbol verified against the installed packages.

**CrewAI 1.x requires Python `>=3.10,<3.14`** and will not install on 3.14 — the only runbook here with an upper bound on Python.

| Package | Version |
| --- | --- |
| `crewai` | 1.15.13 |
| `anthropic` (via `crewai[anthropic]`) | 0.73.0 |
| `e2a` | 5.6.0 |
| `fastapi` | 0.141.1 |
| `uvicorn` | 0.52.1 |
