import { Mastra } from '@mastra/core/mastra';
import { LibSQLStore } from '@mastra/libsql';

import { inboxAgent } from './agents/inbox-agent';
import { e2aRoutes } from './e2a/routes';

export const mastra = new Mastra({
  agents: { inboxAgent },
  storage: new LibSQLStore({ id: 'mastra', url: 'file:./mastra.db' }),
  server: {
    // POST /webhooks/e2a — point your e2a webhook endpoint here.
    apiRoutes: e2aRoutes,
  },
  bundler: {
    externals: ['supports-color'],
  },
});
