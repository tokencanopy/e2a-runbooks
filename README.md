# e2a runbooks

Deployable reference agents that use [e2a](https://e2a.dev) — one per framework. Each runbook is a complete project you can clone, configure with four environment variables, and run.

e2a gives an AI agent a **real email address**: verified inbound mail over signed webhooks, replies that stay in-thread, and a human approval gate before the agent sends anything.

## Runbooks

| Runbook | Agent | Framework | e2a surface it exercises |
| --- | --- | --- | --- |
| [`mastra/`](./mastra) | **Support agent** — answers in-thread, with an approval gate before it sends | [Mastra](https://mastra.ai) — built-in server, memory, HITL primitives | Threading, memory, `reviews` |
| [`openai-agents/`](./openai-agents) | **Receptionist** — answers what it can, forwards the rest to the right desk | [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) — handoffs are first-class here | `forward`, `update_labels` |
| [`claude-agent-sdk/`](./claude-agent-sdk) | **AI SRE** — triages monitoring alerts, recommends, never acts | [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) — the ops/coding agent SDK | Verification-as-gate, `reviews` (account-scoped) |
| [`langchain/`](./langchain) | **Contract review** — reads an emailed PDF, replies with a structured risk summary | [LangChain v1](https://docs.langchain.com) — document work is its origin | `attachments`, `get_attachment` |
| [`crewai/`](./crewai) | **Escalation desk** — a crew triages, investigates, and answers *from the specialist's own inbox* | [CrewAI](https://docs.crewai.com) — multi-agent crews, so multiple identities make sense | Multiple agents, cross-identity `conversation_id` |
| [`pydantic-ai/`](./pydantic-ai) | **Scheduling secretary** — negotiates a meeting time over many round-trips, storing nothing | [Pydantic AI](https://ai.pydantic.dev) — typed outputs, and no session store, so the thread really is the state | `conversations`, `messages.get` |

The `mastra/` runbook is the fully-worked reference — same core, plus tests, structured tool errors, and the outbound approval path. The others are deliberately minimal.

Each pairs a use case with the framework that suits it, and each exercises an e2a surface the others don't. Each lives in its own directory with its own dependency manifest and pinned SDK versions, so one framework's churn never breaks another.

## Using one

```bash
git clone https://github.com/tokencanopy/e2a-runbooks

# TypeScript runbooks
cd e2a-runbooks/mastra && npm install && cp .env.example .env && npm run dev

# Python runbooks
cd e2a-runbooks/openai-agents
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
uvicorn app:app --port 8000
```

Each runbook's README carries its own quickstart, configuration table, and deployment notes. One caveat worth knowing up front: **`crewai/` needs Python `<3.14`** — CrewAI 1.x declares that upper bound and will not install on 3.14. The other Python runbooks have no upper bound.

## What these are for

Reading an API reference tells you which calls exist. It doesn't tell you the things that actually break an email agent in production, which is what these encode:

- **Verify the signature on raw bytes.** Parse first and re-serialize and the HMAC will not match.
- **Only inbound mail should wake the agent.** e2a emits the full lifecycle; without a guard, your own delivery receipt triggers a reply, which produces another receipt.
- **Webhook delivery is at-least-once.** Claim the event id *before* running the agent — the failure being prevented is a second reply in someone's inbox.
- **Inbound email is untrusted input.** Pass the authentication verdict to the model and instruct it to treat message bodies as data, not instructions. An agent with an inbox and no provenance is a prompt-injection surface.
- **Gate sending in infrastructure, not in a prompt.** e2a can hold outbound mail for human approval, so "wait for a human" is not something the model can be talked out of.
- **A conversation gives you the skeleton, not the content.** `conversations.get()` returns message summaries without body text; rebuilding what was actually said costs one fetch per message. An agent that needs history has to pay for it — see [`pydantic-ai/`](./pydantic-ai).

## Conventions

Each runbook:

- is a standalone project — its own dependency manifest, its own pinned SDK versions, verified against the latest published releases;
- reads all configuration from the environment, with a documented `.env.example`;
- verifies the webhook signature on raw bytes, filters to `email.received`, and replies in-thread;
- passes the SPF/DKIM/DMARC verdict into the prompt and instructs the agent to treat message bodies as data;
- uses only synthetic addresses (`example.com`, `agents.localhost`, `.example`). No real inboxes, customers, or keys.

The reference runbook (`mastra/`) goes further: transport separated from logic so the handler is testable without a server or credentials, tests covering signature rejection / event filtering / duplicate suppression, structured tool errors, and the outbound approval path. The minimal runbooks stay one file and say what they simplified.

## Contributing

Adding a runbook is a directory, not a repo. Follow the conventions above, verify every SDK symbol against the installed package rather than from memory, pin to the latest published version, and add a row to the table.

## License

Apache-2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
