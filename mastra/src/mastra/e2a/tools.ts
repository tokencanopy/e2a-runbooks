import { createTool } from '@mastra/core/tools';
import { z } from 'zod';

import { agentEmail, e2a } from './client';

/**
 * NOTE ON NAMING: the model sees the *object keys* these tools are registered
 * under (`sendEmail`, `replyToEmail`, `listMessages`), not the `id` strings. Keep
 * the agent instructions using the camelCase names or the model will be told
 * about a tool it cannot see.
 *
 * NOTE ON ERRORS: a tool that throws surfaces as a framework-level failure
 * ("ToolInvocation must have a result") rather than something the agent can
 * recover from, which turns a bad API key into a dead request. Each tool below
 * catches and returns a structured error instead, so the agent can tell the
 * human what failed.
 */

/** Shape shared by the two sending tools. */
const sendResultSchema = z.object({
  status: z
    .string()
    .describe(
      'Status from e2a, or "error". A queued-for-approval status means it has NOT been delivered yet — say so.',
    ),
  messageId: z.string().optional(),
  approvalExpiresAt: z.string().optional(),
  error: z.string().optional().describe('Present when the send failed. Report it; do not retry blindly.'),
});

function failed(error: unknown): { status: 'error'; error: string } {
  return { status: 'error', error: error instanceof Error ? error.message : String(error) };
}

/**
 * Sending is a real outbound action, so it is the one worth gating. e2a can hold
 * outbound mail for human approval per agent; when it does, this returns a
 * pending review rather than a delivery, and the status says so.
 */
export const sendEmail = createTool({
  id: 'send-email',
  description:
    "Send a NEW email from this agent's own inbox — use only to start a new thread, never to respond. " +
    'If outbound approval is enabled, the message is queued for a human rather than sent.',
  inputSchema: z.object({
    to: z.array(z.string()).min(1).describe('Recipient email addresses.'),
    subject: z.string().describe('Subject line.'),
    text: z.string().describe('Plain-text body.'),
  }),
  outputSchema: sendResultSchema,
  execute: async ({ to, subject, text }) => {
    try {
      const result = await e2a().messages.send(agentEmail(), { to, subject, text });
      return {
        status: result.status,
        messageId: result.messageId,
        approvalExpiresAt: result.approvalExpiresAt?.toISOString(),
      };
    } catch (error) {
      return failed(error);
    }
  },
});

/**
 * Replying is separate from sending because threading matters: e2a keeps the
 * conversation intact when you reply to a message id, and loses it if you send a
 * fresh message with a matching subject.
 */
export const replyToEmail = createTool({
  id: 'reply-to-email',
  description:
    'Reply in-thread to a message this agent received. Prefer this over sendEmail whenever ' +
    'responding to something, so the conversation stays threaded.',
  inputSchema: z.object({
    messageId: z.string().describe('The id of the message being replied to.'),
    text: z.string().describe('Plain-text reply body.'),
  }),
  outputSchema: sendResultSchema,
  execute: async ({ messageId, text }) => {
    try {
      const result = await e2a().messages.reply(agentEmail(), messageId, { text });
      return { status: result.status, messageId: result.messageId };
    } catch (error) {
      return failed(error);
    }
  },
});

/** Read recent mail — useful when the agent needs prior context in a thread. */
export const listMessages = createTool({
  id: 'list-messages',
  description: "List recent messages in this agent's inbox, newest first.",
  inputSchema: z.object({
    limit: z.number().int().min(1).max(50).default(10),
  }),
  outputSchema: z.object({
    messages: z.array(
      z.object({
        id: z.string(),
        from: z.string().nullable().describe('Header From address.'),
        subject: z.string(),
        direction: z.string().nullable().describe('inbound or outbound.'),
        conversationId: z.string().nullable(),
      }),
    ),
    error: z.string().optional(),
  }),
  execute: async ({ limit }) => {
    const messages: {
      id: string;
      from: string | null;
      subject: string;
      direction: string | null;
      conversationId: string | null;
    }[] = [];
    try {
      // `messages.list` returns an AutoPager, which is async-iterable and keeps
      // fetching pages — so break once we have what was asked for.
      const pager = e2a().messages.list(agentEmail(), { limit });
      for await (const m of pager) {
        messages.push({
          id: m.id,
          from: m.headerFrom ?? null,
          subject: m.subject ?? '',
          direction: m.direction ?? null,
          conversationId: m.conversationId ?? null,
        });
        if (messages.length >= limit) break;
      }
      return { messages };
    } catch (error) {
      return { messages, ...failed(error) };
    }
  },
});

export const e2aTools = { sendEmail, replyToEmail, listMessages };
