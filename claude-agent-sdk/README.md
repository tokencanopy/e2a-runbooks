# Claude Agent SDK + e2a — an SRE triage agent

An SRE triage agent built with the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk), with its own email address via [e2a](https://e2a.dev).

Monitoring systems email alerts to the agent. It triages them and drafts a recommendation for the on-call engineer. **It never touches infrastructure.**

That last sentence is the point of this runbook — and it is enforced twice, in code and in infrastructure, not in a prompt.

## Why an SRE agent, and why it's safe

Alerts already arrive by email — Grafana, PagerDuty, and CloudWatch all send them. So an email-native agent is the natural shape for triage. But it is also the most dangerous shape available: **email is trivially spoofable**, so "an email triggers automated action against production" is a vulnerability, not a feature.

This runbook exists to show the two properties that make it safe:

**1. The agent has no tools.** `allowed_tools=[]` with `permission_mode="dontAsk"` — nothing is pre-approved, so nothing can run. An alert body that says *"restart the payments service"* has nothing to call. The prompt also tells it to refuse and flag the attempt, but the prompt is the second line of defence, not the first.

**2. It cannot approve its own output.** The triage note is emailed to on-call through e2a's outbound review hold, and **reviews are account-scoped**. Configure this runbook with an *agent*-scoped key (`e2a_agt_`) and it is structurally incapable of approving the send — that requires an account key. e2a emails the on-call human, who approves.

```
Grafana ──alert──▶ e2a ──signed webhook──▶ agent
                                            │  no tools; triage note only
                                            ▼
                            send to on-call ──▶ HELD for review
                                                     │
                              e2a emails the human ──▶ approves ──▶ delivered
```

Separation of duties, enforced by key scope rather than by instructions.

## The sender gate

Two conditions, both required, in `triage_gate()`:

```python
if sender not in ALERT_SOURCES:  return "not a configured alert source"
if not email.verified:           return "failed SPF/DKIM/DMARC and may be spoofed"
```

The allowlist says **who may page us**. `verified` says **the mail actually came from them** — without it, anyone can put `alerts@grafana.example` in a `From:` header and the allowlist waves them through. Mail that fails either check never reaches the agent; the agent is not invoked at all, and the sender gets an in-thread reply saying so.

This is the concrete argument for authenticated inbound: an alert-driven agent without provenance is an open trigger.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill it in
uvicorn app:app --reload --port 8000
```

**Prerequisite:** the Claude Agent SDK drives the Claude Code CLI as a subprocess (it raises `CLINotFoundError` when it's missing), so `claude` must be installed and on `PATH`.

Expose the port, point an e2a webhook at `<tunnel-url>/webhooks/e2a` subscribed to `email.received`, and copy the signing secret into `E2A_WEBHOOK_SECRET`. Then point one monitoring alert at the agent's address — or send a test alert yourself from an address you add to `ALERT_SOURCES`.

## Configuration

| Variable | Required | What it is |
| --- | --- | --- |
| `E2A_API_KEY` | yes | **Use an agent key (`e2a_agt_`)** — see the safety note above |
| `E2A_AGENT_EMAIL` | yes | The inbox alerts are sent to |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `ONCALL_EMAIL` | no | Where approved triage notes go |
| `ANTHROPIC_API_KEY` | yes | Model access |
| `MODEL` | no | Defaults to `claude-opus-5` |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

`ALERT_SOURCES` is a set at the top of `app.py` — edit it for your monitoring stack. The addresses shipped here are non-routable `.example` ones.

## Reading the response

`POST /webhooks/e2a` returns one of:

| `status` | Meaning |
| --- | --- |
| `refused` | Sender wasn't an allowlisted, authenticated alert source. **The agent was not run.** |
| `triaged` | Triage note produced. Check `notification_status` — a queued-for-approval value means **on-call has not been notified yet**. |
| `ignored` | Not an `email.received` event (delivery receipt, bounce, …) |
| `duplicate` | This event id was already handled |

## Simplifications worth knowing

- **Duplicate suppression is an in-memory `set`.** Paging on-call twice for one alert is the failure this prevents, so replace it with a unique insert on the event id before running more than one instance.
- **`ALERT_SOURCES` is hardcoded** rather than loaded from config.
- **No escalation path.** A real deployment would page a second human if the review isn't approved within some window; the `email.review_requested` webhook is the hook for that.

The [`mastra/`](../mastra) runbook is the fully-worked reference — same core with tests, structured tool errors, and the approval path.

## Versions

Pinned to the latest published releases as of **2026-08-07**, with every symbol verified against the installed packages:

| Package | Version |
| --- | --- |
| `claude-agent-sdk` | 0.2.132 |
| `e2a` | 5.6.0 |
| `fastapi` | 0.141.1 |
| `uvicorn` | 0.52.1 |
