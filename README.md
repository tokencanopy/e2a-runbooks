# e2a runbooks

Deployable reference agents that use [e2a](https://e2a.dev) — one per framework. Each runbook is a complete project you can clone, configure with four environment variables, and run.

e2a gives an AI agent a **real email address**: verified inbound mail over signed webhooks, replies that stay in-thread, and a human approval gate before the agent sends anything.

## Runbooks

| Runbook | Framework | What it shows |
| --- | --- | --- |
| [`mastra/`](./mastra) | [Mastra](https://mastra.ai) | An agent that owns an inbox — signed webhook → verified inbound → in-thread reply, with SPF/DKIM/DMARC provenance passed to the model |

More frameworks to come. Each lives in its own directory with its own `package.json` and pinned framework version, so one framework's churn never breaks another.

## Using one

```bash
git clone https://github.com/tokencanopy/e2a-runbooks
cd e2a-runbooks/mastra
npm install
cp .env.example .env    # fill in four values
npm run dev
```

Each runbook's README carries its own quickstart, configuration table, and deployment notes.

## What these are for

Reading an API reference tells you which calls exist. It doesn't tell you the things that actually break an email agent in production, which is what these encode:

- **Verify the signature on raw bytes.** Parse first and re-serialize and the HMAC will not match.
- **Only inbound mail should wake the agent.** e2a emits the full lifecycle; without a guard, your own delivery receipt triggers a reply, which produces another receipt.
- **Webhook delivery is at-least-once.** Claim the event id *before* running the agent — the failure being prevented is a second reply in someone's inbox.
- **Inbound email is untrusted input.** Pass the authentication verdict to the model and instruct it to treat message bodies as data, not instructions. An agent with an inbox and no provenance is a prompt-injection surface.
- **Gate sending in infrastructure, not in a prompt.** e2a can hold outbound mail for human approval, so "wait for a human" is not something the model can be talked out of.

## Conventions

Each runbook:

- is a standalone project — its own `package.json`, its own lockfile, its own pinned framework version;
- reads all configuration from the environment, with a documented `.env.example`;
- separates transport from logic, so the webhook handler is testable without a server, an API key, or a live inbox;
- ships tests for the paths worth locking — signature rejection, event filtering, and duplicate suppression;
- uses only synthetic addresses (`example.com`, `agents.localhost`, `.example`). No real inboxes, customers, or keys.

## Contributing

Adding a runbook is a directory, not a repo. Follow the conventions above, make `npm run typecheck`, `npm test`, and the framework's own build pass, and add a row to the table.

## License

Apache-2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
