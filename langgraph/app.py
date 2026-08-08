"""A supplier follow-up desk with its own email address, via e2a.

Every other runbook in this repo waits for mail. This one starts the
conversation: it emails suppliers about open purchase orders, chases the ones
who go quiet, reads the replies into structured commitments, and hands a PO to
a human the moment the terms stop being acceptable.

That inversion is the whole point. An agent that only replies needs no memory
of who it is waiting on. An agent that initiates needs a durable answer to
"who is overdue for a follow-up, and who already answered?" — and getting that
answer from the wrong place is how a supplier receives the same chase twice.

Here the answer comes from e2a. `contacts.outreach()` returns each contact's
outreach record with `replied`, `last_outbound_at`, and `next_action_at` on it,
derived by e2a from real message activity. The mailbox is the queue; this app
stores nothing.

Run:  uvicorn app:app --reload --port 8000
Then point an e2a webhook (email.received) at POST /webhooks/e2a,
and call POST /tick on a schedule (see the README).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()  # read .env before anything reads os.environ

from e2a import (
    AsyncE2AClient,
    E2AConflictError,
    E2ANotFoundError,
    E2AWebhookSignatureError,
    construct_event,
)
from fastapi import FastAPI, HTTPException, Request

import orders
from chase import CHASE_INTERVAL_DAYS, MAX_CHASES, ChaseState, Outcome, graph

AGENT_EMAIL = os.environ["E2A_AGENT_EMAIL"]
WEBHOOK_SECRET = os.environ["E2A_WEBHOOK_SECRET"]

# Required, not optional. Every escalation path in chase.py ends with a message
# to this address, and the supplier is told a person will follow up — so a
# person has to actually receive it.
BUYER_EMAIL = os.environ["BUYER_EMAIL"]

COMPANY = os.environ.get("COMPANY_NAME", "our company")

# Protects the tick endpoint. It sends mail, so it is not something the open
# internet should be able to trigger.
TICK_TOKEN = os.environ["TICK_TOKEN"]

# Reads E2A_API_KEY (and E2A_API_URL if you self-host) from the environment.
#
# The ASYNC client, because the handlers are `async def`. The sync E2AClient
# raises RuntimeError when called from inside a running event loop, so a server
# pairing the two fails on its first inbound email. Every e2a call below is
# awaited.
e2a = AsyncE2AClient()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await e2a.aclose()


app = FastAPI(lifespan=lifespan)

# Webhook delivery is at-least-once. Claim the event id BEFORE running the
# graph — the failure being prevented is a second reply landing in a supplier's
# inbox. In-memory is fine for one instance; back it with a unique insert on the
# event id before running more.
_seen: set[str] = set()


def _signature(body: str) -> str:
    return (
        f"{body.strip()}\n\n--\n"
        f"Purchasing, {COMPANY}\n"
        f"{AGENT_EMAIL}\n"
        "This mailbox is monitored by an automated assistant; a person reads escalations."
    )


def _subject(po: orders.PurchaseOrder) -> str:
    """One subject per PO, forever.

    e2a groups by `conversation_id`, but the supplier's mail client does not —
    Gmail and Outlook thread on In-Reply-To/References plus a *stable* subject.
    Changing this string between chases splits one negotiation into several
    threads in the supplier's inbox while e2a's own view stays tidy, which is
    exactly the kind of bug nobody notices from the sending side.
    """
    return f"{po.po_number} — {po.quantity} × {po.sku} ({po.description})"


@dataclass
class ThreadState:
    """What the PO's own email thread says about where the chase stands."""

    latest_id: str | None
    # Outbound messages since the last inbound one — i.e. how many times we
    # have asked without an answer.
    unanswered: int


async def _thread_state(po: orders.PurchaseOrder) -> ThreadState:
    """Derive the chase position from the thread itself.

    The tempting shortcut is the engagement's `outbound_count`, which e2a
    already maintains. It is the wrong number twice over: it counts every
    outbound message to that supplier including our answers to them, so a
    chatty exchange burns the chase budget without a single unanswered ask; and
    it is per-contact, so a supplier with two open POs has the two chases
    counted against each other. The conversation is per-PO and distinguishes
    direction, so counting here gives "consecutive unanswered follow-ups on
    this order", which is what MAX_CHASES is actually about.
    """
    try:
        conversation = await e2a.conversations.get(
            AGENT_EMAIL, orders.conversation_id_for(po.po_number)
        )
    except E2ANotFoundError:
        return ThreadState(None, 0)  # first contact for this PO

    # Do not rely on server ordering; sort explicitly.
    messages = sorted(conversation.messages, key=lambda m: m.created_at)
    if not messages:
        return ThreadState(None, 0)

    unanswered = 0
    for message in reversed(messages):
        if message.direction == "inbound":
            break
        unanswered += 1

    return ThreadState(messages[-1].id, unanswered)


async def _send_in_thread(
    po: orders.PurchaseOrder,
    body: str,
    *,
    latest_id: str | None,
    idempotency_key: str,
):
    """Put a message in front of the supplier, in the PO's thread.

    The first message for a PO is a `send` that assigns the conversation id.
    Every message after it is a `reply` to the newest message in that
    conversation — including our own follow-ups. A `send` tagged with the same
    conversation_id would be filed correctly by e2a and still show up as a
    brand-new thread in the supplier's client, because a fresh send carries no
    References header.
    """
    if latest_id is not None:
        return await e2a.messages.reply(
            AGENT_EMAIL,
            latest_id,
            {"text": _signature(body)},
            idempotency_key=idempotency_key,
        )

    return await e2a.messages.send(
        AGENT_EMAIL,
        {
            "to": [po.supplier_email],
            "subject": _subject(po),
            "text": _signature(body),
            # Assigned by us, not by e2a. The PO number is the join key, so
            # inbound mail resolves to an order with no lookup table.
            "conversation_id": orders.conversation_id_for(po.po_number),
        },
        idempotency_key=idempotency_key,
    )


async def _escalate(po: orders.PurchaseOrder, note: str, *, idempotency_key: str):
    """Hand the PO to the buyer. A separate thread, deliberately — this is not
    correspondence with the supplier and must not land in their thread."""
    return await e2a.messages.send(
        AGENT_EMAIL,
        {
            "to": [BUYER_EMAIL],
            "subject": f"[needs you] {po.po_number} — {po.supplier_name}",
            "text": (
                f"{note}\n\n"
                f"PO: {po.po_number}\n"
                f"Supplier: {po.supplier_name} <{po.supplier_email}>\n"
                f"Ordered: {po.quantity} × {po.sku} at ${po.unit_price_usd:.2f}\n"
                f"Need by: {po.need_by}\n"
                f"Thread: conversation {orders.conversation_id_for(po.po_number)}\n\n"
                "The agent has stopped following up on this order."
            ),
        },
        idempotency_key=idempotency_key,
    )


async def _apply(
    po: orders.PurchaseOrder,
    outcome: Outcome,
    *,
    latest_id: str | None,
    idempotency_key: str,
):
    """Execute one graph decision: send, then record.

    Ordering is load-bearing and the safe direction is "send, then record". If
    the state write fails after a successful send, e2a's own server-maintained
    `last_outbound_at` still moves — and the sweep filters on it — so the
    supplier does not get chased again on the next tick. Record first and a
    crash in between loses the message entirely.
    """
    if outcome.action == "escalate":
        result = await _escalate(po, outcome.escalation_note or "", idempotency_key=idempotency_key)
        orders.set_status(po.po_number, "escalated")
    else:
        result = await _send_in_thread(
            po, outcome.body, latest_id=latest_id, idempotency_key=idempotency_key
        )
        if outcome.stage == "confirmed":
            orders.set_status(po.po_number, "confirmed")

    # Omitted fields are left unchanged, so this advances the stage and the
    # schedule without touching the metadata written at enrolment. `None` for
    # next_action_at is sent as an explicit null, which clears the schedule —
    # that is what takes a finished PO out of the sweep.
    await e2a.contacts.set_outreach(
        AGENT_EMAIL,
        po.supplier_email,
        {"stage": outcome.stage, "next_action_at": outcome.next_action_at},
    )
    return result


# ── Opening a chase ─────────────────────────────────────────────────────────


@app.post("/orders/{po_number}/open")
async def open_chase(po_number: str, request: Request) -> dict[str, object]:
    """Start following up on a PO. In production your ERP calls this when a PO
    is issued; here it is a manual trigger so the runbook can be driven by hand."""
    _require_tick_token(request)

    po = orders.get(po_number)
    if po is None:
        raise HTTPException(status_code=404, detail=f"unknown purchase order {po_number}")

    # Contacts are account-level: the same supplier may be worked by several
    # agents, so creating one twice is a conflict rather than a duplicate row.
    # Treat the conflict as success — this endpoint has to be safe to re-run.
    try:
        await e2a.contacts.create(
            {"address": po.supplier_email, "display_name": po.supplier_name},
            idempotency_key=f"contact:{po.supplier_email}",
        )
    except E2AConflictError:
        pass

    # Enrol the supplier in THIS agent's outreach. `metadata` is replace-
    # wholesale, not a patch — every later write in this app omits the field
    # entirely rather than resending it, because sending a partial object here
    # would erase the po_number and orphan the engagement.
    await e2a.contacts.set_outreach(
        AGENT_EMAIL,
        po.supplier_email,
        {
            "stage": "awaiting_reply",
            "next_action_at": datetime.now(timezone.utc)
            + timedelta(days=CHASE_INTERVAL_DAYS),
            "metadata": {"po_number": po.po_number},
        },
    )

    state: ChaseState = {
        "mode": "chase",
        "po": po,
        "chases_sent": 0,
        "today": datetime.now(timezone.utc).date(),
    }
    result = await graph.ainvoke(state)
    outcome: Outcome = result["outcome"]

    send = await _apply(
        po, outcome, latest_id=None, idempotency_key=f"open:{po.po_number}"
    )
    return {
        "status": "opened",
        "po_number": po.po_number,
        "conversation_id": orders.conversation_id_for(po.po_number),
        "send_status": send.status,
    }


# ── The sweep ───────────────────────────────────────────────────────────────


def _require_tick_token(request: Request) -> None:
    if request.headers.get("x-tick-token") != TICK_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing x-tick-token")


@app.post("/tick")
async def tick(request: Request) -> dict[str, object]:
    """Chase every supplier who is overdue for a follow-up.

    Call this on a schedule — hourly is plenty; the per-contact schedule is what
    controls cadence, not how often this runs.
    """
    _require_tick_token(request)
    now = datetime.now(timezone.utc)

    # The query that makes this runbook work.
    #
    # `next_action_before=now` is the obvious half: whose follow-up is due.
    #
    # `last_outbound_before` is the half that prevents double-sends, and it is
    # the one people leave out. It filters on `last_outbound_at`, which e2a
    # maintains from actual sent mail rather than from anything this app writes.
    # If a chase went out and the `set_outreach` after it failed — crash,
    # timeout, deploy mid-tick — that contact's `next_action_at` is still in the
    # past, so the next tick would chase them again. Their `last_outbound_at` is
    # not, so they are excluded. Without this filter every failed state write
    # becomes a duplicate email to a supplier.
    #
    # `replied` is deliberately NOT filtered on, and this is the subtle one.
    # e2a's `replied` means "this contact has ever sent us anything", not
    # "replied to the last thing we sent". Passing `replied=False` — which is
    # right for a cold outreach sequence, and what the SDK docstring suggests —
    # would permanently remove any supplier who once answered. A supplier who
    # writes back "let me check with the factory" and then goes silent is
    # exactly the one that most needs chasing, and they would never be swept
    # again. What ends the chase here is `next_action_at` being cleared, which
    # only the two terminal stages do.
    due = e2a.contacts.outreach(
        AGENT_EMAIL,
        suppressed=False,  # never chase an address that bounced or complained
        next_action_before=now,
        last_outbound_before=now - timedelta(days=CHASE_INTERVAL_DAYS),
    )

    work: list[orders.PurchaseOrder] = []
    skipped: list[dict[str, str]] = []

    async for engagement in due:
        # The engagement carries the PO number it was enrolled for. The
        # conversation id would give the same answer, but only once a message
        # exists — metadata is set at enrolment, so it is right from the start.
        po_number = engagement.metadata.get("po_number")
        po = orders.get(po_number) if po_number else None
        if po is None:
            # An engagement pointing at a PO the ERP no longer has. Do not
            # guess and do not chase — say so in the response so it is visible.
            skipped.append({"address": engagement.address, "reason": "no matching PO"})
            continue
        if po.status != "open":
            skipped.append({"address": engagement.address, "reason": f"PO is {po.status}"})
            continue
        work.append(po)

    results = await asyncio.gather(
        *(_chase_one(po) for po in work), return_exceptions=True
    )

    chased = [r for r in results if not isinstance(r, BaseException)]
    failed = [
        {"po_number": po.po_number, "error": repr(r)}
        for po, r in zip(work, results)
        if isinstance(r, BaseException)
    ]
    return {
        "status": "ticked",
        "due": len(work),
        "chased": chased,
        "skipped": skipped,
        # One supplier's failure must not abort the sweep, but it must not be
        # swallowed either — a tick that reports failures is how you find out
        # the agent stopped chasing.
        "failed": failed,
    }


async def _chase_one(po: orders.PurchaseOrder) -> dict[str, object]:
    thread = await _thread_state(po)

    state: ChaseState = {
        "mode": "give_up" if thread.unanswered >= MAX_CHASES else "chase",
        "po": po,
        "chases_sent": thread.unanswered,
        "today": datetime.now(timezone.utc).date(),
    }
    outcome: Outcome = (await graph.ainvoke(state))["outcome"]

    # The idempotency key pins one send per PO per attempt number, so a tick
    # that runs twice — overlapping cron, a retried deploy — cannot produce two
    # copies of follow-up 2. It is derived from the thread rather than a local
    # counter, so it survives a restart.
    send = await _apply(
        po,
        outcome,
        latest_id=thread.latest_id,
        idempotency_key=f"chase:{po.po_number}:{thread.unanswered + 1}",
    )
    return {
        "po_number": po.po_number,
        "action": outcome.action,
        # Only meaningful for a follow-up. An escalation is the chase ending,
        # not another attempt at it.
        "attempt": thread.unanswered + 1 if outcome.action == "reply" else None,
        "unanswered_before": thread.unanswered,
        "send_status": send.status,
    }


# ── Inbound: a supplier replied ─────────────────────────────────────────────


@app.post("/webhooks/e2a")
async def inbound(request: Request) -> dict[str, object]:
    # Verify against the RAW bytes. Parsing first and re-serializing changes the
    # bytes and the signature will not match.
    raw = await request.body()
    signature = request.headers.get("x-e2a-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="missing x-e2a-signature")

    try:
        event = construct_event(raw, signature, WEBHOOK_SECRET)
    except E2AWebhookSignatureError:
        # Unverified payloads are untrusted. Never reach the graph.
        raise HTTPException(status_code=401, detail="signature verification failed")

    # e2a emits the whole lifecycle. Without this guard the delivery receipt for
    # the agent's own chase comes back as an event and the agent answers itself
    # — which, in an outbound agent, means an infinite thread with a supplier
    # cc'd on it.
    if event.type != "email.received":
        return {"status": "ignored", "type": event.type}

    if event.id in _seen:
        return {"status": "duplicate", "event_id": event.id}
    _seen.add(event.id)

    email = await e2a.inbound.from_event(event)

    # The join. No lookup table: the conversation id carries the PO number
    # because this app assigned it on the opening send.
    po_number = orders.po_number_for(email.conversation_id)
    po = orders.get(po_number) if po_number else None
    if po is None:
        # Mail to this address that is not about a PO we opened. Forward it
        # rather than answering — the agent has no context for it, and an agent
        # that improvises a reply to an unknown thread is worse than one that
        # stays quiet.
        await e2a.messages.forward(
            AGENT_EMAIL,
            email.id,
            {"to": [BUYER_EMAIL]},
            idempotency_key=f"{event.id}:unmatched",
        )
        return {"status": "forwarded", "reason": "no matching PO", "event_id": event.id}

    outcome: Outcome = (
        await graph.ainvoke(
            {
                "mode": "read",
                "po": po,
                "supplier_text": email.text,
                "supplier_from": email.from_,
                "verified": email.verified,
                "chases_sent": 0,
                "today": datetime.now(timezone.utc).date(),
            }
        )
    )["outcome"]

    # Reply to the message that triggered this webhook, not to the newest one in
    # the thread — if two supplier emails arrive close together, replying to
    # "newest" would answer one of them twice and the other never.
    send = await _apply(po, outcome, latest_id=email.id, idempotency_key=event.id)

    reading = outcome.reading
    return {
        "status": outcome.action,
        "event_id": event.id,
        "po_number": po.po_number,
        "conversation_id": email.conversation_id,
        "stage": outcome.stage,
        "committed_quantity": reading.quantity if reading else None,
        "committed_ship_date": str(reading.ship_date) if reading and reading.ship_date else None,
        "concerns": outcome.assessment.concerns if outcome.assessment else [],
        "manipulation_attempt": reading.manipulation_attempt if reading else False,
        # A queued-for-approval status means the supplier has NOT been written
        # to yet — an outbound review is holding the message for a human.
        "send_status": send.status,
    }
