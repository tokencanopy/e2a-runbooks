# e2a runbooks

**Example agents that show what you can build with [e2a](https://e2a.dev)** — one per framework. Each is a small, complete, runnable project: clone it, fill in a `.env`, point a webhook at it, and email it.

e2a gives an AI agent a **real email address**: verified inbound mail over signed webhooks, replies that stay in-thread, and a human approval gate before the agent sends anything. These seven demonstrate seven different things you can do with that.

> **These are examples, not products.** Each one is deliberately small enough to read in a sitting, and each README ends with a *Simplifications worth knowing* section listing exactly what it leaves out — in-memory deduplication, hardcoded routing tables, no OCR, no calendar. Copy from them; don't deploy them as-is.

## Runbooks

| Runbook | Agent | Framework | e2a surface it exercises |
| --- | --- | --- | --- |
| [`mastra/`](./mastra) | **Support agent** — answers in-thread, with an approval gate before it sends | [Mastra](https://mastra.ai) — built-in server, memory, HITL primitives | Threading, memory, `reviews` |
| [`openai-agents/`](./openai-agents) | **Receptionist** — answers what it can, forwards the rest to the right desk | [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) — handoffs are first-class here | `forward`, `update_labels` |
| [`claude-agent-sdk/`](./claude-agent-sdk) | **AI SRE** — triages monitoring alerts, recommends, never acts | [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) — the ops/coding agent SDK | Verification-as-gate, `reviews` (account-scoped) |
| [`langchain/`](./langchain) | **Contract review** — reads an emailed PDF, replies with a structured risk summary | [LangChain v1](https://docs.langchain.com) — document work is its origin | `attachments`, `get_attachment` |
| [`crewai/`](./crewai) | **Escalation desk** — a crew triages, investigates, and answers *from the specialist's own inbox* | [CrewAI](https://docs.crewai.com) — multi-agent crews, so multiple identities make sense | Multiple agents, cross-identity `conversation_id` |
| [`pydantic-ai/`](./pydantic-ai) | **Scheduling secretary** — negotiates a meeting time over many round-trips, storing nothing | [Pydantic AI](https://ai.pydantic.dev) — typed outputs, and no session store, so the thread really is the state | `conversations`, `messages.get` |
| [`langgraph/`](./langgraph) | **Supplier follow-up desk** — *starts* the conversation: chases open purchase orders, reads the replies, escalates slips | [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) — a declared state machine, which is what a multi-day chase is | `contacts.outreach` (the mailbox as the queue) |

## Find an example by use case

Start with the job you want the agent to do, then choose the framework whose shape fits it:

| What you want to build | Start here | What the example demonstrates |
| --- | --- | --- |
| Support or escalation agent | [`mastra/`](./mastra) or [`crewai/`](./crewai) | Threaded support replies, human approval, specialist routing, and multiple agent identities |
| AI receptionist | [`openai-agents/`](./openai-agents) | Handoffs from a front desk to the right human desk |
| Scheduling agent | [`pydantic-ai/`](./pydantic-ai) | Multi-round-trip scheduling where the email conversation is the state |
| Ecommerce or order-support agent | — | Not yet represented; this is the next high-value runbook to add |
| Procurement agent | [`langgraph/`](./langgraph) | Outbound supplier follow-up, state transitions, and escalation |
| Contract or document-review agent | [`langchain/`](./langchain) | Authenticated attachments, PDF extraction, and structured results |
| SRE or alert-triage agent | [`claude-agent-sdk/`](./claude-agent-sdk) | Sender verification, least privilege, and mandatory human approval |

This table is deliberately use-case-first: it is also the index to use when linking from a tutorial, framework example, directory listing, or AI answer. If you are looking for an ecommerce example, open an issue or contribute one following the conventions below.

## Distribution-ready links

Use the smallest relevant link when sharing the project:

- **All examples:** <https://github.com/tokencanopy/e2a-runbooks>
- **Support:** <https://github.com/tokencanopy/e2a-runbooks/tree/main/mastra>
- **Receptionist:** <https://github.com/tokencanopy/e2a-runbooks/tree/main/openai-agents>
- **Scheduling:** <https://github.com/tokencanopy/e2a-runbooks/tree/main/pydantic-ai>
- **Procurement:** <https://github.com/tokencanopy/e2a-runbooks/tree/main/langgraph>

Each runbook is intentionally a runnable starting point, not a hosted product. Link to the specific directory when demonstrating a framework integration; link to the repository root when sharing the collection.

The `mastra/` runbook is the fully-worked reference — same core, plus tests, structured tool errors, and the outbound approval path. The others are deliberately minimal.

Six of the seven wait for mail. [`langgraph/`](./langgraph) is the outbound one, and it is the only one that has to answer a question no inbound email will ever arrive to ask: *who is overdue for a follow-up, and who already answered?*

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

Reading an API reference tells you which calls exist. It doesn't tell you the things that actually break an email agent, which is what these examples encode — each was driven end-to-end against a stand-in e2a API before being published, and every lesson below is one that broke a runbook first:

- **Verify the signature on raw bytes.** Parse first and re-serialize and the HMAC will not match.
- **In an `async` handler, use the async client.** The sync `E2AClient` raises `RuntimeError` when called from inside a running event loop, so a sync client in an `async def` webhook fails on the *first* inbound email — not later, under load. The Python runbooks use `AsyncE2AClient` and await every call. The same applies to your agent framework's runner: `Runner.run_sync()` and `Crew.kickoff()` block or raise inside a loop; use `Runner.run()` and `kickoff_async()`.
- **Only inbound mail should wake the agent.** e2a emits the full lifecycle; without a guard, your own delivery receipt triggers a reply, which produces another receipt.
- **Webhook delivery is at-least-once.** Claim the event id *before* running the agent — the failure being prevented is a second reply in someone's inbox.
- **Inbound email is untrusted input.** Pass the authentication verdict to the model and instruct it to treat message bodies as data, not instructions. An agent with an inbox and no provenance is a prompt-injection surface.
- **Gate sending in infrastructure, not in a prompt.** e2a can hold outbound mail for human approval, so "wait for a human" is not something the model can be talked out of.
- **A conversation gives you the skeleton, not the content.** `conversations.get()` returns message summaries without body text; rebuilding what was actually said costs one fetch per message. An agent that needs history has to pay for it — see [`pydantic-ai/`](./pydantic-ai).
- **For outbound agents, schedule off what the server saw, not what you wrote.** An agent that initiates has to decide who is due for a follow-up, and the obvious design — send, then write `next_action_at` forward — sends twice whenever the write fails after the send. Filtering the sweep on e2a's server-maintained `last_outbound_at` closes that gap, because it moves with the message that was actually accepted. See [`langgraph/`](./langgraph).

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
