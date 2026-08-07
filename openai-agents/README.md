# OpenAI Agents SDK + e2a — a receptionist agent

A **receptionist** built with the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/), with its own email address via [e2a](https://e2a.dev).

It answers what it can, **forwards what it can't to the right desk**, and labels everything on the way through. One file.

Handoffs are a first-class idea in this SDK, which is why the receptionist lives here — routing to a human is the same shape as routing to another agent.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in four values
uvicorn app:app --reload --port 8000
```

Expose the port and point an e2a webhook at it, subscribed to `email.received`:

```bash
ngrok http 8000               # any tunnel works
# then create the webhook with <tunnel-url>/webhooks/e2a
```

Copy the signing secret (`whsec_…`) into `E2A_WEBHOOK_SECRET`, email the agent, and it replies in-thread.

## Configuration

| Variable | Required | What it is |
| --- | --- | --- |
| `E2A_API_KEY` | yes | Account (`e2a_acct_`) or agent (`e2a_agt_`) key |
| `E2A_AGENT_EMAIL` | yes | The inbox this agent owns and sends as |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `OPENAI_API_KEY` | yes | Model access |
| `MODEL` | no | Defaults to `gpt-5-mini` |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

## How it works

```
someone@example.com ──email──▶ e2a ──signed webhook──▶ POST /webhooks/e2a
                                                          construct_event   verify raw bytes
                                                          event.type check  only email.received
                                                          from_event        hydrate InboundEmail
                                                          Runner.run_sync   agent decides
                                                          label_message     update_labels
                                                          forward_to_desk   email.forward (if human needed)
                                                          email.reply       in-thread
```

Four things in `app.py` are load-bearing, and they are the same in every runbook here:

**Verification runs on raw bytes.** `construct_event(raw, signature, secret)` verifies the HMAC and replay window, then parses. Read the body with `await request.body()` — parse first and re-serialize and the signature will not match. Unverified payloads return 401 and never reach the agent.

**Only `email.received` wakes the agent.** e2a emits the full lifecycle (`sent`, `delivered`, `bounced`, `complained`). Without the guard, the agent's own delivery receipt triggers a reply, which produces another receipt.

**The authentication verdict goes into the prompt.** `email.verified` carries the SPF/DKIM/DMARC result. When it is false, the prompt says so and instructs the agent not to act on the contents. An agent with an inbox and no provenance is a prompt-injection surface.

**`email.reply()` keeps the thread.** e2a sets `In-Reply-To`/`References`. A fresh `send` with a matching subject starts a parallel thread instead.

## The desk allowlist is the security boundary

`forward_to_desk` takes a **desk name, not an address**, and resolves it against `DESKS`. A model that has been talked into forwarding your inbox to `attacker@evil.example` cannot do it — there is no argument that expresses it. Prompt instructions alone would not be enough; this is enforced in code, and it has a test:

```python
_forward_to_desk("attacker@evil.example", "n")  # -> "unknown desk ..." , no API call
```

The tools also act on **the message currently being handled**, not on a message id supplied by the model, so a confused agent cannot forward some *other* message out of the inbox.

## Simplifications worth knowing

This runbook is deliberately minimal. Two shortcuts to fix before production:

- **Duplicate suppression is an in-memory `set`.** Webhook delivery is at-least-once, so events are claimed by id before the agent runs — but that state dies with the process and is not shared across instances. Back it with a unique insert on the event id.
- **No outbound approval.** e2a can hold an agent's outbound mail for human review per agent; this runbook sends directly. See the [`mastra/`](../mastra) runbook, which surfaces the queued-for-approval status to the model.
- **`DESKS` is hardcoded.** Edit it for your organisation. The addresses shipped here are non-routable `.example` ones.

The `mastra/` runbook is the fully-worked reference — it has the same core with tests, structured tool errors, and the approval path.

## Versions

Pinned to the latest published releases as of **2026-08-07**, and every symbol used here was verified against the installed packages:

| Package | Version |
| --- | --- |
| `openai-agents` | 0.19.4 |
| `e2a` | 5.6.0 |
| `fastapi` | 0.141.1 |
| `uvicorn` | 0.52.1 |
