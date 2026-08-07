import { E2AClient } from '@e2a/sdk/v1';

/**
 * Configuration is read from the environment so the template deploys without
 * code edits. See `.env.example`.
 *
 * - E2A_API_KEY        account (`e2a_acct_`) or agent (`e2a_agt_`) key
 * - E2A_AGENT_EMAIL    the inbox this agent owns, e.g. support@your-domain.example
 * - E2A_WEBHOOK_SECRET signing secret for inbound webhooks (`whsec_...`)
 * - E2A_API_URL        optional; defaults to https://api.e2a.dev (set for self-host)
 */
function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing ${name}. Copy .env.example to .env and fill it in — see the README quickstart.`,
    );
  }
  return value;
}

/** The inbox this agent sends and receives as. */
export const agentEmail = (): string => required('E2A_AGENT_EMAIL');

/** Signing secret used to verify inbound webhook deliveries. */
export const webhookSecret = (): string => required('E2A_WEBHOOK_SECRET');

/**
 * `E2AClient` reads `E2A_API_KEY` and `E2A_API_URL` from the environment on its
 * own, so we only pass an explicit key to fail fast with a clear message.
 */
let client: E2AClient | undefined;

export function e2a(): E2AClient {
  if (!client) {
    client = new E2AClient({ apiKey: required('E2A_API_KEY') });
  }
  return client;
}
