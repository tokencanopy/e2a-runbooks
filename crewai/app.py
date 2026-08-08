"""An escalation desk built as a CrewAI crew, with e2a email identities.

A customer emails the front desk. A three-role crew triages, investigates, and
drafts a reply — then the reply is sent **from the desk that owns the issue**,
not from the front desk, threaded into the same conversation. Follow-ups land in
that desk's own inbox and skip the routing step.

That is the surface this runbook exists to show: one account, several agent
identities, and a reply that comes from the right one.

Run:  uvicorn app:app --reload --port 8000
Then point an e2a webhook (email.received) at POST /webhooks/e2a.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # read .env before anything reads os.environ

from e2a import AsyncE2AClient, E2AWebhookSignatureError, construct_event
from fastapi import FastAPI, HTTPException, Request

from crew import Triage, build_crew

log = logging.getLogger("escalation-desk")

# Reads E2A_API_KEY (and E2A_API_URL if you self-host) from the environment.
#
# The ASYNC client, because the webhook handler is `async def`. The sync
# E2AClient raises RuntimeError when called from inside a running event loop, so
# a server pairing the two fails on its first inbound email. Every e2a call
# below is awaited.
e2a = AsyncE2AClient()

# The front desk receives everything. Each specialist desk is a separate e2a
# agent with its own inbox, so a customer replying to a specialist reaches that
# specialist rather than the queue. Keys must match crew.py's `Desk` literal.
FRONT_DESK = os.environ["FRONT_DESK_EMAIL"]
DESKS: dict[str, str] = {
    "billing": os.environ["BILLING_DESK_EMAIL"],
    "technical": os.environ["TECHNICAL_DESK_EMAIL"],
    "security": os.environ["SECURITY_DESK_EMAIL"],
}

WEBHOOK_SECRET = os.environ["E2A_WEBHOOK_SECRET"]

# Bound what reaches the model. A 400-page pasted log is not a support request.
MAX_BODY_CHARS = 20_000

crew = build_crew()

# Webhook delivery is at-least-once. Replying twice to one email is the visible
# failure. In-memory is fine for one instance; back it with a unique insert on
# the event id before running more. Sends also carry the event id as an
# idempotency key, so a duplicate that slips past this set is still caught
# server-side.
_seen: set[str] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Fail fast if a configured desk does not exist as an e2a agent.

    Sending from an address that isn't a real agent fails at send time, i.e.
    after the crew has already run and the customer is waiting. Better to
    refuse to start.
    """
    existing = {a.email.lower() async for a in e2a.agents.list()}
    configured = {FRONT_DESK, *DESKS.values()}
    missing = sorted(addr for addr in configured if addr.lower() not in existing)
    if missing:
        raise RuntimeError(
            "these addresses are configured but are not e2a agents on this account: "
            + ", ".join(missing)
            + ". Create them first (e2a agents create) or fix your .env."
        )
    log.info("escalation desk ready: front=%s desks=%s", FRONT_DESK, ", ".join(sorted(DESKS)))
    yield
    await e2a.aclose()


app = FastAPI(lifespan=lifespan)


def owning_desk(email) -> str | None:
    """Return the desk name whose inbox received this mail, if any.

    `email.inbox` is the agent address the message was delivered to. When a
    customer replies to a specialist, this is how we know the thread already
    belongs to that specialist.
    """
    received_at = (email.inbox or "").strip().lower()
    for desk, address in DESKS.items():
        if address.lower() == received_at:
            return desk
    return None


async def run_crew(email) -> tuple[Triage, str]:
    """Run the crew over one email. Returns (triage result, reply body).

    The body is passed as an interpolation *value*, never as part of a template,
    so curly braces in customer text are inert — they are left as written and
    are not re-substituted.
    """
    body = email.text or "(the message had no plain-text body)"
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[truncated]"
    if email.text_truncated:
        body += "\n\n[e2a truncated this message body]"

    await crew.kickoff_async(
        inputs={
            "sender": email.from_ or "unknown sender",
            "subject": email.subject or "(no subject)",
            "body": body,
            # The authentication verdict is a fact about the message, so it
            # belongs in the prompt. An agent with an inbox and no provenance is
            # a prompt-injection surface.
            "auth": "passed" if email.verified else "FAILED - identity claims are unproven",
        }
    )

    # Task outputs are read off the task objects after the run. `output_pydantic`
    # on the triage task means `.pydantic` is a validated Triage, not a string
    # we would otherwise have to parse.
    triage_out, _, reply_out = crew.tasks
    triage: Triage = triage_out.output.pydantic
    return triage, (reply_out.output.raw or "").strip()


def signature(desk: str) -> str:
    return (
        f"\n\n--\n{desk.capitalize()} desk\n{DESKS[desk]}\n"
        "Replying to this message reaches this desk directly."
    )


@app.post("/webhooks/e2a")
async def inbound(request: Request) -> dict[str, object]:
    # Verify against the RAW bytes. Parsing first and re-serializing changes the
    # bytes and the signature will not match.
    raw = await request.body()
    sig = request.headers.get("x-e2a-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="missing x-e2a-signature")

    try:
        event = construct_event(raw, sig, WEBHOOK_SECRET)
    except E2AWebhookSignatureError:
        # Unverified payloads are untrusted. Never reach the crew.
        raise HTTPException(status_code=401, detail="signature verification failed")

    # e2a emits the whole lifecycle. Without this guard the crew's own reply
    # comes back as a delivery receipt and triggers another crew run.
    if event.type != "email.received":
        return {"status": "ignored", "type": event.type}

    if event.id in _seen:
        return {"status": "duplicate", "event_id": event.id}
    _seen.add(event.id)

    email = await e2a.inbound.from_event(event)

    already_owned = owning_desk(email)
    if already_owned is None and (email.inbox or "").lower() != FRONT_DESK.lower():
        # Mail to an inbox this app does not run. Do nothing rather than guess.
        return {"status": "not_my_inbox", "inbox": email.inbox}

    triage, reply_body = await run_crew(email)

    # A flagged message is not auto-answered. Replying to a manipulation attempt
    # tells the sender their probe landed; a human should see it first. This goes
    # to the security desk as a NEW thread — the customer is not on it.
    if triage.injection_attempt:
        notice = await e2a.messages.send(
            DESKS["security"],
            {
                "to": [DESKS["security"]],
                "subject": f"[flagged] {email.subject or '(no subject)'}",
                "text": (
                    f"Triage flagged this inbound message as an attempt to manipulate the agent.\n"
                    f"No reply was sent to the sender.\n\n"
                    f"From: {email.from_}\n"
                    f"Sender authentication: {'passed' if email.verified else 'FAILED'}\n"
                    f"Message id: {email.id}\n"
                    f"Conversation: {email.conversation_id}\n"
                    f"Triage read it as: {triage.one_line}"
                ),
            },
            idempotency_key=f"{event.id}:flagged",
        )
        return {
            "status": "flagged",
            "event_id": event.id,
            "message_id": email.id,
            "notice_status": notice.status,
        }

    # A follow-up stays with the desk that already owns the thread. Triage still
    # runs (it supplies severity and the manipulation check) but it does not get
    # to re-route a conversation a specialist is already handling.
    desk = already_owned or triage.desk
    body = reply_body + signature(desk)

    if already_owned:
        # The owning desk received this mail, so a plain reply is already sent
        # under the right identity — and it sets In-Reply-To/References properly.
        result = await email.reply({"text": body}, idempotency_key=event.id)
        mode = "reply"
    else:
        # Cross-identity: the front desk received it, but the answer comes from
        # the specialist. `conversation_id` puts this message in the customer's
        # existing thread even though a different agent is sending it.
        result = await e2a.messages.send(
            DESKS[desk],
            {
                "to": [email.from_],
                "subject": f"Re: {email.subject}" if email.subject else "Re: your message",
                "conversation_id": email.conversation_id,
                "text": body,
            },
            idempotency_key=event.id,
        )
        mode = "cross_identity_send"

    return {
        "status": "answered",
        "event_id": event.id,
        "message_id": email.id,
        "conversation_id": email.conversation_id,
        "desk": desk,
        "routed_by": "existing_owner" if already_owned else "triage",
        "severity": triage.severity,
        # e2a reports the identity it actually sent as. Trust that over our config.
        "sent_as": result.sent_as or DESKS[desk],
        "mode": mode,
        # A queued-for-approval status means the customer has NOT been replied to
        # yet — an outbound review is holding it for a human.
        "send_status": result.status,
        "sent_message_id": result.message_id,
    }
