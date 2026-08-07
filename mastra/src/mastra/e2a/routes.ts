import { registerApiRoute } from '@mastra/core/server';

import { agentEmail, e2a, webhookSecret } from './client';
import { createDeduper } from './dedupe';
import { handleInboundWebhook, type HandlerDeps } from './handler';
import { inboxAgent } from '../agents/inbox-agent';

/**
 * Thin HTTP adapter. All the logic lives in `handler.ts` so it can be tested
 * without a server or credentials; this file only reads the request and maps the
 * result onto a status code.
 */
const deduper = createDeduper();

function deps(): HandlerDeps {
  return {
    secret: webhookSecret(),
    agentEmail: agentEmail(),
    deduper,
    // `e2a()` is resolved at call time, not here: an unverified request must be
    // rejected on its signature alone and never turn a missing API key into a
    // 500 on a path that should have been a 401.
    toInboundEmail: event => e2a().inbound.fromEvent(event),
    runAgent: async prompt => {
      const result = await inboxAgent.generate(prompt);
      return { text: result.text };
    },
  };
}

export const e2aRoutes = [
  registerApiRoute('/webhooks/e2a', {
    method: 'POST',
    handler: async c => {
      const rawBody = await c.req.text();
      const signature = c.req.header('x-e2a-signature');

      const result = await handleInboundWebhook(deps(), rawBody, signature);

      switch (result.kind) {
        case 'unverified':
          // Untrusted payload — never reached the agent.
          return c.json({ error: 'signature verification failed' }, 401);
        case 'ignored':
          return c.json({ status: 'ignored', type: result.type });
        case 'duplicate':
          return c.json({ status: 'duplicate', eventId: result.eventId });
        case 'handled':
          return c.json({
            status: 'handled',
            eventId: result.eventId,
            messageId: result.messageId,
            conversationId: result.conversationId,
            verified: result.verified,
            text: result.text,
          });
      }
    },
  }),
];
