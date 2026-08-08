# LangGraph + e2a — a supplier follow-up desk that starts the conversation

A purchasing agent built with [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), using [e2a](https://e2a.dev) as both its email address **and** its follow-up queue.

It emails suppliers about open purchase orders, chases the ones who go quiet, reads the replies into structured commitments, and hands a PO to a human the moment the terms stop being acceptable.

> **An example, not a product.** This is one of the [e2a runbooks](../README.md) — a small demonstration of what you can build with e2a. See [*Simplifications worth knowing*](#simplifications-worth-knowing) at the end for what it deliberately leaves out.

## The inversion

Every other runbook in this repo waits for mail. This one starts the conversation, and that changes the hard part.

An agent that only replies needs no memory of who it is waiting on — the inbound email *is* the trigger. An agent that initiates needs a durable answer to a question no email will ever arrive to ask:

> Who is overdue for a follow-up, and who already answered?

Get that answer from the wrong place and a supplier gets the same chase twice.

## The e2a surface: the mailbox is the queue

`contacts.outreach()` returns, for each contact an agent is working, the reply and delivery facts **e2a derives from real message activity** — not from anything this app writes:

```python
due = e2a.contacts.outreach(
    AGENT_EMAIL,
    suppressed=False,                                  # bounced or complained
    next_action_before=now,                            # follow-up is due
    last_outbound_before=now - timedelta(days=3),      # …and we haven't just written
)
```

That query is the whole scheduler. There is no jobs table, no `last_chased_at` column, no cron state — and nothing that can disagree with what actually went out.

Two filters, and the second is the one people leave out:

| Filter | What it does | What happens without it |
| --- | --- | --- |
| `next_action_before` | Whose follow-up is due | Nothing is ever chased |
| `last_outbound_before` | Whose follow-up we haven't *just sent* | **Every failed state write becomes a duplicate email** |

The second one matters because of a specific failure. The app sends a chase, then writes `next_action_at` forward. If it dies in between — crash, timeout, deploy mid-tick — the contact is still due, and the next tick chases them again. But `last_outbound_at` is maintained by e2a from the message it actually accepted, so that contact is excluded regardless of whether our write landed. The server-derived fact covers the gap our own write left.

### `replied` means "ever replied"

The SDK docstring suggests pairing the sweep with `replied=False`, and for a cold outreach sequence that is right. Here it is a bug, and a quiet one.

e2a's `replied` means *this contact has ever sent us anything* — not *replied to the last thing we sent*. A supplier who writes back "let me check with the factory" and then goes silent is exactly the one that most needs chasing, and `replied=False` would remove them from the sweep permanently.

So this runbook doesn't filter on it. What ends a chase is `next_action_at` being cleared, which only the two terminal stages do:

```python
{"stage": "confirmed", "next_action_at": None}   # explicit null clears the schedule
```

## The PO number is the conversation id

There is no mapping table in this runbook, in either direction, because the join key is assigned rather than looked up:

```python
e2a.messages.send(AGENT_EMAIL, {
    "to": [po.supplier_email],
    "subject": _subject(po),
    "text": body,
    "conversation_id": f"po-{po.po_number}",   # ours, not e2a's
})
```

Inbound, the whole lookup is a string prefix:

```python
po_number = orders.po_number_for(email.conversation_id)   # "po-PO-1042" -> "PO-1042"
```

Let e2a assign the conversation id instead and you have to store the one it picked — a row that can drift from the mailbox.

### Keep the subject stable, and reply to your own chases

e2a groups by `conversation_id`. Gmail and Outlook **do not** — they thread on `In-Reply-To`/`References` plus a stable subject. A follow-up sent as a fresh `send` with the same `conversation_id` is filed correctly by e2a and still shows up as a brand-new thread in the supplier's client. e2a's own view stays tidy, which is why nobody catches this from the sending side.

So the first message for a PO is a `send`, and **every message after it is a `reply`** — including the agent's own follow-ups, which reply to the newest message in the thread.

## What the model decides, and what it doesn't

The graph splits along one line: the model handles language, Python handles numbers.

```
START ─┬─ "read"    ─▶ read_reply ─▶ assess ─┬─▶ write_reply ─▶ END
       │                 (model)    (python) └─▶ hand_off ────▶ END
       ├─ "chase"   ─▶ write_chase ────────────────────────────▶ END
       └─ "give_up" ─▶ hand_off ───────────────────────────────▶ END
```

- **`read_reply`** (model) turns the supplier's email into fields: quantity, ship date, unit price, lead time. Every numeric field is nullable, because a model asked for a ship date will invent one rather than leave it blank unless the schema makes blank a valid answer.
- **`assess`** (plain Python) does the arithmetic and applies the thresholds.
- **`write_reply` / `write_chase`** (model) write the prose, given a decision already made.
- **`hand_off`** is deliberately *not* a model call. It runs when the agent is out of its depth, and a node that drafts prose in that state is one more place for a bad reply to come from.

Whether 400 units against a 500-unit order is acceptable is a business rule with a number attached. Asking a model to apply a threshold it can round, rephrase, or be argued out of is how an agent quietly accepts a bad delivery:

| Env var | Default | Escalates when |
| --- | --- | --- |
| `MAX_SHORTFALL_PCT` | `5` | They commit to fewer units than ordered, by more than this |
| `MAX_SLIP_DAYS` | `3` | Their ship date is later than the need-by date, by more than this |
| `MAX_PRICE_INCREASE_PCT` | `2` | They quote a higher unit price than agreed, by more than this |

Two subtler rules live in `assess` too:

- **A reply that commits to nothing is not a confirmation.** "We'll look into it and revert" needs no human, but it closes nothing — so it gets asked again rather than marked confirmed. `needs_human` and `committed` are separate flags for exactly this reason.
- **A lead time is converted to a date in Python**, never by the model. "Three weeks" resolved against the wrong idea of *today* is a silent off-by-days.

### The graph has no tools

It reads facts and returns a decision; every send, every state write, and every ERP update happens in `app.py`. An injected instruction in a supplier's email can therefore make a reply read oddly — it cannot make the agent email someone. Supplier text reaches exactly one node, fenced and labelled as data, alongside the SPF/DKIM/DMARC verdict.

## Counting the chase

`MAX_CHASES` is about *consecutive unanswered* follow-ups, so the count is derived from the PO's own thread:

```python
for message in reversed(messages):
    if message.direction == "inbound":
        break
    unanswered += 1
```

The engagement's `outbound_count` is the tempting shortcut and it is wrong twice over: it counts every outbound message to that supplier including our answers to them, so a chatty exchange burns the budget without a single unanswered ask — and it is per-*contact*, so a supplier with two open POs has the two chases counted against each other. The conversation is per-PO and knows direction.

## What it looks like

```
Day 0   POST /orders/PO-1042/open
        agent ─▶ supplier   "PO-1042, 500 × TEE-ORG-BLK-M — confirm quantity and ship date?"
        contact enrolled · stage=awaiting_reply · next_action_at=+3d

Day 3   POST /tick
        due: next_action_at passed, last_outbound_at is 3 days old
        agent ─▶ supplier   follow-up 1, as a reply, same subject
        stage=chasing · next_action_at=+3d

Day 5   supplier ─▶ agent   "we can do 400 by the 20th"
        read_reply  → {quantity: 400, ship_date: 2026-09-20}
        assess      → 20% short (limit 5%), 8 days late (limit 3)  → needs_human
        agent ─▶ buyer      "[needs you] PO-1042 — Northwind Textiles"
        stage=escalated · next_action_at=None   ← chase stops; a human owns it
```

The supplier gets nothing on that last step. Answering a pushback the agent isn't authorised to accept is worse than silence.

## Quickstart

```bash
cd langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in the keys
uvicorn app:app --reload --port 8000
```

Point an e2a webhook at `POST /webhooks/e2a`, filtered to `email.received`, then drive it:

```bash
# Start chasing a PO. Your ERP calls this when a PO is issued.
curl -X POST localhost:8000/orders/PO-1042/open -H "x-tick-token: $TICK_TOKEN"

# The sweep. Run it on a schedule.
curl -X POST localhost:8000/tick -H "x-tick-token: $TICK_TOKEN"
```

`/tick` is a plain endpoint rather than an in-process scheduler on purpose — it stays correct when you run more than one instance, and it is the shape cron, Cloud Scheduler, and a Kubernetes CronJob all want. Hourly is plenty; the per-contact `next_action_at` controls cadence, not how often the sweep runs.

Both endpoints send mail, so both require `x-tick-token`.

## Configuration

| Variable | Required | Notes |
| --- | --- | --- |
| `E2A_API_KEY` | yes | **Account scope** (`e2a_acct_`) — this runbook creates contacts, which is account-level. An agent key can drive its own outreach records but not `contacts.create` |
| `E2A_AGENT_EMAIL` | yes | The inbox the desk sends from and receives on |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `BUYER_EMAIL` | yes | Where every escalation goes. The supplier is told a person will follow up, so a person has to receive it |
| `TICK_TOKEN` | yes | Shared secret for `/tick` and `/orders/{po}/open` |
| `COMPANY_NAME` | no | Appears in the outbound signature |
| `MAX_SHORTFALL_PCT` | no | Defaults to `5` |
| `MAX_SLIP_DAYS` | no | Defaults to `3` |
| `MAX_PRICE_INCREASE_PCT` | no | Defaults to `2` |
| `MAX_CHASES` | no | Unanswered follow-ups before handing off. Defaults to `3` |
| `CHASE_INTERVAL_DAYS` | no | Days between follow-ups. Defaults to `3` |
| `MODEL` | no | Defaults to `claude-opus-5` |
| `ANTHROPIC_API_KEY` | yes | Model access |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

## Reading the response

`POST /tick` returns what it did, and the three lists are all worth watching:

```json
{"status": "ticked", "due": 2,
 "chased":  [{"po_number": "PO-1042", "action": "reply", "attempt": 2, "send_status": "accepted"}],
 "skipped": [{"address": "sales@northwind-textiles.example", "reason": "no matching PO"}],
 "failed":  []}
```

- **`skipped`** means an engagement points at a PO the ERP doesn't have open. The agent won't guess, and a growing `skipped` list means enrolments are outliving their orders.
- **`failed`** is per-PO: one supplier's failure doesn't abort the sweep, but it isn't swallowed either. A tick that reports failures is how you find out the agent stopped chasing.
- **`send_status`** of `pending_review` means an outbound review is holding the message and the supplier has **not** been written to yet.

The webhook response reports `stage`, the extracted `committed_quantity` / `committed_ship_date`, the `concerns` list from `assess`, and `manipulation_attempt`.

## Simplifications worth knowing

- **`orders.py` is a dict, not an ERP.** In a real deployment it's a thin client over Shopify, NetSuite, or Cin7. The boundary is the part worth copying: the agent asks what was ordered and gets facts back — it never asks the model, and it never caches the answer in e2a.
- **Duplicate suppression is an in-memory `set`.** Fine for one instance; back it with a unique insert on the event id before running more. Note the sweep is already safe across instances — `last_outbound_before` does that work — but the webhook path is not.
- **One engagement per supplier, so one active PO per supplier.** Contacts are account-level and outreach records are per (agent, contact); a supplier with two open POs would share one `next_action_at`. The honest fixes are a separate agent inbox per product line, or keeping the schedule in your own ERP and using e2a only for `last_outbound_at`.
- **Escalation is one email and then silence.** Nothing tracks whether the buyer acted, and nothing un-escalates. `stage="escalated"` with a cleared schedule is a dead end by design — a human has it now.
- **The agent never negotiates.** It accepts within tolerance or escalates. Countering ("can you do 480 if we split the shipment?") is a real use case and deliberately out of scope.
- **No attachments.** Suppliers send PDF proforma invoices constantly and this reads only the plain-text body. See [`langchain/`](../langchain) for the attachment path.
- **`send_at` is not used.** e2a can schedule a send server-side, which would let a chase be queued at open time rather than swept for. It's beta, and a sweep is easier to reason about when a PO gets confirmed early and the queued mail has to be unqueued.

The [`mastra/`](../mastra) runbook is the fully-worked reference — same core with tests, structured tool errors, and the approval path.

## Versions

Pinned to the latest published releases as of **2026-08-08**, with every symbol verified against the installed packages:

| Package | Version |
| --- | --- |
| `langgraph` | 1.2.10 |
| `langchain-anthropic` | 1.5.4 |
| `langchain-core` (transitive) | 1.5.3 |
| `anthropic` (transitive) | 0.121.0 |
| `e2a` | 5.6.0 |
| `fastapi` | 0.141.1 |
| `uvicorn` | 0.52.1 |

The `langchain` meta-package is deliberately absent: this runbook needs the graph runtime and one chat model, and pulling in the whole framework for `init_chat_model` would make the dependency surface look larger than the code.
