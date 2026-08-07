# e2a + Mastra — an agent with its own inbox

A deployable [Mastra](https://mastra.ai) agent that owns a real email address, powered by [e2a](https://e2a.dev).

People email the agent directly. Inbound mail arrives as a **signature-verified webhook**, the agent replies **in-thread**, and SPF/DKIM/DMARC results are passed to the model so unauthenticated senders are treated as untrusted.

```
someone@example.com  ──email──▶  e2a  ──webhook (signed)──▶  Mastra agent
                     ◀──reply in-thread──────────────────────┘
```

## Why this exists

Mastra agents can call tools, but they have no address — nobody can email one, and nothing it produces has a conversation to live in. `@mastra/core` ships human-in-the-loop tool approvals (`approveToolCall` / `declineToolCall`), but the framework deliberately leaves *notification delivery* to the developer: it can pause an agent awaiting a human's decision and has no way to reach the human. Email is the channel every human already has.

This template gives the agent a durable identity, an authenticated inbound channel, and an approval boundary in front of anything it sends.

## Quickstart

```bash
git clone https://github.com/tokencanopy/e2a-mastra
cd e2a-mastra
pnpm install
cp .env.example .env   # fill in the four values
pnpm dev
```

`pnpm dev` starts Mastra locally, including the webhook route at `POST /webhooks/e2a`.

### Point e2a at it

The webhook has to reach your machine, so expose the port during development:

```bash
# any tunnel works
ngrok http 4111
```

Then create the webhook in e2a with the tunnel URL plus `/webhooks/e2a`, subscribing to `email.received`. Copy the signing secret (`whsec_…`) into `E2A_WEBHOOK_SECRET`.

Send the agent an email. It replies in-thread.

## Configuration

| Variable | Required | What it is |
| --- | --- | --- |
| `E2A_API_KEY` | yes | Account (`e2a_acct_`) or agent (`e2a_agt_`) key |
| `E2A_AGENT_EMAIL` | yes | The inbox this agent owns and sends as |
| `E2A_WEBHOOK_SECRET` | yes | Signing secret for the webhook (`whsec_…`) |
| `MODEL` | no | Mastra model-router id. Defaults to `google/gemini-3.6-flash` (the model this was tested against) |
| provider key | yes | The key for whichever model you chose — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, … |
| `E2A_API_URL` | no | Defaults to `https://api.e2a.dev`; set when self-hosting |

### Swapping models

`MODEL` takes any id in Mastra's model-router format (`provider/model-name`) — 5000+ models across 160+ providers, with **no provider package to install**. Mastra reads the matching `*_API_KEY` by provider name.

```bash
MODEL=google/gemini-3.6-flash       # + GOOGLE_GENERATIVE_AI_API_KEY  (tested)
MODEL=anthropic/claude-sonnet-4-6   # + ANTHROPIC_API_KEY
MODEL=openai/gpt-5-mini             # + OPENAI_API_KEY
MODEL=xai/grok-4.3                  # + XAI_API_KEY
```

**Model ids go stale, and availability is per-account.** Mastra's docs still show `google/gemini-2.5-flash`, which now returns *"no longer available to new users"*. If a model is refused, list what your key can actually reach before assuming the template is broken:

```bash
curl -H "x-goog-api-key: $KEY" \
  'https://generativelanguage.googleapis.com/v1beta/models?pageSize=200'
```

Nothing in this template is model-specific — the tools are plain JSON schemas and the agent instructions are provider-neutral. Local models via an OpenAI-compatible endpoint (LMStudio, Ollama-style) and AI SDK provider modules also work; see Mastra's models docs.

## What's in here

```
src/mastra/
├── index.ts                 Mastra instance; registers the webhook route
├── agents/inbox-agent.ts    the agent, its instructions, and its tools
└── e2a/
    ├── client.ts            E2AClient + env configuration
    ├── tools.ts             send-email, reply-to-email, list-messages
    └── routes.ts            POST /webhooks/e2a — verify, dedupe, run the agent
```

### Three things worth reading before you extend it

**Signature verification happens on raw bytes.** `constructEvent(rawBody, signature, secret)` verifies and parses in one step. It must see the exact bytes e2a sent — parse first and re-serialize and verification will fail. Unverified payloads are rejected with a 401 and never reach the agent.

**Inbound mail is untrusted input.** The agent's instructions state that message bodies are data, not instructions, and that it must refuse redirection, credential requests, and exfiltration attempts. When `email.verified` is false, the prompt says so explicitly and the agent replies only to say it could not verify the sender. This is the difference between an agent with an inbox and an agent with a prompt-injection surface.

**Webhook delivery is at-least-once.** Replying twice to one email is the visible failure, so events are claimed by id before the agent runs. The dedupe set in `routes.ts` is in-memory and is the one thing to replace before running more than one instance — move it to the Mastra store or any shared table keyed on the event id.

## Outbound approval

e2a can hold an agent's outbound mail for human approval per agent (outbound protection). With it enabled, `send-email` returns a queued review instead of delivering, and the tools surface that to the model so the agent says "queued for approval" rather than claiming it sent something. Approve or reject from the e2a dashboard, or via `reviews.approve()` / `reviews.reject()`.

Wiring this to Mastra's own `approveToolCall` — so a human approves a *tool call* by replying to an email — is the natural next step and is not implemented here yet. Mastra's approval surface has several open correctness issues at the time of writing, so this template stays on e2a's own review gate, which is stable.

## Deploying

`pnpm build` produces a standard Mastra build, deployable anywhere Mastra runs. Whatever you choose needs a stable public HTTPS URL for the webhook — update the endpoint in e2a to the deployed URL and keep the same signing secret.

## License

Apache-2.0.

---

Not affiliated with Mastra. Built and maintained by the [e2a](https://e2a.dev) team, following Mastra's own recommendation that third-party integrations live in their maintainers' repositories.
