# OpenAI Agents SDK + e2a — an ecommerce support agent

An ecommerce support agent built with the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/), with its own authenticated email address via [e2a](https://e2a.dev).

Customers can ask about an order, delivery, return, refund, cancellation, or address change. The agent can answer status questions from a synthetic order store; actions that change money or fulfillment are constrained human-review requests.

> **An example, not a product.** This is one of the [e2a runbooks](../README.md) — a small demonstration of what you can build with e2a. It uses synthetic order records and deliberately leaves out a real commerce integration. See [*Simplifications worth knowing*](#simplifications-worth-knowing) before adapting it.

This is the ecommerce companion to the [receptionist runbook](../openai-agents/). It uses the same framework but a different workflow: the receptionist demonstrates handoffs between desks; this example demonstrates safe, order-specific customer support.

## What it demonstrates

```text
customer@example.com ──email──▶ e2a ──signed webhook──▶ ecommerce agent
       ▲                                                   │
       └──────────── threaded status reply ◀───────────────┘
                                                           │
                         refund/cancel/address change ──▶ human review
```

- Verified inbound email before any order lookup or escalation
- Threaded replies using `email.reply()`
- A constrained, read-only `lookup_order` tool
- Human review for refunds, cancellations, returns, and address changes
- Idempotent review notification and reply sends
- No payment data, credentials, arbitrary recipients, or model-selected URLs

## Quickstart

Python **3.10+** is required by the OpenAI Agents SDK.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in the values
uvicorn app:app --reload --port 8000
```

Expose the port and point an e2a webhook at `/webhooks/e2a`, subscribed to `email.received`:

```bash
ngrok http 8000               # any tunnel works
# then create the webhook with <tunnel-url>/webhooks/e2a
```

Copy the signing secret (`whsec_…`) into `E2A_WEBHOOK_SECRET`, then send a test email containing one of these synthetic order references:

```text
Hi, where is order E2A-1001?
```

The example records are `E2A-1001`, `E2A-1002`, and `E2A-1003`. They are hardcoded in `app.py` so the runbook works without a commerce account or customer data.

## Configuration

| Variable | Required | What it is |
| --- | --- | --- |
| `E2A_API_KEY` | yes | Agent-scoped API key for this inbox |
| `E2A_AGENT_EMAIL` | yes | The support inbox this agent owns |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `HANDOFF_EMAIL` | yes | Human operations address for review requests |
| `OPENAI_API_KEY` | yes | Model access |
| `MODEL` | no | Defaults to `gpt-5-mini` |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

## The safety boundary

`lookup_order` is deliberately read-only. It returns only records from the local synthetic map, and an unknown order cannot be escalated.

`request_human_review` accepts only four action names: `return`, `refund`, `cancellation`, and `address_change`. It sends a notification to the configured operations address, but it never calls a payment, fulfillment, or customer-data system. The customer reply must say that review was requested, not that the action completed.

The inbound handler rejects missing or invalid webhook signatures and refuses to invoke the agent for a sender that fails SPF/DKIM/DMARC. Email content is always placed in an untrusted-data section of the prompt. These are code and infrastructure boundaries; they do not depend on the model following an instruction.

## Reading the response

| `status` | Meaning |
| --- | --- |
| `replied` | Authenticated message was handled and a threaded reply was accepted or queued |
| `refused` | Sender authentication failed; no order lookup or escalation occurred |
| `ignored` | The webhook was for a lifecycle event other than `email.received` |
| `duplicate` | This event id was already claimed |

Always inspect `send_status`. `pending_review` means e2a accepted the reply or operations notification but has not delivered it; do not retry blindly.

## Simplifications worth knowing

- **The order store is hardcoded.** Replace `ORDERS` with a read-only, authenticated commerce API and validate authorization for the requesting customer before exposing order details.
- **No customer identity verification.** A production integration needs an account or order-verification step before disclosing status or accepting a request.
- **Human review is notification, not workflow completion.** A production system needs a private operations workflow that records the decision and performs the action.
- **Duplicate suppression is an in-memory `set`.** Replace it with a durable unique insert on the event id before running more than one instance.
- **No attachment or image handling.** Product photos, invoices, and shipping labels need separate content-type and size limits.

The [`mastra/`](../mastra) runbook is the fully-worked reference with transport tests and the outbound approval path.

## Versions

Pinned to the latest versions available from the package index used to verify this runbook, with every symbol verified against the installed packages:

| Package | Version |
| --- | --- |
| `openai-agents` | 0.8.4 |
| `e2a` | 5.6.0 |
| `fastapi` | 0.128.8 |
| `uvicorn` | 0.39.0 |
